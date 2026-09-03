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
- Kursrückgang-Tiefe (drawdown_20t_pct): NEU aufgenommen, ersetzt den
  Turnaround-Score im Punktbudget. War das mit Abstand stärkste
  Einzelsignal im Backtest (quote_10pct stieg sauber von 23% auf 80%
  mit zunehmender Rückgangstiefe) - "größerer Dip = größerer Rebound"
  statt "fallendes Messer" bestätigt sich hier klar.

Enthält bewusst KEINE Streamlit- oder yfinance-Abhängigkeiten, damit es
sich auch von einem reinen Kommandozeilen-Skript (backtest.py) ohne
Streamlit-Kontext importieren lässt.
"""

import pandas as pd

# ==========================================
# KONFIGURATION (identisch zu app.py - dort werden dieselben Werte
# verwendet; falls in app.py angepasst, hier synchron halten)
# ==========================================
KAUFSIGNAL_SCHWELLE = 70.0        # "volles" Kaufsignal - Backtest-Ziel ~10%+
SOFT_KAUFSIGNAL_SCHWELLE = 60.0   # "softes" Kaufsignal - Backtest-Ziel ~5%+
RSI_WATCHLIST_SCHWELLE = 40.0
REGIME_MALUS_FAKTOR = 0.8
MARKT_BENCHMARK_TICKER = "URTH"  # Breiter Referenzindex (iShares MSCI World). Alternative: "^STOXX" (Europa)


def berechne_indikator_serien(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.DataFrame:
    """
    Berechnet alle zeitreihenbasierten Indikatoren EINMAL, vektorisiert,
    für die komplette übergebene Kursreihe. Sowohl die Live-App (nur der
    letzte Wert zählt) als auch der Backtest (jeder Tag zählt) rufen dies
    einmal pro ETF auf und lesen danach nur noch Positionen aus.

    close/high/low müssen denselben, chronologisch aufsteigend sortierten
    DatetimeIndex besitzen und bereits NaN-bereinigt sein.
    """
    gd200 = close.rolling(window=200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

    gd200_vor_10d = gd200.shift(10)
    gd200_steigt = gd200 > gd200_vor_10d

    # Kursrückgang vom 20-Tage-Hoch bis heute (negativ oder 0)
    hoch_20t = close.rolling(window=20, min_periods=1).max()
    drawdown_20t_pct = ((close - hoch_20t) / hoch_20t) * 100

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

    # 1) RSI-Sweet-Spot (max. 20 Punkte): Peak bei RSI 25, faellt zu beiden
    #    Seiten linear ab. Backtest zeigte RSI<20 schlechter als RSI 20-30 -
    #    vermutlich eher Crash-Signal als normaler, kaufbarer Dip.
    rsi_peak = 25.0
    rsi_score = max(0.0, 20.0 - abs(rsi_today - rsi_peak) * 0.8)

    # 2) Trend intakt: EMA50 > GD200 UND GD200 steigt (max. 15 Punkte)
    trend_score = (7.5 if ema50_heute > gd200_heute else 0.0) + (
        7.5 if gd200_steigt else 0.0
    )

    # 3) Sicherheitspuffer über GD200 (max. 15 Punkte)
    gd200_buffer_pct = (
        ((c_today - gd200_heute) / gd200_heute) * 100 if gd200_heute else 0.0
    )
    gd200_score = min(15.0, max(0.0, gd200_buffer_pct) * 0.9)

    # 4) Mean-Reversion-Potenzial bis zur EMA50 (max. 15 Punkte)
    ema50_upside_pct = (
        max(0.0, ((ema50_heute - c_today) / c_today) * 100) if c_today else 0.0
    )
    ema50_score = min(15.0, ema50_upside_pct * 1.2)

    # 5) Kursrückgang-Tiefe vor dem Signal (max. 35 Punkte - staerkste
    #    Einzelkomponente laut Backtest, ersetzt den wirkungslosen
    #    Turnaround-Score). Rueckgang vom 20-Tage-Hoch, linear bis zum
    #    Cap bei ca. -29 % (deckt sich mit dem staerksten Backtest-Bucket).
    drawdown_magnitude = max(0.0, -drawdown_20t_pct)  # positive Groesse
    drawdown_score = min(35.0, drawdown_magnitude * 1.2)

    basis_score = rsi_score + trend_score + gd200_score + ema50_score  # max. 65
    dip_score_roh = basis_score + drawdown_score  # max. 100

    # 6) Marktregime-Filter
    regime_multiplier = 1.0 if regime_ok else REGIME_MALUS_FAKTOR

    # 7) GD200-Bruch-Malus
    gd200_bruch_pct = (
        max(0.0, ((gd200_heute - c_today) / gd200_heute) * 100)
        if gd200_heute
        else 0.0
    )
    gd200_bruch_malus_faktor = max(0.6, 1.0 - (gd200_bruch_pct / 10.0) * 0.4)

    dip_score = round(
        dip_score_roh * regime_multiplier * gd200_bruch_malus_faktor, 1
    )

    return {
        "close": c_today,
        "rsi": round(rsi_today, 1),
        "gd200": gd200_heute,
        "ema50": ema50_heute,
        "gd200_steigt": gd200_steigt,
        "drawdown_20t_pct": round(drawdown_20t_pct, 2),
        "rsi_score": round(rsi_score, 1),
        "trend_score": round(trend_score, 1),
        "gd200_score": round(gd200_score, 1),
        "ema50_score": round(ema50_score, 1),
        "drawdown_score": round(drawdown_score, 1),
        "regime_multiplier": regime_multiplier,
        "gd200_bruch_malus_faktor": round(gd200_bruch_malus_faktor, 3),
        "dip_score": dip_score,
    }


def signal_stufe(
    dip_score: float,
    soft_schwelle: float = SOFT_KAUFSIGNAL_SCHWELLE,
    voll_schwelle: float = KAUFSIGNAL_SCHWELLE,
) -> str:
    """Klassifiziert einen Dip Score in eine von drei Signalstufen, auf
    Basis des Backtests (Schwellen-Sweep mit der aktuellen Formel):
    - 'voll'  (Score >= 70): statistisches Ziel ~10%+, quote_10pct ~63%
    - 'soft'  (Score 60-69): statistisches Ziel ~5%+, quote_5pct ~80%
    - 'kein'  (Score < 60): kein Kaufsignal

    Wird sowohl in app.py (Watchlist-Anzeige) als auch potenziell im
    Backtest fuer stufenspezifische Auswertungen genutzt."""
    if dip_score >= voll_schwelle:
        return "voll"
    elif dip_score >= soft_schwelle:
        return "soft"
    return "kein"
