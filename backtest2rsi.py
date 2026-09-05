"""
backtest_rsi2.py

Separater Backtest fuer den Connors-RSI(2)-Ansatz - bewusst getrennt von
backtest.py (RSI14-Dip-Score-System) gehalten, damit sich die beiden
Forschungsstraenge nicht gegenseitig verwaessern. Nutzt aber dieselbe
Dateninfrastruktur (Datenladen, Marktregime-Filter, Cluster-Robustheit,
Zielrenditen-Quoten) aus backtest.py wieder, statt sie zu duplizieren.

Kriterien (Connors, klassisch) - alle vier gleichzeitig am Handelsende
erfuellt = Signal:
- Trigger: RSI(2) < 5
- Trendfilter: Kurs > SMA200
- Kurzfrist-Korrekturfilter: Kurs < SMA5
- Marktregime: Referenzindex (MSCI World) > eigenem GD200

Exit: Kurs > SMA5 ODER RSI(2) > 70 (was zuerst eintritt).

Universum: die 4 Welt-/Momentum-ETFs (fuer die Connors' Methodik
urspruenglich validiert wurde) PLUS alle Sektor-ETFs aus isin.txt (fuer
mehr Signale zur Auswertung, wie besprochen). Beide werden getrennt UND
zusammen ausgewertet, damit die "reinere" Welt-/Momentum-Teilmenge nicht
im groesseren, aber weniger repraesentativen Sektor-Universum untergeht.

WICHTIG - bewusste Vereinfachung: Nutzt dieselbe native Kursreihe
(Xetra/gettex-Listing) wie das RSI14-System, KEINE Umstellung auf
US-Kursdaten fuer die Signalberechnung (das war eine offene Frage aus
unserer Diskussion - zurueckgestellt, siehe Notiz weiter oben im Chat).

Aufruf: python backtest_rsi2.py
Benoetigt: backtest.py im selben Ordner (wird importiert, nicht erneut
ausgefuehrt), sowie isin.txt und Internetzugang.
"""

import sys
import time

import pandas as pd

from backtest import (
    parse_isin_file,
    lade_kursdaten,
    berechne_regime_serie,
    regime_an_datum,
    episoden_aus_maske,
    geclusterte_kennzahlen,
    forward_return,
    forward_max_drawdown,
    ziele_erreicht_multi,
    _episode_index,
    ZIEL_SCHWELLEN,
)

# ==========================================
# KONFIGURATION
# ==========================================
ISIN_DATEI = "isin.txt"
WELT_MOMENTUM_TICKER = {
    # Ticker-Kuerzel -> ISIN, wie in der Diskussion festgelegt
    "IS3N.DE": "IE00BKM4GZ66",   # MSCI World EM IMI
    "SPPW.DE": "IE00BFY0GT14",   # MSCI World
    "XDEM.DE": "IE00BL25JP72",   # MSCI Momentum
    "SXR8.DE": "IE00B5BMR087",   # S&P 500
}
RSI2_TRIGGER_SCHWELLE = 5.0
RSI2_EXIT_SCHWELLE = 70.0
VORSCHAU_TAGE = [5, 7, 10, 15, 21]
HAUPT_VORSCHAU = 21
MAX_TAGE_EXIT_SIMULATION = 40
API_PAUSE_SEKUNDEN = 0.3


def berechne_rsi2_indikatoren(close):
    """SMA5, SMA200 und RSI(2) - gleiche Wilder-Glaettung wie beim RSI14,
    nur mit Periode 2 statt 14 (alpha=1/2 statt 1/14)."""
    sma5 = close.rolling(window=5).mean()
    sma200 = close.rolling(window=200).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 2, min_periods=2, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 2, min_periods=2, adjust=False).mean()
    rsi2 = 100 - (100 / (1 + (avg_gain / avg_loss)))

    return pd.DataFrame({"close": close, "sma5": sma5, "sma200": sma200, "rsi2": rsi2})


def simuliere_connors_exit(close, sma5_serie, rsi2_serie, i, max_tage=MAX_TAGE_EXIT_SIMULATION):
    """Exit sobald Kurs > SMA5 ODER RSI(2) > 70 - was zuerst eintritt."""
    buy_price = float(close.iloc[i])

    for t in range(1, max_tage + 1):
        idx = i + t
        if idx >= len(close):
            break
        kurs = float(close.iloc[idx])
        sma5 = sma5_serie.iloc[idx]
        rsi2 = rsi2_serie.iloc[idx]
        sma5_ok = (not pd.isna(sma5)) and kurs > float(sma5)
        rsi2_ok = (not pd.isna(rsi2)) and float(rsi2) > RSI2_EXIT_SCHWELLE

        if sma5_ok or rsi2_ok:
            rendite = ((kurs - buy_price) / buy_price) * 100
            return {
                "exit_typ": "SMA5_erreicht" if sma5_ok else "RSI2_ueber_70",
                "exit_tag": t,
                "exit_price": round(kurs, 4),
                "gesamt_rendite_pct": round(rendite, 2),
                "haltedauer_tage": t,
            }

    letzter_idx = min(i + max_tage, len(close) - 1)
    letzter_kurs = float(close.iloc[letzter_idx])
    rendite = ((letzter_kurs - buy_price) / buy_price) * 100
    return {
        "exit_typ": "offen_kein_exit",
        "exit_tag": None,
        "exit_price": round(letzter_kurs, 4),
        "gesamt_rendite_pct": round(rendite, 2),
        "haltedauer_tage": letzter_idx - i,
    }


def analysiere_etf_rsi2(isin, ticker, sektor, regime_serie):
    df = lade_kursdaten(ticker)
    mindest_laenge = 200 + max(VORSCHAU_TAGE) + 1
    if df is None or len(df) < mindest_laenge:
        laenge = 0 if df is None else len(df)
        print(f"    -> uebersprungen (nur {laenge} Handelstage Historie, "
              f"mindestens {mindest_laenge} noetig)")
        return [], None

    close = df["Close"].dropna()
    high = df["High"].dropna() if "High" in df else close
    low = df["Low"].dropna() if "Low" in df else close
    ind = berechne_rsi2_indikatoren(close)

    ergebnisse = []
    start_i = 199
    end_i = len(close) - max(VORSCHAU_TAGE)

    for i in range(start_i, end_i):
        if pd.isna(ind["sma200"].iloc[i]) or pd.isna(ind["rsi2"].iloc[i]) or pd.isna(ind["sma5"].iloc[i]):
            continue

        kurs = float(ind["close"].iloc[i])
        sma5 = float(ind["sma5"].iloc[i])
        sma200 = float(ind["sma200"].iloc[i])
        rsi2 = float(ind["rsi2"].iloc[i])

        datum = close.index[i].date()
        regime_ok = regime_an_datum(regime_serie, datum)

        signal = (
            (rsi2 < RSI2_TRIGGER_SCHWELLE)
            and (kurs > sma200)
            and (kurs < sma5)
            and regime_ok
        )

        zeile = {
            "sektor": sektor, "isin": isin, "ticker": ticker, "datum": datum,
            "close": kurs, "sma5": sma5, "sma200": sma200, "rsi2": round(rsi2, 2),
            "regime_ok": regime_ok, "signal": signal,
        }
        for t in VORSCHAU_TAGE:
            zeile[f"return_{t}t"] = forward_return(close, i, t)
        zeile["max_drawdown_21t"] = forward_max_drawdown(low, close, i, HAUPT_VORSCHAU)
        zeile.update(ziele_erreicht_multi(high, close, i, max_tage=MAX_TAGE_EXIT_SIMULATION))

        ergebnisse.append(zeile)

    serien = {"close": close, "sma5": ind["sma5"], "rsi2": ind["rsi2"]}
    return ergebnisse, serien


def episoden_rsi2(df):
    """Fasst aufeinanderfolgende Signal-Tage pro ETF zu einer Episode
    zusammen - identische Logik zu episoden_zaehlen(), nur auf der
    booleschen 'signal'-Spalte statt einem Score-Schwellenwert."""
    return episoden_aus_maske(df, df["signal"])


def exit_analyse_rsi2(df, serien_cache):
    episoden = episoden_rsi2(df)
    zeilen = []
    for _, ep in episoden.iterrows():
        serien, i = _episode_index(serien_cache, ep)
        if serien is None:
            continue
        r = simuliere_connors_exit(serien["close"], serien["sma5"], serien["rsi2"], i)
        r["sektor"] = ep["sektor"]
        r["isin"] = ep["isin"]
        r["ticker"] = ep["ticker"]
        r["datum"] = ep["datum"]
        zeilen.append(r)
    return pd.DataFrame(zeilen)


def zusammenfassung_rsi2(df, exit_df, label=""):
    print(f"\n{'=' * 60}")
    print(f"AUSWERTUNG RSI(2)-CONNORS {label}")
    print(f"{'=' * 60}")

    episoden = episoden_rsi2(df)
    valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
    print(f"ETF-Tage gesamt: {len(df)}")
    print(f"Signale (Episoden): {len(episoden)}, mit vollem Vorschau-Fenster: {len(valide)}")

    if len(valide) > 0:
        cluster_stats = geclusterte_kennzahlen(valide, f"return_{HAUPT_VORSCHAU}t")
        rendite_spalte = valide[f"return_{HAUPT_VORSCHAU}t"]
        trefferquote = (rendite_spalte > 0).mean() * 100
        print(f"\nTrefferquote (Return > 0 nach {HAUPT_VORSCHAU}T): {trefferquote:.1f}%")
        print(f"Ø Return: {rendite_spalte.mean():+.2f}% "
              f"(geclustert: {cluster_stats['geclusterter_mittelwert_pct']}%)")
        print(f"Unabhängige Marktereignisse (Cluster): {cluster_stats['anzahl_cluster']}")

        print(f"\nZielrenditen-Trefferquoten (Fenster: {MAX_TAGE_EXIT_SIMULATION} Handelstage):")
        for label_z, _ in ZIEL_SCHWELLEN:
            spalte = f"erreicht_{label_z}"
            if spalte in valide.columns and valide[spalte].notna().any():
                quote = valide[spalte].dropna().mean() * 100
                print(f"  +{label_z}: {quote:.1f}%")
    else:
        print("Keine Episoden mit vollständigem Vorschau-Fenster - keine Statistik möglich.")

    if not exit_df.empty:
        cluster_stats_exit = geclusterte_kennzahlen(exit_df, "gesamt_rendite_pct")
        print(f"\nExit-Regel (Kurs > SMA5 ODER RSI(2) > {RSI2_EXIT_SCHWELLE:.0f}):")
        print(f"  Ø Rendite: {exit_df['gesamt_rendite_pct'].mean():+.2f}% "
              f"(geclustert: {cluster_stats_exit['geclusterter_mittelwert_pct']}%)")
        print(f"  Ø Haltedauer: {exit_df['haltedauer_tage'].mean():.1f} Tage")
        print("  Exit-Typ-Verteilung:")
        for typ, anzahl in exit_df["exit_typ"].value_counts().items():
            print(f"    {typ}: {anzahl} ({anzahl / len(exit_df) * 100:.1f}%)")
    else:
        print("\nKeine Exit-Episoden vorhanden.")

    print("\n  Hinweis: Bei wenigen Clustern (<~15-20) sind das Tendenzen, kein Beweis.")


def main():
    print("Lade Marktregime-Historie...")
    regime_serie = berechne_regime_serie()

    alle_ticker = []
    for ticker, isin in WELT_MOMENTUM_TICKER.items():
        alle_ticker.append({"sektor": "Welt/Momentum", "isin": isin, "ticker": ticker})

    sektor_etfs = parse_isin_file(ISIN_DATEI)
    anzahl_sektor_mit_ticker = 0
    for item in sektor_etfs:
        if item.get("ticker"):
            alle_ticker.append(item)
            anzahl_sektor_mit_ticker += 1

    print(f"\n{len(alle_ticker)} Ticker im Universum "
          f"({len(WELT_MOMENTUM_TICKER)} Welt/Momentum + {anzahl_sektor_mit_ticker} Sektor-ETFs).")

    alle_ergebnisse = []
    serien_cache = {}
    for item in alle_ticker:
        ticker = item["ticker"]
        print(f"  Backteste {ticker} ({item['isin']})...")
        ergebnisse, serien = analysiere_etf_rsi2(item["isin"], ticker, item["sektor"], regime_serie)
        alle_ergebnisse.extend(ergebnisse)
        if serien is not None:
            serien_cache[ticker] = serien
        time.sleep(API_PAUSE_SEKUNDEN)

    if not alle_ergebnisse:
        print("Keine auswertbaren Ergebnisse. Abbruch.")
        sys.exit(1)

    df = pd.DataFrame(alle_ergebnisse)
    df.to_csv("backtest_rsi2_ergebnisse.csv", index=False)
    print(f"\n{len(df)} ETF-Tage gespeichert in backtest_rsi2_ergebnisse.csv")

    exit_df = exit_analyse_rsi2(df, serien_cache)
    if not exit_df.empty:
        exit_df.to_csv("backtest_rsi2_exit.csv", index=False)
        print(f"{len(exit_df)} Exit-Episoden gespeichert in backtest_rsi2_exit.csv")

    zusammenfassung_rsi2(df, exit_df, label="- Gesamtuniversum (Welt/Momentum + Sektor-ETFs)")

    # Getrennte Auswertung nur für die 4 Welt-/Momentum-ETFs - das ist die
    # Teilmenge, die Connors' ursprünglicher Validierungsbasis (liquide,
    # breite Indizes) am nächsten kommt.
    welt_ticker_namen = set(WELT_MOMENTUM_TICKER.keys())
    df_welt = df[df["ticker"].isin(welt_ticker_namen)]
    exit_df_welt = (
        exit_df[exit_df["ticker"].isin(welt_ticker_namen)] if not exit_df.empty else exit_df
    )
    if not df_welt.empty:
        zusammenfassung_rsi2(df_welt, exit_df_welt, label="- NUR Welt-/Momentum-ETFs")
    else:
        print("\nKeine auswertbaren Daten für die Welt-/Momentum-ETFs allein.")


if __name__ == "__main__":
    main()
