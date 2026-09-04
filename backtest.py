"""
backtest.py

Historischer Backtest fuer die Dip-Score-Logik aus dip_score.py.

WICHTIG: Dieses Skript braucht Internetzugang (yfinance) sowie eure
isin.txt im selben Ordner. Es wurde in dieser Sandbox NICHT gegen echte
Daten ausgefuehrt (kein Netzwerkzugriff hier verfuegbar) - bitte lokal
laufen lassen. Falls Fehler auftauchen, gerne Rueckmeldung geben, dann
fixe ich das Skript gezielt.

Ablauf:
1. ETF-Universum aus isin.txt laden (gleiches Format wie app.py).
2. Fuer jeden ETF die maximal verfuegbare Historie laden.
3. Einmalig die Benchmark-Historie laden (Marktregime-Filter).
4. Fuer jeden Handelstag ab Tag 200 (GD200 verfuegbar) bis 21 Tage vor
   Ende der Historie den Score + feste Forward-Returns (5/7/10/15/21
   Tage) berechnen. -> zusammenfassung() / schwellen_sweep()
5. Fuer die Episoden bei der AKTUELLEN Kaufsignal-Schwelle zusaetzlich:
   Tag-fuer-Tag-Renditekurve (bis 40 Tage) UND Simulation eurer echten
   T1/T2-Exit-Regel (EMA50/52-Wochen-Hoch/dynamischer Stop), verglichen
   mit festen Haltedauern und dem theoretischen Optimum.
6. Zusatzvalidierung auf etablierten, langjaehrigen US-Sektor-ETFs.

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
    SOFT_KAUFSIGNAL_SCHWELLE,
    MARKT_BENCHMARK_TICKER,
)

# ==========================================
# KONFIGURATION
# ==========================================
ISIN_DATEI = "isin.txt"
VORSCHAU_TAGE = [5, 7, 10, 15, 21]     # Handelstage fuer feste Forward-Return-Punkte
HAUPT_VORSCHAU = 21                    # entspricht eurem ~1-Monats-Ziel
ZIEL_SCHWELLEN = [
    ("2pct", 2.0),
    ("3pct", 3.0),
    ("5pct", 5.0),
    ("7_5pct", 7.5),
    ("10pct", 10.0),
]  # eure Zielrenditen-Bandbreite (2% bis 10%+), Fenster = MAX_TAGE_EXIT_SIMULATION
MAX_TAGE_EXIT_SIMULATION = 40          # Obergrenze fuer Tag-fuer-Tag-Kurve & T1/T2-Sim
SCHWELLEN_SWEEP_BEREICH = range(30, 95, 5)  # Kandidaten-Schwellen fuer Frage 1
CLUSTER_MAX_LUECKE_TAGE = 7            # Episoden, die hoechstens so viele Kalendertage
                                        # auseinanderliegen (ueber ALLE Ticker hinweg),
                                        # gelten als dasselbe "Marktereignis"
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


def ziele_erreicht_multi(high, close, i, schwellen=ZIEL_SCHWELLEN, max_tage=MAX_TAGE_EXIT_SIMULATION):
    """Fuer JEDE Ziel-Rendite in `schwellen` (z.B. 2/3/5/7,5/10%): wurde sie
    innerhalb `max_tage` Handelstagen erreicht, und nach wie vielen Tagen
    (erstes Erreichen, auf Basis des Tageshochs)? Beantwortet direkt:
    'Bei welchem Score habe ich eine hohe Wahrscheinlichkeit auf mindestens
    +X% in einem ueberschaubaren Zeitraum?' - fuer mehrere X gleichzeitig,
    statt nur fuer ein einzelnes fest verdrahtetes Ziel."""
    ergebnis = {}
    if i + 1 >= len(high):
        for label, _ in schwellen:
            ergebnis[f"erreicht_{label}"] = None
            ergebnis[f"tage_bis_{label}"] = None
        return ergebnis

    fenster = high.iloc[i + 1: i + 1 + max_tage]
    for label, ziel_pct in schwellen:
        ziel_kurs = close.iloc[i] * (1 + ziel_pct / 100)
        treffer = fenster[fenster >= ziel_kurs]
        if treffer.empty:
            ergebnis[f"erreicht_{label}"] = False
            ergebnis[f"tage_bis_{label}"] = None
        else:
            tage_bis_treffer = fenster.index.get_loc(treffer.index[0]) + 1
            ergebnis[f"erreicht_{label}"] = True
            ergebnis[f"tage_bis_{label}"] = tage_bis_treffer
    return ergebnis


def rendite_kurve(close, i, max_tage=MAX_TAGE_EXIT_SIMULATION):
    """Tag-fuer-Tag-Rendite ab Tag i (Index-Position) bis max_tage Tage
    voraus. Liefert u.a. den besten Tag nach Gesamtrendite UND nach
    Rendite/Tag (Kapital-Effizienz) - das theoretische (nicht handelbare)
    Optimum als obere Vergleichslinie."""
    tage, renditen = [], []
    for t in range(1, max_tage + 1):
        idx = i + t
        if idx >= len(close):
            break
        rendite = ((close.iloc[idx] - close.iloc[i]) / close.iloc[i]) * 100
        tage.append(t)
        renditen.append(rendite)

    ergebnis = {
        "bester_tag_gesamt": None,
        "bester_rendite_gesamt": None,
        "bester_tag_effizienz": None,
        "beste_rendite_pro_tag": None,
        "rendite_tag7": None,
        "rendite_tag21": None,
    }
    if not renditen:
        return ergebnis

    serie = pd.Series(renditen, index=tage)
    tage_index = pd.Series(tage, index=tage, dtype=float)
    pro_tag = serie / tage_index

    ergebnis["bester_tag_gesamt"] = int(serie.idxmax())
    ergebnis["bester_rendite_gesamt"] = round(float(serie.max()), 2)
    ergebnis["bester_tag_effizienz"] = int(pro_tag.idxmax())
    ergebnis["beste_rendite_pro_tag"] = round(float(pro_tag.max()), 3)
    if 7 in serie.index:
        ergebnis["rendite_tag7"] = round(float(serie.loc[7]), 2)
    if 21 in serie.index:
        ergebnis["rendite_tag21"] = round(float(serie.loc[21]), 2)
    return ergebnis


def simuliere_t1_t2_regel(close, ema50_serie, high_252_serie, i, max_tage=MAX_TAGE_EXIT_SIMULATION):
    """Simuliert EURE tatsaechliche Exit-Regel aus app.py (Tab 2):
    T1 (50%) sobald Kurs >= EMA50 (an diesem Tag - die 'wandernde' EMA50,
    exakt wie aktuell in der App), danach T2 (restliche 50%) entweder bei
    52-Wochen-Hoch * 0.99 oder beim dynamischen Stop (Kaufkurs + 50% des
    T1-Gewinns), je nachdem was zuerst eintritt."""
    buy_price = float(close.iloc[i])
    t1_triggered = False
    t1_tag, t1_price = None, None

    for t in range(1, max_tage + 1):
        idx = i + t
        if idx >= len(close):
            break
        aktueller_kurs = float(close.iloc[idx])

        if not t1_triggered:
            ema50_aktuell = float(ema50_serie.iloc[idx])
            if aktueller_kurs >= ema50_aktuell:
                t1_triggered = True
                t1_tag = t
                t1_price = aktueller_kurs
            continue

        t1_profit = t1_price - buy_price
        stop_loss_limit = buy_price + (t1_profit / 2.0) if t1_profit > 0 else buy_price
        hoch_252 = float(high_252_serie.iloc[idx])
        t2_ziel = hoch_252 * 0.99

        if aktueller_kurs >= t2_ziel:
            gesamt_rendite_pct = (
                ((t1_price - buy_price) * 0.5 + (aktueller_kurs - buy_price) * 0.5) / buy_price
            ) * 100
            return {
                "t1_tag": t1_tag, "t1_price": round(t1_price, 4),
                "exit_typ": "T2_erreicht", "exit_tag": t,
                "exit_price": round(aktueller_kurs, 4),
                "gesamt_rendite_pct": round(gesamt_rendite_pct, 2),
                "haltedauer_tage": t,
            }
        if aktueller_kurs <= stop_loss_limit:
            gesamt_rendite_pct = (
                ((t1_price - buy_price) * 0.5 + (aktueller_kurs - buy_price) * 0.5) / buy_price
            ) * 100
            return {
                "t1_tag": t1_tag, "t1_price": round(t1_price, 4),
                "exit_typ": "StopLoss", "exit_tag": t,
                "exit_price": round(aktueller_kurs, 4),
                "gesamt_rendite_pct": round(gesamt_rendite_pct, 2),
                "haltedauer_tage": t,
            }

    # Kein vollstaendiger Exit innerhalb max_tage -> offene Position markieren
    letzter_idx = min(i + max_tage, len(close) - 1)
    letzter_kurs = float(close.iloc[letzter_idx])
    tage_bis_ende = letzter_idx - i

    if t1_triggered:
        gesamt_rendite_pct = (
            ((t1_price - buy_price) * 0.5 + (letzter_kurs - buy_price) * 0.5) / buy_price
        ) * 100
        exit_typ = "offen_nach_T1"
    else:
        gesamt_rendite_pct = ((letzter_kurs - buy_price) / buy_price) * 100
        exit_typ = "offen_kein_T1"

    return {
        "t1_tag": t1_tag,
        "t1_price": round(t1_price, 4) if t1_price is not None else None,
        "exit_typ": exit_typ, "exit_tag": None,
        "exit_price": round(letzter_kurs, 4),
        "gesamt_rendite_pct": round(gesamt_rendite_pct, 2),
        "haltedauer_tage": tage_bis_ende,
    }


def simuliere_prozent_ziel_exit(close, high, i, ziel_pct, max_tage=MAX_TAGE_EXIT_SIMULATION):
    """Einfache Alternativ-Regel zu simuliere_t1_t2_regel: Kompletter
    Verkauf (100%, keine Tranchen), sobald der Kurs +ziel_pct erreicht -
    unabhaengig von EMA50/GD200/52W-Hoch. Exit-Kurs ist der Zielkurs
    selbst (konservativ, nicht das ggf. hoehere Tageshoch)."""
    buy_price = float(close.iloc[i])
    ziel_kurs = buy_price * (1 + ziel_pct / 100)

    for t in range(1, max_tage + 1):
        idx = i + t
        if idx >= len(close):
            break
        tages_hoch = float(high.iloc[idx])
        if tages_hoch >= ziel_kurs:
            return {
                "exit_typ": "Ziel_erreicht",
                "exit_tag": t,
                "exit_price": round(ziel_kurs, 4),
                "gesamt_rendite_pct": round(ziel_pct, 2),
                "haltedauer_tage": t,
            }

    letzter_idx = min(i + max_tage, len(close) - 1)
    letzter_kurs = float(close.iloc[letzter_idx])
    tage_bis_ende = letzter_idx - i
    rendite = ((letzter_kurs - buy_price) / buy_price) * 100
    return {
        "exit_typ": "offen_ziel_nicht_erreicht",
        "exit_tag": None,
        "exit_price": round(letzter_kurs, 4),
        "gesamt_rendite_pct": round(rendite, 2),
        "haltedauer_tage": tage_bis_ende,
    }


def analysiere_etf(isin, ticker, sektor, regime_serie):
    """Laedt Kursdaten, berechnet die Score-/Forward-Return-Zeilen fuer
    JEDEN Tag (fuer Korrelation/Buckets/Schwellen-Sweep) UND gibt
    zusaetzlich die Rohserien zurueck (fuer die spaetere episodenbasierte
    T1/T2-Exit-Simulation, ohne die Daten ein zweites Mal laden zu
    muessen)."""
    df = lade_kursdaten(ticker)
    mindest_laenge = 200 + max(VORSCHAU_TAGE) + 1
    if df is None or len(df) < mindest_laenge:
        laenge = 0 if df is None else len(df)
        print(f"    -> uebersprungen (nur {laenge} Handelstage Historie, "
              f"mindestens {mindest_laenge} noetig)")
        return [], None

    close = df["Close"].dropna()
    low = df["Low"].dropna() if "Low" in df else close
    high = df["High"].dropna() if "High" in df else close

    indikatoren = berechne_indikator_serien(close, high, low)
    high_252 = high.rolling(window=252, min_periods=1).max()
    hoch_10t = close.rolling(window=10, min_periods=1).max()
    hoch_20t = close.rolling(window=20, min_periods=1).max()

    # --- DIAGNOSE 1: ATR-normalisierter Rueckgang (nicht im Live-Score,
    #     nur zum Testen, ob die rohe Prozent-Tiefe Sektor-verzerrt ist) ---
    prev_close = close.shift(1)
    true_range = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = true_range.rolling(window=14).mean()
    drawdown_abs_20t = hoch_20t - close  # positiver Wert = so weit unter dem 20T-Hoch
    drawdown_atr_multiple = drawdown_abs_20t / atr14

    # --- DIAGNOSE 2: Volumen-Verhaeltnis (Tagesvolumen / 20T-Schnitt) ---
    if "Volume" in df.columns:
        volume = df["Volume"].reindex(close.index)
        volume_avg_20t = volume.rolling(window=20, min_periods=5).mean()
        volumen_ratio = volume / volume_avg_20t
    else:
        volumen_ratio = pd.Series(float("nan"), index=close.index)

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
        # Wie stark ist der Kurs VOR dem Signal auf 10-Tage-Sicht gefallen?
        # (10-Tage-Variante als Ergaenzung - die 20-Tage-Variante liefert
        # score_result bereits als "drawdown_20t_pct" mit)
        zeile["drawdown_10t_vor_signal_pct"] = round(
            ((close.iloc[i] - hoch_10t.iloc[i]) / hoch_10t.iloc[i]) * 100, 2
        )

        atr_wert = drawdown_atr_multiple.iloc[i]
        zeile["drawdown_atr_multiple"] = (
            round(float(atr_wert), 3) if pd.notna(atr_wert) and atr_wert != float("inf") else None
        )
        vol_wert = volumen_ratio.iloc[i]
        zeile["volumen_ratio"] = (
            round(float(vol_wert), 3) if pd.notna(vol_wert) and vol_wert != float("inf") else None
        )

        # --- DIAGNOSE 3: Trend-/GD200-Komponenten einzeln (ChatGPT-Punkt:
        #     beide zeigten leicht negative Korrelation mit der Rendite,
        #     nie isoliert nachgeprueft). Beide direkt aus score_result
        #     ableitbar, keine Aenderung an dip_score.py noetig. ---
        zeile["ema50_ueber_gd200"] = score_result["ema50"] > score_result["gd200"]
        zeile["gd200_puffer_pct"] = round(
            ((score_result["close"] - score_result["gd200"]) / score_result["gd200"]) * 100, 2
        ) if score_result["gd200"] else 0.0

        zeile.update(ziele_erreicht_multi(high, close, i))

        ergebnisse.append(zeile)

    serien = {
        "close": close, "high": high, "low": low,
        "ema50": indikatoren["ema50"], "high_252": high_252,
    }
    return ergebnisse, serien


def episoden_aus_maske(df, maske_serie):
    """Fasst aufeinanderfolgende Tage, an denen `maske_serie` True ist,
    PRO ETF zu EINER Episode zusammen (nur der erste Tag zaehlt). Basis-
    Baustein fuer episoden_zaehlen() und alle Einzelfaktor-Analysen unten -
    `maske_serie` wird ueber den Original-Index von `df` ausgerichtet,
    nicht positionsbasiert (wichtig, da df hier nach isin/datum sortiert
    wird)."""
    df = df.sort_values(["isin", "datum"]).copy()
    df["_maske"] = maske_serie.reindex(df.index)
    vorheriger_tag = df.groupby("isin")["_maske"].shift(1, fill_value=False)
    df["episode_start"] = df["_maske"] & (~vorheriger_tag)
    ergebnis = df[df["episode_start"]].copy()
    return ergebnis.drop(columns=["_maske"])


def episoden_zaehlen(df, score_spalte="dip_score", schwelle=KAUFSIGNAL_SCHWELLE):
    """Fasst aufeinanderfolgende Tage mit score>=schwelle PRO ETF zu EINER
    Episode zusammen (nur der erste Tag zaehlt), damit z.B. 5 Tage am
    Stueck ueber der Schwelle nicht als 5 unabhaengige Ereignisse in die
    Statistik eingehen. Deckt NICHT die Haeufung UEBER Ticker hinweg ab
    (z.B. 33 ETFs am selben Tag) - dafuer siehe cluster_zuweisen()."""
    return episoden_aus_maske(df, df[score_spalte] >= schwelle)


def cluster_zuweisen(episoden_df, max_luecke_tage=CLUSTER_MAX_LUECKE_TAGE):
    """Gruppiert Episoden UEBER ALLE TICKER HINWEG zu 'Marktereignissen':
    Episoden, deren Datum hoechstens `max_luecke_tage` Kalendertage von der
    zeitlich naechsten anderen Episode entfernt liegt, gehoeren zum selben
    Cluster - unabhaengig davon, welcher Ticker betroffen ist. Das faengt
    genau das Muster ab, das wir in den echten Daten gefunden haben (z.B.
    33 ETFs am 06.08.2024 gleichzeitig ueber der Schwelle) und verhindert,
    dass ein einzelnes Marktereignis als viele unabhaengige Beobachtungen
    gezaehlt wird."""
    df = episoden_df.copy()
    df["datum"] = pd.to_datetime(df["datum"])
    eindeutige_daten = sorted(df["datum"].unique())

    cluster_id = 0
    datum_zu_cluster = {}
    voriges_datum = None
    for datum in eindeutige_daten:
        if voriges_datum is not None and (datum - voriges_datum).days > max_luecke_tage:
            cluster_id += 1
        datum_zu_cluster[datum] = cluster_id
        voriges_datum = datum

    df["cluster_id"] = df["datum"].map(datum_zu_cluster)
    return df


def geclusterte_kennzahlen(episoden_df, rendite_spalte, max_luecke_tage=CLUSTER_MAX_LUECKE_TAGE):
    """Berechnet eine Kennzahl, bei der jedes Marktereignis (Cluster aus
    cluster_zuweisen) GENAU EINMAL zaehlt: zuerst der Mittelwert INNERHALB
    jedes Clusters, danach der Mittelwert UEBER die Cluster. Ein Tag mit 33
    gleichzeitigen Signalen zaehlt damit wie EIN Ereignis, nicht wie 33 -
    das ist der konservativere, unabhaengigere Vergleichswert zur rohen
    Episoden-Statistik."""
    df = cluster_zuweisen(episoden_df, max_luecke_tage)
    valide = df.dropna(subset=[rendite_spalte])
    if valide.empty:
        return {
            "anzahl_cluster": 0,
            "anzahl_episoden_in_clustern": 0,
            "geclusterter_mittelwert_pct": None,
        }
    cluster_mittelwerte = valide.groupby("cluster_id")[rendite_spalte].mean()
    return {
        "anzahl_cluster": int(valide["cluster_id"].nunique()),
        "anzahl_episoden_in_clustern": int(len(valide)),
        "geclusterter_mittelwert_pct": round(float(cluster_mittelwerte.mean()), 2),
    }


def schwellen_sweep(df, schwellen=SCHWELLEN_SWEEP_BEREICH):
    """FRAGE 1: Welche Dip-Score-Schwelle ist optimal? Wertet fuer jede
    Kandidaten-Schwelle Trefferquote, Ø-Gewinn/-Verlust und den
    Erwartungswert aus - sowohl roh (pro Episode) als auch geclustert
    (pro unabhaengigem Marktereignis) - PLUS fuer jede Ziel-Rendite aus
    ZIEL_SCHWELLEN (2/3/5/7,5/10%) die Trefferquote innerhalb von
    MAX_TAGE_EXIT_SIMULATION Handelstagen. Das beantwortet direkt: 'Bei
    welcher Schwelle habe ich eine hohe Wahrscheinlichkeit auf mindestens
    +X%?' - fuer mehrere X gleichzeitig."""
    zeilen = []
    for schwelle in schwellen:
        episoden = episoden_zaehlen(df, schwelle=schwelle)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        if len(valide) == 0:
            zeile = {
                "schwelle": schwelle, "anzahl_episoden": 0, "anzahl_cluster": 0,
                "trefferquote_pct": None, "avg_gewinn_pct": None,
                "avg_verlust_pct": None, "erwartungswert_pct": None,
                "geclusterter_erwartungswert_pct": None,
            }
            for label, _ in ZIEL_SCHWELLEN:
                zeile[f"quote_{label}"] = None
            zeilen.append(zeile)
            continue

        rendite_spalte = valide[f"return_{HAUPT_VORSCHAU}t"]
        gewinner = rendite_spalte[rendite_spalte > 0]
        verlierer = rendite_spalte[rendite_spalte <= 0]
        trefferquote = len(gewinner) / len(valide) * 100
        avg_gewinn = gewinner.mean() if len(gewinner) > 0 else 0.0
        avg_verlust = verlierer.mean() if len(verlierer) > 0 else 0.0
        erwartungswert = rendite_spalte.mean()
        cluster_stats = geclusterte_kennzahlen(valide, f"return_{HAUPT_VORSCHAU}t")

        zeile = {
            "schwelle": schwelle,
            "anzahl_episoden": len(valide),
            "anzahl_cluster": cluster_stats["anzahl_cluster"],
            "trefferquote_pct": round(trefferquote, 1),
            "avg_gewinn_pct": round(avg_gewinn, 2),
            "avg_verlust_pct": round(avg_verlust, 2),
            "erwartungswert_pct": round(erwartungswert, 2),
            "geclusterter_erwartungswert_pct": cluster_stats["geclusterter_mittelwert_pct"],
        }
        for label, _ in ZIEL_SCHWELLEN:
            spalte = f"erreicht_{label}"
            if spalte in valide.columns and valide[spalte].notna().any():
                quote = valide[spalte].dropna().mean() * 100
                zeile[f"quote_{label}"] = round(quote, 1)
            else:
                zeile[f"quote_{label}"] = None
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


# ==========================================
# SCHRITT 3: EINZELFAKTOR-TESTS
# Gemeinsames Muster fuer alle vier Analysen: Episoden aus einer Bedingung
# bilden (episoden_aus_maske), dann dieselben Kennzahlen wie im Schwellen-
# Sweep berechnen (Trefferquote, Erwartungswert, geclusterter Erwartungs-
# wert, quote_Xpct) - damit alle Einzelfaktoren 1:1 vergleichbar sind.
# ==========================================

RSI_BINS = [(0, 20), (20, 25), (25, 30), (30, 35), (35, 40)]
DRAWDOWN_BINS = [(-100, -30), (-30, -20), (-20, -10), (-10, -5), (-5, 0)]
DRAWDOWN_ATR_BINS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 100)]
VOLUMEN_BINS = [(0, 0.8), (0.8, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 100)]


def _episoden_kennzahlen(valide, rendite_spalte=f"return_{HAUPT_VORSCHAU}t"):
    """Gemeinsame Kennzahlen-Berechnung fuer eine bereits gefilterte
    Episoden-Menge - von allen Einzelfaktor-Analysen genutzt."""
    zeile = {"anzahl_episoden": len(valide)}
    if len(valide) == 0:
        zeile["anzahl_cluster"] = 0
        return zeile
    rendite = valide[rendite_spalte].dropna()
    cluster_stats = geclusterte_kennzahlen(valide, rendite_spalte)
    zeile["anzahl_cluster"] = cluster_stats["anzahl_cluster"]
    if len(rendite) > 0:
        zeile["trefferquote_pct"] = round((rendite > 0).mean() * 100, 1)
        zeile["erwartungswert_pct"] = round(rendite.mean(), 2)
    zeile["geclusterter_erwartungswert_pct"] = cluster_stats["geclusterter_mittelwert_pct"]
    for label, _ in ZIEL_SCHWELLEN:
        spalte = f"erreicht_{label}"
        if spalte in valide.columns and valide[spalte].notna().any():
            zeile[f"quote_{label}"] = round(valide[spalte].dropna().mean() * 100, 1)
    return zeile


def rsi_bin_analyse(df, bins=RSI_BINS):
    """EINZELFAKTOR 1: RSI-Bereich isoliert (ChatGPT-Punkt 1) - unabhaengig
    von Trend/GD200, nur die reine RSI-Tiefe. Hat den RSI-Sweet-Spot bei
    25 im aktuellen Score begruendet."""
    zeilen = []
    for lo, hi in bins:
        maske = (df["rsi"] >= lo) & (df["rsi"] < hi)
        episoden = episoden_aus_maske(df, maske)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"rsi_bereich": f"{lo}-{hi}"}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def drawdown_bin_analyse(df, spalte="drawdown_20t_pct", bins=DRAWDOWN_BINS):
    """EINZELFAKTOR 2: Wie stark ist der Kurs VOR dem Tag gefallen
    (ChatGPT-Punkt 6) - testet, ob tiefere Dips bessere Rebounds oder eher
    fallende Messer sind. War das staerkste Einzelsignal im Backtest und
    ist mittlerweile selbst Score-Komponente (siehe dip_score.py) -
    `spalte` zeigt standardmaessig auf das vom Score gelieferte Feld,
    keine separate Berechnung mehr noetig."""
    zeilen = []
    for lo, hi in bins:
        maske = (df[spalte] >= lo) & (df[spalte] < hi)
        episoden = episoden_aus_maske(df, maske)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"drawdown_bereich_pct": f"{lo} bis {hi}"}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def drawdown_atr_bin_analyse(df, bins=DRAWDOWN_ATR_BINS):
    """EINZELFAKTOR 2b: ATR-normalisierter Rueckgang - Diagnose, ob die
    rohe Prozent-Tiefe (Einzelfaktor 2) teilweise nur einen Sektor-/
    Volatilitaets-Bias abbildet. Nicht Teil des Live-Scores, nur Test."""
    zeilen = []
    for lo, hi in bins:
        maske = (df["drawdown_atr_multiple"] >= lo) & (df["drawdown_atr_multiple"] < hi)
        episoden = episoden_aus_maske(df, maske)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"atr_vielfaches": f"{lo}-{hi}"}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def volumen_bin_analyse(df, bins=VOLUMEN_BINS):
    """EINZELFAKTOR 2c: Handelsvolumen am Signaltag relativ zum 20-Tage-
    Schnitt - testet die Kapitulations-Hypothese (hohes Volumen beim Dip
    = bessere Rebound-Chance). Bislang komplett ungetestet."""
    zeilen = []
    for lo, hi in bins:
        maske = (df["volumen_ratio"] >= lo) & (df["volumen_ratio"] < hi)
        episoden = episoden_aus_maske(df, maske)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"volumen_vielfaches": f"{lo}-{hi}"}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def trend_kreuz_analyse(df):
    """EINZELFAKTOR 4: Die zwei Teilbedingungen des Trend-Scores (EMA50 >
    GD200, GD200 steigt) einzeln UND gekreuzt betrachten - beide zeigten in
    der Gesamt-Korrelation ein leicht negatives Vorzeichen (-0.027), nie
    isoliert nachgeprueft. Testet, ob eine der beiden Bedingungen (oder
    ihre Kombination) fuer den Effekt verantwortlich ist."""
    zeilen = []
    for ema_ueber in [True, False]:
        for steigt in [True, False]:
            maske = (
                (df["ema50_ueber_gd200"] == ema_ueber)
                & (df["gd200_steigt"] == steigt)
            )
            episoden = episoden_aus_maske(df, maske)
            valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
            zeile = {
                "ema50>gd200": ema_ueber,
                "gd200_steigt": steigt,
            }
            zeile.update(_episoden_kennzahlen(valide))
            zeilen.append(zeile)
    return pd.DataFrame(zeilen)


GD200_PUFFER_BINS = [(-100, -5), (-5, 0), (0, 2), (2, 5), (5, 10), (10, 20), (20, 1000)]


def gd200_puffer_bin_analyse(df, bins=GD200_PUFFER_BINS):
    """EINZELFAKTOR 5: GD200-Puffer (Kurs relativ zur GD200) in feinen
    Stufen statt nur als gedeckelten Score - testet, ob ein GROSSER Puffer
    (weit ueber der GD200) tatsaechlich ein Sicherheitsmerkmal ist (wie
    aktuell im Score angenommen) oder eher "bereits weit gelaufen, ueberkauft
    auf laengerer Sicht" bedeutet, was die leicht negative Korrelation
    (-0.032) erklaeren wuerde."""
    zeilen = []
    for lo, hi in bins:
        maske = (df["gd200_puffer_pct"] >= lo) & (df["gd200_puffer_pct"] < hi)
        episoden = episoden_aus_maske(df, maske)
        valide = episoden.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"gd200_puffer_bereich_pct": f"{lo} bis {hi}"}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def jahres_robustheit(df, schwelle=KAUFSIGNAL_SCHWELLE):
    """EINZELFAKTOR 3: Traegt das Signal bei der AKTUELLEN Kaufsignal-
    Schwelle ueber mehrere Jahre, oder kommt die Performance nur aus
    1-2 Marktphasen (ChatGPT-Punkt 14)?"""
    episoden = episoden_zaehlen(df, schwelle=schwelle)
    episoden = episoden.copy()
    episoden["jahr"] = pd.to_datetime(episoden["datum"]).dt.year
    zeilen = []
    for jahr, gruppe in episoden.groupby("jahr"):
        valide = gruppe.dropna(subset=[f"return_{HAUPT_VORSCHAU}t"])
        zeile = {"jahr": int(jahr)}
        zeile.update(_episoden_kennzahlen(valide))
        zeilen.append(zeile)
    return pd.DataFrame(zeilen)


def einzelfaktor_analysen(df):
    print(f"\n{'=' * 60}")
    print("SCHRITT 3: EINZELFAKTOR-TESTS")
    print(f"{'=' * 60}")

    print("\n--- 1) RSI-Bereich isoliert ---")
    print(rsi_bin_analyse(df).to_string(index=False))

    print("\n--- 2) Kursrueckgang vor dem Tag (20-Tage-Hoch bis heute, Prozent) ---")
    print(drawdown_bin_analyse(df).to_string(index=False))

    print("\n--- 2b) Kursrueckgang ATR-normalisiert (Diagnose: Sektor-Bias?) ---")
    print(drawdown_atr_bin_analyse(df).to_string(index=False))

    print("\n--- 2c) Handelsvolumen relativ zum 20-Tage-Schnitt (Kapitulations-Test) ---")
    print(volumen_bin_analyse(df).to_string(index=False))

    print("\n--- 2d) Trend-Teilbedingungen gekreuzt (EMA50>GD200 x GD200 steigt) ---")
    print(trend_kreuz_analyse(df).to_string(index=False))

    print("\n--- 2e) GD200-Puffer in feinen Stufen (Sicherheitsmerkmal oder ueberkauft?) ---")
    print(gd200_puffer_bin_analyse(df).to_string(index=False))

    print(f"\n--- 3) Jahres-Robustheit bei Schwelle {KAUFSIGNAL_SCHWELLE:.0f} ---")
    print(jahres_robustheit(df).to_string(index=False))
    print("\n  Hinweis: Bei allen drei Tabellen gilt wie beim Schwellen-Sweep -")
    print("  'anzahl_cluster' ist die eigentliche unabhaengige Stichprobengroesse.")
    print("  Bei <~15-20 Clustern sind das Tendenzen, kein Beweis.")


def exit_analyse(df, serien_cache, schwelle=KAUFSIGNAL_SCHWELLE):
    """FRAGE 2: Haltedauer vs. Verkaufskurs. Fuer jede Episode bei der
    aktuellen Kaufsignal-Schwelle wird sowohl die Tag-fuer-Tag-Rendite-
    kurve als auch eure tatsaechliche T1/T2-Regel simuliert."""
    episoden = episoden_zaehlen(df, schwelle=schwelle)
    zeilen = []
    for _, ep in episoden.iterrows():
        serien = serien_cache.get(ep["ticker"])
        if serien is None:
            continue
        try:
            i = serien["close"].index.get_loc(pd.Timestamp(ep["datum"]))
        except KeyError:
            continue
        if isinstance(i, slice) or hasattr(i, "__len__"):
            continue  # doppelte Zeitstempel -> ueberspringen, sollte nicht vorkommen

        kurve = rendite_kurve(serien["close"], i)
        t1t2 = simuliere_t1_t2_regel(serien["close"], serien["ema50"], serien["high_252"], i)

        zeilen.append({
            "sektor": ep["sektor"], "isin": ep["isin"], "ticker": ep["ticker"],
            "datum": ep["datum"], "dip_score": ep["dip_score"],
            **kurve, **t1t2,
        })
    return pd.DataFrame(zeilen)


def episoden_tier(df, tier):
    """Episoden fuer eine Signalstufe: 'soft' (SOFT_KAUFSIGNAL_SCHWELLE bis
    knapp unter KAUFSIGNAL_SCHWELLE) oder 'voll' (>= KAUFSIGNAL_SCHWELLE)."""
    if tier == "voll":
        maske = df["dip_score"] >= KAUFSIGNAL_SCHWELLE
    elif tier == "soft":
        maske = (df["dip_score"] >= SOFT_KAUFSIGNAL_SCHWELLE) & (df["dip_score"] < KAUFSIGNAL_SCHWELLE)
    else:
        raise ValueError("tier muss 'soft' oder 'voll' sein")
    return episoden_aus_maske(df, maske)


def _episode_index(serien_cache, ep):
    """Findet die iloc-Position einer Episode in der gecachten Kursreihe
    ihres Tickers, oder None falls nicht auffindbar (Datenluecke o.ae.)."""
    serien = serien_cache.get(ep["ticker"])
    if serien is None:
        return None, None
    try:
        i = serien["close"].index.get_loc(pd.Timestamp(ep["datum"]))
    except KeyError:
        return None, None
    if isinstance(i, slice) or hasattr(i, "__len__"):
        return None, None
    return serien, i


RENDITE_PROFIL_TAGE = [1, 3, 5, 7, 10, 15, 21, 30, 40]


def rendite_kurve_tage(close, i, tage_liste=RENDITE_PROFIL_TAGE):
    """Rendite ab Tag i (Index-Position) fuer eine feste Liste von
    Haltedauern (z.B. 1/3/5/7/10/15/21/30/40 Tage) - Basis fuer das
    Renditeprofil nach Signalstufe."""
    ergebnis = {}
    for tag in tage_liste:
        idx = i + tag
        if idx >= len(close):
            ergebnis[f"rendite_tag{tag}"] = None
            continue
        rendite = ((close.iloc[idx] - close.iloc[i]) / close.iloc[i]) * 100
        ergebnis[f"rendite_tag{tag}"] = round(rendite, 2)
    return ergebnis


def renditeprofil_nach_tagen(df, serien_cache, tier, tage_liste=RENDITE_PROFIL_TAGE):
    """Fuer eine Signalstufe (soft/voll): Ø Rendite, Median und
    Trefferquote nach 1 Tag, 3 Tagen, ..., 40 Tagen - beantwortet direkt
    'welche Rendite ist bei diesem Signal nach welchem Zeitraum im
    Schnitt zu erwarten'. Gedacht als Datengrundlage fuer die
    aufklappbare Backtest-Erkenntnisse-Box in app.py."""
    episoden = episoden_tier(df, tier)
    zeilen_pro_episode = []
    for _, ep in episoden.iterrows():
        serien, i = _episode_index(serien_cache, ep)
        if serien is None:
            continue
        zeilen_pro_episode.append(rendite_kurve_tage(serien["close"], i, tage_liste))

    if not zeilen_pro_episode:
        return pd.DataFrame(columns=["tag", "anzahl", "avg_rendite_pct", "median_rendite_pct", "trefferquote_pct"])

    profil_df = pd.DataFrame(zeilen_pro_episode)
    zeilen = []
    for tag in tage_liste:
        spalte = f"rendite_tag{tag}"
        if spalte not in profil_df.columns:
            continue
        werte = profil_df[spalte].dropna()
        if len(werte) == 0:
            zeilen.append({"tag": tag, "anzahl": 0, "avg_rendite_pct": None,
                            "median_rendite_pct": None, "trefferquote_pct": None})
            continue
        zeilen.append({
            "tag": tag,
            "anzahl": len(werte),
            "avg_rendite_pct": round(werte.mean(), 2),
            "median_rendite_pct": round(werte.median(), 2),
            "trefferquote_pct": round((werte > 0).mean() * 100, 1),
        })
    return pd.DataFrame(zeilen)


def ziel_exit_sweep(df, serien_cache, tier, ziel_kandidaten):
    """Testet mehrere feste Ziel-Renditen fuer eine Signalstufe und
    vergleicht Erreichquote, Haltedauer und tatsaechliche Rendite."""
    episoden = episoden_tier(df, tier)
    zeilen = []
    for ziel_pct in ziel_kandidaten:
        ergebnisse = []
        for _, ep in episoden.iterrows():
            serien, i = _episode_index(serien_cache, ep)
            if serien is None:
                continue
            r = simuliere_prozent_ziel_exit(serien["close"], serien["high"], i, ziel_pct)
            r["datum"] = ep["datum"]
            ergebnisse.append(r)

        edf = pd.DataFrame(ergebnisse)
        if edf.empty:
            zeilen.append({"ziel_pct": ziel_pct, "anzahl_episoden": 0})
            continue

        cluster_stats = geclusterte_kennzahlen(edf, "gesamt_rendite_pct")
        erreicht = edf[edf["exit_typ"] == "Ziel_erreicht"]
        zeilen.append({
            "ziel_pct": ziel_pct,
            "anzahl_episoden": len(edf),
            "anzahl_cluster": cluster_stats["anzahl_cluster"],
            "erreichquote_pct": round(len(erreicht) / len(edf) * 100, 1),
            "avg_tage_bis_ziel": (
                round(erreicht["haltedauer_tage"].mean(), 1) if len(erreicht) > 0 else None
            ),
            "avg_gesamtrendite_pct": round(edf["gesamt_rendite_pct"].mean(), 2),
            "geclusterte_rendite_pct": cluster_stats["geclusterter_mittelwert_pct"],
        })
    return pd.DataFrame(zeilen)


def exit_regel_vergleich(df, serien_cache):
    """Beantwortet: EMA50/52W-Hoch als Ziel (alte Regel) oder feste
    Zielrendite je Signalstufe - was schneidet in den Daten besser ab?"""
    print(f"\n{'=' * 60}")
    print("EXIT-REGEL-VERGLEICH: EMA50-Regel vs. feste Zielrendite")
    print(f"{'=' * 60}")

    ziel_kandidaten = {
        "soft": [3.0, 4.0, 5.0, 6.0, 7.0],
        "voll": [7.0, 8.0, 10.0, 12.0, 15.0],
    }
    labels = {
        "soft": f"Softes Signal ({SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE - 1:.0f})",
        "voll": f"Volles Signal (>= {KAUFSIGNAL_SCHWELLE:.0f})",
    }

    for tier in ["soft", "voll"]:
        episoden = episoden_tier(df, tier)
        print(f"\n--- {labels[tier]}: {len(episoden)} Episoden ---")

        alte_ergebnisse = []
        for _, ep in episoden.iterrows():
            serien, i = _episode_index(serien_cache, ep)
            if serien is None:
                continue
            r = simuliere_t1_t2_regel(serien["close"], serien["ema50"], serien["high_252"], i)
            r["datum"] = ep["datum"]
            alte_ergebnisse.append(r)
        alte_df = pd.DataFrame(alte_ergebnisse)

        if not alte_df.empty:
            alte_cluster = geclusterte_kennzahlen(alte_df, "gesamt_rendite_pct")
            print(f"\n  Alte Regel (EMA50 / 52W-Hoch / dynamischer Stop):")
            print(f"    Ø Rendite: {alte_df['gesamt_rendite_pct'].mean():+.2f}% "
                  f"(geclustert: {alte_cluster['geclusterter_mittelwert_pct']}%)")
            print(f"    Ø Haltedauer: {alte_df['haltedauer_tage'].mean():.1f} Tage, "
                  f"n={len(alte_df)}, Cluster={alte_cluster['anzahl_cluster']}")
        else:
            print("\n  Alte Regel: keine auswertbaren Episoden.")

        print(f"\n  Feste Zielrenditen im Vergleich:")
        sweep = ziel_exit_sweep(df, serien_cache, tier, ziel_kandidaten[tier])
        print(sweep.to_string(index=False))

        print(f"\n  Renditeprofil nach Haltedauer (unabhängig von jeder Exit-Regel -")
        print(f"  einfach 'was ist nach X Tagen im Schnitt drin'):")
        profil = renditeprofil_nach_tagen(df, serien_cache, tier)
        print(profil.to_string(index=False))

    print("\n  Hinweis: 'geclusterte_rendite_pct' ist die robustere Zahl bei")
    print("  zeitlich gehaeuften Signalen (siehe Schwellen-Sweep-Hinweis oben).")


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
    bins = sorted(set([0, 20, 40, 60, KAUFSIGNAL_SCHWELLE, 100]))
    df["score_bucket"] = pd.cut(df["dip_score"], bins=bins, include_lowest=True)
    bucket_stats = df.groupby("score_bucket", observed=True)[f"return_{HAUPT_VORSCHAU}t"].agg(
        ["mean", "count"]
    )
    print(bucket_stats)

    print("\nKorrelation der einzelnen Score-Komponenten mit dem Forward-Return:")
    for komponente in ["rsi_score", "trend_score", "gd200_score", "ema50_score", "drawdown_score"]:
        k_df = df[[komponente, f"return_{HAUPT_VORSCHAU}t"]].dropna()
        if len(k_df) > 1:
            k = k_df.corr().iloc[0, 1]
            print(f"  {komponente}: {k:.3f}")

    print(f"\n{'-' * 60}")
    print(f"FRAGE 1: Welche Dip-Score-Schwelle ist optimal?")
    print(f"{'-' * 60}")
    sweep = schwellen_sweep(df)
    print(sweep.to_string(index=False))
    print("\n  Hinweis: 'erwartungswert_pct' ist der Ø-Return ueber ALLE Episoden dieser")
    print("  Schwelle. 'geclusterter_erwartungswert_pct' fasst zeitlich nah beieinander")
    print("  liegende Episoden UEBER ALLE TICKER HINWEG zu einem Marktereignis zusammen")
    print("  (z.B. zaehlt ein Tag mit 33 gleichzeitigen Signalen als 1 statt 33) - das")
    print("  ist die robustere, aber konservativere Zahl. 'anzahl_cluster' ist die")
    print("  eigentliche unabhaengige Stichprobengroesse, nicht 'anzahl_episoden'.")
    print(f"\n  'quote_Xpct' = Anteil der Episoden dieser Schwelle, die +X% innerhalb von")
    print(f"  {MAX_TAGE_EXIT_SIMULATION} Handelstagen erreicht haben (nicht auf {HAUPT_VORSCHAU} Tage")
    print("  begrenzt wie die anderen Kennzahlen) - direkte Antwort auf 'bei welcher")
    print("  Schwelle ist die Wahrscheinlichkeit auf +X% hoch?'.")

    return sweep


def main():
    print("Lade Marktregime-Historie...")
    regime_serie = berechne_regime_serie()

    print("\n--- HAUPT-BACKTEST: euer eigenes ETF-Universum ---")
    etfs = parse_isin_file()
    if not etfs:
        print(f"Keine ETFs in {ISIN_DATEI} gefunden. Skript abgebrochen.")
        sys.exit(1)

    alle_ergebnisse = []
    serien_cache = {}
    gesamt_anzahl = len(etfs)
    for idx, item in enumerate(etfs, start=1):
        ticker = item.get("ticker")
        if not ticker:
            print(f"  [{idx}/{gesamt_anzahl}] Uebersprungen (kein Ticker in {ISIN_DATEI}): {item['isin']}")
            continue
        print(f"  [{idx}/{gesamt_anzahl}] Backteste {ticker} ({item['isin']})...")
        ergebnisse, serien = analysiere_etf(item["isin"], ticker, item["sektor"], regime_serie)
        alle_ergebnisse.extend(ergebnisse)
        if serien is not None:
            serien_cache[ticker] = serien
        time.sleep(API_PAUSE_SEKUNDEN)

    if not alle_ergebnisse:
        print("Keine auswertbaren Ergebnisse (zu kurze Historie ueberall?). Abbruch.")
        sys.exit(1)

    df = pd.DataFrame(alle_ergebnisse)
    df.to_csv("backtest_ergebnisse.csv", index=False)
    print(f"\n{len(df)} ETF-Tage gespeichert in backtest_ergebnisse.csv")

    zusammenfassung(df, label="- Euer ETF-Universum")
    einzelfaktor_analysen(df)

    print(f"\n{'-' * 60}")
    print(f"FRAGE 2: Haltedauer vs. Verkaufskurs (Episoden bei Schwelle {KAUFSIGNAL_SCHWELLE:.0f})")
    print(f"{'-' * 60}")
    exit_df = exit_analyse(df, serien_cache)
    if exit_df.empty:
        print("Keine Episoden bei der aktuellen Schwelle gefunden - Exit-Analyse übersprungen.")
    else:
        exit_df.to_csv("backtest_exit_analyse.csv", index=False)
        print(f"{len(exit_df)} Episoden analysiert, gespeichert in backtest_exit_analyse.csv")

        exit_cluster_stats = geclusterte_kennzahlen(exit_df, "gesamt_rendite_pct")
        print(f"Davon unabhaengige Marktereignisse (Cluster): {exit_cluster_stats['anzahl_cluster']}")
        print(f"Geclusterte Ø Gesamtrendite: {exit_cluster_stats['geclusterter_mittelwert_pct']}%\n")

        def _stat(spalte):
            s = exit_df[spalte].dropna()
            if len(s) == 0:
                return "n/a"
            return f"Ø {s.mean():+.2f}% (n={len(s)})"

        print("Feste Haltedauer (unabhaengig von Trendindikatoren):")
        print(f"  Nach 7 Tagen verkaufen:  {_stat('rendite_tag7')}")
        print(f"  Nach 21 Tagen verkaufen: {_stat('rendite_tag21')}")

        print("\nEure aktuelle T1/T2-Regel (simuliert: EMA50 / 52W-Hoch / dynamischer Stop):")
        print(f"  Ø Gesamtrendite: {_stat('gesamt_rendite_pct')}")
        haltedauer = exit_df["haltedauer_tage"].dropna()
        if len(haltedauer) > 0:
            print(f"  Ø Haltedauer bis Exit: {haltedauer.mean():.1f} Tage")
        print("  Exit-Typ-Verteilung:")
        for typ, anzahl in exit_df["exit_typ"].value_counts().items():
            print(f"    {typ}: {anzahl} ({anzahl / len(exit_df) * 100:.1f}%)")

        print("\nTheoretisches Optimum (im Nachhinein bester Tag je Episode - nicht handelbar,")
        print("dient nur als Obergrenze):")
        print(f"  Bester Tag nach Gesamtrendite: {_stat('bester_rendite_gesamt')}")
        bt_tag = exit_df["bester_tag_gesamt"].dropna()
        if len(bt_tag) > 0:
            print(f"    Ø an Tag {bt_tag.mean():.1f}")
        print(f"  Bester Tag nach Rendite/Tag (Kapital-Effizienz): {_stat('beste_rendite_pro_tag')}")
        bt_eff = exit_df["bester_tag_effizienz"].dropna()
        if len(bt_eff) > 0:
            print(f"    Ø an Tag {bt_eff.mean():.1f}")

    exit_regel_vergleich(df, serien_cache)

    print("\n\n--- ZUSATZVALIDIERUNG: etablierte, langjaehrige Sektor-ETFs (USD) ---")
    zusatz_ergebnisse = []
    for idx, ticker in enumerate(ZUSATZVALIDIERUNG_TICKER, start=1):
        print(f"  [{idx}/{len(ZUSATZVALIDIERUNG_TICKER)}] Backteste {ticker}...")
        ergebnisse, _ = analysiere_etf(ticker, ticker, "Zusatzvalidierung", regime_serie)
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
