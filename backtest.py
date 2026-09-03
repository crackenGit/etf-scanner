"""
backtest.py

Historischer Backtest fuer die Dip-Score-Logik aus dip_score.py.

WICHTIG: Dieses Skript braucht Internetzugang (yfinance) sowie eure
isin.txt im selben Ordner. Es wurde in dieser Sandbox NICHT gegen echte
Daten ausgefuehrt (kein Netzwerkzugriff hier verfuegbar) - bitte lokal
laufen lassen. Falls Fehler auftreten, gerne Rueckmeldung geben, dann
fixe ich das Skript gezielt.

Ablauf:
1. ETF-Universum aus isin.txt laden (gleiches Format wie app.py).
2. Fuer jeden ETF die maximal verfuegbare Historie laden (nicht auf
   einen festen Zeitraum wie "5 Jahre" beschraenkt, da eure ETFs
   unterschiedlich alt sind - manche vielleicht nur ~1 Jahr).
3. Einmalig die Benchmark-Historie laden und deren eigenen GD200-Status
   ueber die Zeit bestimmen (fuer den Marktregime-Filter).
4. Fuer jeden Handelstag ab Tag 200 (GD200 verfuegbar) bis 21 Tage vor
   Ende der Historie (Vorschau-Fenster) den Score + die Forward-Returns
   berechnen.
5. Ergebnisse als CSV speichern + zusammenfassende Auswertung ausgeben:
   Korrelation Score<->Return, Score-Buckets, Episoden-Auswertung fuer
   die Kaufsignal-Schwelle, Einzelkomponenten-Korrelation.
6. Zusatzvalidierung auf einer Handvoll etablierter, langjaehriger
   US-Sektor-ETFs (Select Sector SPDRs, 20+ Jahre Historie), um die
   grundsaetzliche Score-Logik auch auf einer breiteren/laengeren
   Datenbasis zu pruefen als es euer eigenes, juengeres Universum
   hergibt.

Aufruf: python backtest.py
Benoetigte Pakete: pandas, yfinance (dieselben wie fuer app.py)
"""

import sys
import time

import pandas as pd
import yfinance as yf

from dip_score import (
    berechne_indikator_serien,
    score_am_punkt,
    KAUFSIGNAL_SCHWELLE,
    MARKT_BENCHMARK_TICKER,
)

# ==========================================
# KONFIGURATION
# ==========================================
ISIN_DATEI = "isin.txt"
VORSCHAU_TAGE = [5, 10, 15, 21]        # Handelstage fuer Forward-Return-Fenster
HAUPT_VORSCHAU = 21                    # entspricht eurem ~1-Monats-Ziel
GEWINN_ZIEL_PCT = 10.0                 # euer 10%+-Ziel
API_PAUSE_SEKUNDEN = 0.3               # kleine Pause zwischen Downloads

# Etablierte, sehr liquide US-Sektor-ETFs mit 20+ Jahren Historie -
# nur fuer die Zusatzvalidierung der grundsaetzlichen Score-Logik,
# nicht Teil eures eigentlichen Anlage-Universums.
ZUSATZVALIDIERUNG_TICKER = ["XLK", "XLF", "XLE", "XLI", "XLV"]


def parse_isin_file(filename=ISIN_DATEI):
    """Identisch zur Logik in app.py - bewusst hier dupliziert, damit
    backtest.py ohne Streamlit-Abhaengigkeiten lauffaehig bleibt."""
    etf_liste = []
    aktueller_sektor = "Allgemein"
    try:
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#"):
                        aktueller_sektor = line.lstrip("#").strip()
                    continue
                if ";" in line:
                    isin, ticker = line.split(";", 1)
                    etf_liste.append({
                        "sektor": aktueller_sektor,
                        "isin": isin.strip(),
                        "ticker": ticker.strip(),
                    })
                else:
                    etf_liste.append({
                        "sektor": aktueller_sektor,
                        "isin": line.strip(),
                        "ticker": None,
                    })
    except FileNotFoundError:
        print(f"WARNUNG: {filename} nicht gefunden - keine ETFs geladen.")
        return []
    return etf_liste


def lade_kursdaten(ticker, period="max"):
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            return None
        return df
    except Exception as e:
        print(f"  Fehler beim Laden von {ticker}: {e}")
        return None


def berechne_regime_serie(benchmark_ticker=MARKT_BENCHMARK_TICKER):
    """Historische Tag-fuer-Tag Marktregime-Zustaende (Benchmark ueber
    eigenem GD200?), als Series[bool] indiziert auf reine Datumswerte."""
    df = lade_kursdaten(benchmark_ticker)
    if df is None:
        print("WARNUNG: Benchmark nicht ladbar - Marktregime wird durchgehend als gesund angenommen.")
        return None
    close = df["Close"].dropna()
    gd200 = close.rolling(window=200).mean()
    regime = close > gd200
    regime.index = pd.to_datetime(regime.index).date
    return regime


def regime_an_datum(regime_serie, datum):
    if regime_serie is None:
        return True
    try:
        wert = regime_serie.loc[datum]
        if isinstance(wert, pd.Series):
            wert = wert.iloc[0]
        return bool(wert)
    except KeyError:
        return True


def forward_return(close, i, tage):
    if i + tage >= len(close):
        return None
    return ((close.iloc[i + tage] - close.iloc[i]) / close.iloc[i]) * 100


def forward_max_drawdown(low, close, i, tage):
    """Groesster Rueckgang vom Einstiegskurs innerhalb der naechsten
    `tage` Handelstage (auf Basis der Tagestiefs)."""
    if i + tage >= len(low):
        return None
    fenster = low.iloc[i + 1: i + 1 + tage]
    if fenster.empty:
        return 0.0
    tiefster = float(fenster.min())
    return ((tiefster - close.iloc[i]) / close.iloc[i]) * 100


def forward_ziel_erreicht(high, close, i, tage, ziel_pct):
    """Wurde innerhalb der naechsten `tage` Handelstage +ziel_pct erreicht?
    Gibt (bool, tage_bis_treffer) zurueck, tage_bis_treffer=None falls nie."""
    if i + tage >= len(high):
        return None, None
    ziel_kurs = close.iloc[i] * (1 + ziel_pct / 100)
    fenster = high.iloc[i + 1: i + 1 + tage]
    treffer = fenster[fenster >= ziel_kurs]
    if treffer.empty:
        return False, None
    tage_bis_treffer = fenster.index.get_loc(treffer.index[0]) + 1
    return True, tage_bis_treffer


def backtest_einzelnes_etf(isin, ticker, sektor, regime_serie):
    df = lade_kursdaten(ticker)
    mindest_laenge = 200 + max(VORSCHAU_TAGE) + 1
    if df is None or len(df) < mindest_laenge:
        laenge = 0 if df is None else len(df)
        print(f"    -> uebersprungen (nur {laenge} Handelstage Historie, "
              f"mindestens {mindest_laenge} noetig)")
        return []

    close = df["Close"].dropna()
    low = df["Low"].dropna() if "Low" in df else close
    high = df["High"].dropna() if "High" in df else close

    indikatoren = berechne_indikator_serien(close, high, low)

    ergebnisse = []
    start_i = 199  # erster Index mit vollstaendigem GD200 (0-indexiert)
    end_i = len(close) - max(VORSCHAU_TAGE)  # exklusiv

    for i in range(start_i, end_i):
        if pd.isna(indikatoren["gd200"].iloc[i]):
            continue

        datum = close.index[i].date()
        regime_ok = regime_an_datum(regime_serie, datum)

        score_result = score_am_punkt(indikatoren, i, regime_ok=regime_ok)

        zeile = {
            "sektor": sektor,
            "isin": isin,
            "ticker": ticker,
            "datum": datum,
            **score_result,
        }

        for t in VORSCHAU_TAGE:
            zeile[f"return_{t}t"] = forward_return(close, i, t)

        zeile["max_drawdown_21t"] = forward_max_drawdown(low, close, i, HAUPT_VORSCHAU)
        ziel_erreicht, tage_bis_ziel = forward_ziel_erreicht(
            high, close, i, HAUPT_VORSCHAU, GEWINN_ZIEL_PCT
        )
        zeile["ziel_erreicht_21t"] = ziel_erreicht
        zeile["tage_bis_ziel"] = tage_bis_ziel

        ergebnisse.append(zeile)

    return ergebnisse


def episoden_zaehlen(df, score_spalte="dip_score", schwelle=KAUFSIGNAL_SCHWELLE):
    """Fasst aufeinanderfolgende Tage mit score>=schwelle pro ETF zu EINER
    Episode zusammen (nur der erste Tag zaehlt), damit z.B. 5 Tage am
    Stueck ueber der Schwelle nicht als 5 unabhaengige Ereignisse in die
    Statistik eingehen."""
    df = df.sort_values(["isin", "datum"]).copy()
    df["ueber_schwelle"] = df[score_spalte] >= schwelle
    vorheriger_tag = df.groupby("isin")["ueber_schwelle"].shift(1, fill_value=False)
    df["episode_start"] = df["ueber_schwelle"] & (~vorheriger_tag)
    return df[df["episode_start"]].copy()


def zusammenfassung(df, label=""):
    print(f"\n{'=' * 60}")
    print(f"AUSWERTUNG {label}")
    print(f"{'=' * 60}")
    print(f"ETF-Tage gesamt: {len(df)}")

    korr_df = df[["dip_score", f"return_{HAUPT_VORSCHAU}t"]].dropna()
    if len(korr_df) > 1:
        korr = korr_df.corr().iloc[0, 1]
        print(f"Korrelation Dip Score <-> {HAUPT_VORSCHAU}-Tage-Return: {korr:.3f}")
    else:
        print("Zu wenig Daten fuer Korrelation.")

    print(f"\nDip Score gebucketed (Ø {HAUPT_VORSCHAU}-Tage-Return):")
    bins = [0, 20, 40, 60, KAUFSIGNAL_SCHWELLE, 100]
    df["score_bucket"] = pd.cut(df["dip_score"], bins=sorted(set(bins)), include_lowest=True)
    bucket_stats = df.groupby("score_bucket", observed=True)[f"return_{HAUPT_VORSCHAU}t"].agg(
        ["mean", "count"]
    )
    print(bucket_stats)

    episoden = episoden_zaehlen(df)
    print(f"\nEpisoden mit Score >= {KAUFSIGNAL_SCHWELLE} (Kaufsignal-Schwelle): {len(episoden)}")
    if len(episoden) > 0:
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        if len(valide) > 0:
            win_rate = (valide[f"return_{HAUPT_VORSCHAU}t"] > 0).mean() * 100
            ziel_rate = valide["ziel_erreicht_21t"].mean() * 100
            avg_return = valide[f"return_{HAUPT_VORSCHAU}t"].mean()
            avg_dd = valide["max_drawdown_21t"].mean()
            print(f"  Trefferquote (Return > 0 nach {HAUPT_VORSCHAU}T): {win_rate:.1f}%")
            print(f"  Quote {GEWINN_ZIEL_PCT:.0f}%-Ziel erreicht: {ziel_rate:.1f}%")
            print(f"  Ø Return nach {HAUPT_VORSCHAU}T: {avg_return:+.2f}%")
            print(f"  Ø max. Drawdown im Fenster: {avg_dd:+.2f}%")
        else:
            print("  Zu wenige Episoden mit vollstaendigem Vorschau-Fenster fuer Statistik.")
    print("\n  Hinweis: Bei wenigen Episoden (<~20) sind diese Zahlen eine")
    print("  Tendenz, kein statistischer Beweis - siehe unsere Absprache")
    print("  zur begrenzten Historie eures ETF-Universums.")

    print(f"\nKorrelation der einzelnen Score-Komponenten mit dem {HAUPT_VORSCHAU}-Tage-Return:")
    for komponente in ["rsi_score", "trend_score", "gd200_score", "ema50_score", "turnaround_score"]:
        k_df = df[[komponente, f"return_{HAUPT_VORSCHAU}t"]].dropna()
        if len(k_df) > 1:
            k = k_df.corr().iloc[0, 1]
            print(f"  {komponente}: {k:.3f}")

    return episoden


def main():
    print("Lade Marktregime-Historie...")
    regime_serie = berechne_regime_serie()

    print("\n--- HAUPT-BACKTEST: euer eigenes ETF-Universum ---")
    etfs = parse_isin_file()
    if not etfs:
        print(f"Keine ETFs in {ISIN_DATEI} gefunden. Skript abgebrochen.")
        sys.exit(1)

    alle_ergebnisse = []
    for item in etfs:
        ticker = item.get("ticker")
        if not ticker:
            print(f"  Uebersprungen (kein Ticker in {ISIN_DATEI}): {item['isin']}")
            continue
        print(f"  Backteste {ticker} ({item['isin']})...")
        ergebnisse = backtest_einzelnes_etf(item["isin"], ticker, item["sektor"], regime_serie)
        alle_ergebnisse.extend(ergebnisse)
        time.sleep(API_PAUSE_SEKUNDEN)

    if not alle_ergebnisse:
        print("Keine auswertbaren Ergebnisse (zu kurze Historie ueberall?). Abbruch.")
        sys.exit(1)

    df = pd.DataFrame(alle_ergebnisse)
    df.to_csv("backtest_ergebnisse.csv", index=False)
    print(f"\n{len(df)} ETF-Tage gespeichert in backtest_ergebnisse.csv")

    zusammenfassung(df, label="- Euer ETF-Universum")

    print("\n\n--- ZUSATZVALIDIERUNG: etablierte, langjaehrige Sektor-ETFs (USD) ---")
    zusatz_ergebnisse = []
    for ticker in ZUSATZVALIDIERUNG_TICKER:
        print(f"  Backteste {ticker}...")
        ergebnisse = backtest_einzelnes_etf(ticker, ticker, "Zusatzvalidierung", regime_serie)
        zusatz_ergebnisse.extend(ergebnisse)
        time.sleep(API_PAUSE_SEKUNDEN)

    if zusatz_ergebnisse:
        df_zusatz = pd.DataFrame(zusatz_ergebnisse)
        df_zusatz.to_csv("backtest_zusatzvalidierung.csv", index=False)
        zusammenfassung(df_zusatz, label="- Zusatzvalidierung (US Sector SPDRs)")
    else:
        print("Keine Zusatzvalidierungs-Ergebnisse.")


if __name__ == "__main__":
    main()
