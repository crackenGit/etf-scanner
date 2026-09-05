"""
dip_score.py

Gemeinsame Score-Logik für den ETF Dip-Scanner (app.py) und das
Backtesting-Skript (backtest.py). Einzige Quelle der Wahrheit für die
Score-Formel, damit die Live-App und der Backtest garantiert exakt
dasselbe berechnen - keine Duplikation, kein Auseinanderdriften.

Stand nach Backtest-Auswertung (277.000+ ETF-Tage, Einzelfaktor-Tests):
- RSI-Score: von "je tiefer desto besser" auf einen Sweet Spot bei RSI 25
  umgestellt - RSI<20 performte im Backtest SCHLECHTER als RSI 20-30
  (vermutlich eher Crash-Signal als normaler Dip).
- Turnaround-Score: ENTFERNT. Zeigte in drei unabhängigen Tests
  (Korrelation, grobe Buckets, isolierte Quote-Analyse) durchgehend
  keine Vorhersagekraft, trotz dreimaliger Lockerung der Formel.
- Kursrückgang-Tiefe: ATR-NORMALISIERT statt roher Prozentwert. Der rohe
  Prozent-Rückgang war zwar das stärkste Einzelsignal, aber ein Test mit
  ATR-Normierung zeigte: ein großer Teil davon war Sektor-/Volatilitäts-
  Bias (rohe -30%-Rückgänge kommen fast nur bei volatilen Sektoren wie
  Halbleiter/AI Hardware vor, die im Backtest-Zeitraum ohnehin besser
  liefen). Nach ATR-Normierung bleibt ein kleinerer, aber sauberer und
  sektor-fairer Effekt übrig - dafür ist die Kaufsignal-Schwelle nach
  dieser Umstellung neu zu validieren (siehe Chat).

Enthält bewusst KEINE Streamlit- oder yfinance-Abhängigkeiten, damit es
sich auch von einem reinen Kommandozeilen-Skript (backtest.py) ohne
Streamlit-Kontext importieren lässt.
"""

import pandas as pd

# ==========================================
# KONFIGURATION (identisch zu app.py - dort werden dieselben Werte
# verwendet; falls in app.py angepasst, hier synchron halten)
# ==========================================
KAUFSIGNAL_SCHWELLE = 75.0        # "volles" Kaufsignal - Re-Backtest nach ATR-Umbau:
                                   # quote_10pct 41,7% bei 70 -> 51,9% bei 75 (212 Ep./39 Cluster),
                                   # löst nebenbei auch die Gesundheit-Sektor-Frage strukturell
                                   # (Gesundheit fällt bei 75 unter n=10, filtert sich von selbst raus)
SOFT_KAUFSIGNAL_SCHWELLE = 65.0   # "softes" Kaufsignal - mit angehoben, um sinnvollen Abstand
                                   # zur neuen KAUFSIGNAL_SCHWELLE (75) zu halten
ZIEL_RENDITE_SOFT_PCT = 4.0       # Exit-Ziel für softes Signal - VOR der Schwellen-Anhebung
                                   # kalibriert, nach Re-Backtest mit den neuen Schwellen
                                   # nochmal zu pruefen (siehe Chat)
ZIEL_RENDITE_VOLL_PCT = 7.0       # Exit-Ziel für volles Signal - dito, noch zu validieren
RSI_WATCHLIST_SCHWELLE = 40.0
DRAWDOWN_SCORE_MAX = 30.0         # Cap fuer die Rueckgang-Komponente (siehe
                                   # score_am_punkt) - als Konstante, damit
                                   # Formel und Anzeige (app.py) nie auseinanderlaufen
MARKT_BENCHMARK_TICKER = "URTH"  # Breiter Referenzindex (iShares MSCI World). Alternative: "^STOXX" (Europa)

# Sektor-spezifischer Aufschlag (in Punkten) auf KAUFSIGNAL_SCHWELLE/
# SOFT_KAUFSIGNAL_SCHWELLE. Aktuell bewusst LEER - wird erst nach dem
# Re-Backtest mit der neuen ATR-Formel befuellt, sobald klar ist, welche
# Sektoren bei fairer (ATR-normalisierter) Messung tatsaechlich seltener
# die Zielrenditen erreichen. Siehe Chat fuer die Diskussion dazu
# (Gesundheit/Konsumgueter als Kandidaten).
SEKTOR_SCHWELLEN_AUFSCHLAG = {
    # "Gesundheit": 10.0,
    # "Konsumgüter": 15.0,
}


def berechne_indikator_serien(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.DataFrame:
    """
    Berechnet alle zeitreihenbasierten Indikatoren EINMAL, vektorisiert,
    für die komplette übergebene Kursreihe. Sowohl die Live-App (nur der
    letzte Wert zählt) als auch der Backtest (jeder Tag zählt) rufen dies
    einmal pro ETF auf und lesen danach nur noch Positionen aus.

    close/high/low müssen denselben, chronologisch aufsteigend sortierten
    DatetimeIndex besitzen und bereits NaN-bereinigt sein.
    """
    # min_periods=190 statt 200: toleriert kleinere Datenluecken (z.B.
    # wochenendbedingte oder temporaere Yahoo-Datenprobleme) innerhalb des
    # Fensters, ohne die GD200 bei jeder einzelnen fehlenden Zeile komplett
    # zu verwerfen. 190/200 = 95% Abdeckung noetig, echte Kurzhistorie faellt
    # weiterhin sauber durch.
    gd200 = close.rolling(window=200, min_periods=190).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

    gd200_vor_10d = gd200.shift(10)
    gd200_steigt = gd200 > gd200_vor_10d

    # Kursrückgang vom 20-Tage-Hoch bis heute (negativ oder 0) - roh, nur
    # noch zur Anzeige/Info, nicht mehr Grundlage des Scores (siehe unten)
    hoch_20t = close.rolling(window=20, min_periods=1).max()
    drawdown_20t_pct = ((close - hoch_20t) / hoch_20t) * 100

    # ATR(14) - volatilitätsnormalisiert die Rückgangs-Komponente, damit
    # ruhige und volatile ETFs fair verglichen werden (siehe Chat: rohe
    # Prozent-Rückgänge waren stark sektor-/volatilitätsverzerrt)
    prev_close = close.shift(1)
    true_range = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = true_range.rolling(window=14).mean()
    drawdown_atr_multiple = (hoch_20t - close) / atr14

    return pd.DataFrame({
        "close": close,
        "high": high,
        "low": low,
        "gd200": gd200,
        "gd200_vor_10d": gd200_vor_10d,
        "gd200_steigt": gd200_steigt,
        "ema50": ema50,
        "rsi": rsi,
        "avg_gain": avg_gain,
        "avg_loss": avg_loss,
        "drawdown_20t_pct": drawdown_20t_pct,
        "drawdown_atr_multiple": drawdown_atr_multiple,
    })


def score_am_punkt(indikatoren: pd.DataFrame, i: int, regime_ok: bool = True) -> dict:
    """
    Berechnet den Dip Score und alle Teil-Scores für die Zeile `i` (iloc-
    Position) von `indikatoren` (Ergebnis von berechne_indikator_serien).

    `i` kann -1 sein (letzter/heutiger Wert, wie in der Live-App) oder ein
    beliebiger historischer Index (Backtest) - die Funktion verwendet
    ausschließlich Werte bis einschließlich Position `i`, es gibt keinen
    Blick in die Zukunft.

    `regime_ok` kommt bewusst von außen (Marktregime ist ein globaler,
    tagesbezogener Zustand, kein ETF-spezifischer - im Backtest wird er
    einmal pro Datum über die Benchmark-Historie bestimmt).
    """
    row = indikatoren.iloc[i]

    c_today = float(row["close"])
    rsi_today = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0
    gd200_heute = float(row["gd200"]) if not pd.isna(row["gd200"]) else 0.0
    ema50_heute = float(row["ema50"]) if not pd.isna(row["ema50"]) else c_today
    gd200_steigt = bool(row["gd200_steigt"]) if not pd.isna(row["gd200_steigt"]) else False
    drawdown_20t_pct = (
        float(row["drawdown_20t_pct"]) if not pd.isna(row["drawdown_20t_pct"]) else 0.0
    )
    drawdown_atr_multiple = (
        float(row["drawdown_atr_multiple"])
        if not pd.isna(row["drawdown_atr_multiple"])
        else 0.0
    )

    # 1) RSI-Sweet-Spot (max. 20 Punkte): Peak bei RSI 25, faellt zu beiden
    #    Seiten linear ab. Backtest zeigte RSI<20 schlechter als RSI 20-30 -
    #    vermutlich eher Crash-Signal als normaler, kaufbarer Dip.
    rsi_peak = 25.0
    rsi_score = max(0.0, 20.0 - abs(rsi_today - rsi_peak) * 0.8)

    # 2) Trend-STRUKTUR (max. 15 Punkte) - Richtung UMGEKEHRT ggue. der
    #    urspruenglichen Annahme. Backtest zeigte sauber monoton UND
    #    unabhaengig vom Rueckgang (innerhalb gleicher ATR-Bins geprueft):
    #    "Trend voll intakt" (EMA50>GD200 + GD200 steigt) hatte die
    #    SCHLECHTESTE Zielrenditen-Quote alle Kombinationen (21.8% quote_10pct
    #    im 4+-ATR-Bin), "Trend komplett gebrochen" die BESTE (33.9%) - ein
    #    ETF mit bereits gebrochener laengerfristiger Trendstruktur hat
    #    vermutlich einen "reiferen", weiter fortgeschrittenen Rueckgang
    #    hinter sich (naeher am Boden) als einer, der nur kurz aus einem
    #    intakten Aufwaertstrend heraus dippt. Bricht mit klassischer TA-
    #    Weisheit, aber gut durch die Daten gestuetzt (siehe Chat).
    trend_score = (7.5 if ema50_heute < gd200_heute else 0.0) + (
        7.5 if not gd200_steigt else 0.0
    )

    # 3) Abstand zur GD200 (max. 15 Punkte) - von linear "je hoeher desto
    #    besser" auf ABSOLUTEN Abstand (Richtung egal) umgestellt. Backtest
    #    zeigte eine U-Form statt einer Linie, ebenfalls unabhaengig vom
    #    Rueckgang bestaetigt: sowohl weit UNTER der GD200 (~40-45%
    #    quote_10pct) als auch weit UEBER ihr (~33-38%) schneiden deutlich
    #    besser ab als der bisherige "Sweet Spot" bei +5 bis +10% (~15-24%,
    #    der schwaechste Bereich ueberhaupt). Downside-Risiko (max.
    #    Zwischenzeit-Ruckgang) wurde fuer beide Extreme geprueft und ist
    #    vergleichbar - keine Sonderbehandlung fuer eine Richtung noetig.
    gd200_buffer_pct = (
        ((c_today - gd200_heute) / gd200_heute) * 100 if gd200_heute else 0.0
    )
    gd200_score = min(15.0, abs(gd200_buffer_pct) * 1.0)

    # 4) Mean-Reversion-Potenzial bis zur EMA50 (max. 20 Punkte - erhöht,
    #    da staerkste nachgewiesene Einzelkorrelation, 0.090)
    ema50_upside_pct = (
        max(0.0, ((ema50_heute - c_today) / c_today) * 100) if c_today else 0.0
    )
    ema50_score = min(20.0, ema50_upside_pct * 1.6)

    # 5) Kursrückgang-Tiefe vor dem Signal (max. 30 Punkte - jetzt ATR-
    #    normalisiert statt roher Prozentwert. Ein Test zeigte: der rohe
    #    Prozentwert war stark sektor-/volatilitaetsverzerrt (rohe -30%-
    #    Rueckgaenge kommen fast nur bei volatilen Sektoren vor) - nach
    #    ATR-Normierung bleibt ein echter, aber deutlich schwaecherer
    #    Effekt (Korrelation 0.015 statt der aufgeblaehten 0.078 roh),
    #    deshalb auch das Gewicht von 35 auf 30 gesenkt zugunsten von
    #    EMA50-Potenzial oben. Cap bei 6 ATR (deckt sich mit dem Punkt,
    #    an dem der Effekt bei den meisten Sektoren saettigt/kippt).
    drawdown_magnitude = max(0.0, drawdown_atr_multiple)
    drawdown_score = min(DRAWDOWN_SCORE_MAX, drawdown_magnitude * 5.0)

    basis_score = rsi_score + trend_score + gd200_score + ema50_score  # max. 70
    dip_score_roh = basis_score + drawdown_score  # max. 100

    # Marktregime-Malus ENTFERNT (frueher hier: ×0.8 auf den gesamten Score,
    # wenn der Referenzindex unter seiner eigenen GD200 lag). Ein gezielter
    # Nachtest (bei GLEICHEM Rohscore, Regime OK vs. nicht OK verglichen)
    # zeigte: die fuer uns eigentlich relevante Zielrenditen-Quote war bei
    # schlechtem Regime nicht schlechter, in den meisten Rohscore-Bereichen
    # sogar leicht BESSER, und die Zielerreichung ging im Schnitt sogar
    # SCHNELLER (nicht langsamer wie zunaechst vermutet). Nur eine an sich
    # nicht handelsrelevante Nebenmetrik (Trefferquote nach fixen 21 Tagen,
    # nicht Teil unserer echten zielbasierten Exit-Regel) sprach fuer den
    # Malus - siehe Chat. Regime-Status bleibt als reine Information erhalten
    # (regime_ok unten), beeinflusst den Score aber nicht mehr.
    #
    # GD200-Bruch-Malus ENTFERNT (frueher hier: Punktabzug von bis zu 40%,
    # wenn der Kurs unter der GD200 lag). Basierte auf der Annahme "unter
    # GD200 = fallendes Messer, bestrafen" - genau das Gegenteil dessen, was
    # der Backtest jetzt zeigt (siehe trend_score/gd200_score oben). Haette
    # der neuen, belegten Logik direkt widersprochen - kein Kompromiss
    # moeglich, deshalb komplett gestrichen statt abgeschwaecht.

    dip_score = round(dip_score_roh, 1)

    return {
        "close": c_today,
        "rsi": round(rsi_today, 1),
        "gd200": gd200_heute,
        "ema50": ema50_heute,
        "gd200_steigt": gd200_steigt,
        "drawdown_20t_pct": round(drawdown_20t_pct, 2),
        "drawdown_atr_multiple": round(drawdown_atr_multiple, 2),
        "rsi_score": round(rsi_score, 1),
        "trend_score": round(trend_score, 1),
        "gd200_score": round(gd200_score, 1),
        "ema50_score": round(ema50_score, 1),
        "drawdown_score": round(drawdown_score, 1),
        "regime_ok": regime_ok,
        "dip_score": dip_score,
    }


def signal_stufe(
    dip_score: float,
    sektor: str = None,
    soft_schwelle: float = SOFT_KAUFSIGNAL_SCHWELLE,
    voll_schwelle: float = KAUFSIGNAL_SCHWELLE,
) -> str:
    """Klassifiziert einen Dip Score in eine von drei Signalstufen.

    `sektor` ist optional: falls angegeben, wird ein sektor-spezifischer
    Aufschlag aus SEKTOR_SCHWELLEN_AUFSCHLAG addiert (aktuell leer/neutral
    fuer alle Sektoren - wird erst nach dem Re-Backtest mit der neuen
    ATR-Formel mit echten Werten befuellt, siehe Chat: Gesundheit/
    Konsumgueter zeigten selbst bei fairer ATR-Messung schwaechere
    Zielrenditen-Quoten als z.B. Verteidigung/Cloud/Halbleiter).
    Ohne `sektor` (Standard) verhaelt sich die Funktion wie bisher."""
    aufschlag = SEKTOR_SCHWELLEN_AUFSCHLAG.get(sektor, 0.0) if sektor else 0.0
    effektive_voll = voll_schwelle + aufschlag
    effektive_soft = soft_schwelle + aufschlag
    if dip_score >= effektive_voll:
        return "voll"
    elif dip_score >= effektive_soft:
        return "soft"
    return "kein"


def effektive_schwelle(sektor: str = None, basis_schwelle: float = KAUFSIGNAL_SCHWELLE) -> float:
    """Liefert die fuer einen Sektor tatsaechlich geltende Kaufsignal-
    Schwelle (Basis-Schwelle + evtl. sektor-spezifischer Aufschlag).
    Nuetzlich fuer die Anzeige ('aktueller Score / benoetigte Punkte')."""
    aufschlag = SEKTOR_SCHWELLEN_AUFSCHLAG.get(sektor, 0.0) if sektor else 0.0
    return basis_schwelle + aufschlag
