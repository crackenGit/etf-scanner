"""
dip_score.py

Gemeinsame Score-Logik für den ETF Dip-Scanner (app.py) und das
Backtesting-Skript (backtest.py). Einzige Quelle der Wahrheit für die
Score-Formel, damit die Live-App und der Backtest garantiert exakt
dasselbe berechnen - keine Duplikation, kein Auseinanderdriften.

Enthält bewusst KEINE Streamlit- oder yfinance-Abhängigkeiten, damit es
sich auch von einem reinen Kommandozeilen-Skript (backtest.py) ohne
Streamlit-Kontext importieren lässt.
"""

import pandas as pd

# ==========================================
# KONFIGURATION (identisch zu app.py - dort werden dieselben Werte
# verwendet; falls in app.py angepasst, hier synchron halten)
# ==========================================
KAUFSIGNAL_SCHWELLE = 66.0
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

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.rolling(window=14).mean()

    gd200_vor_10d = gd200.shift(10)
    gd200_steigt = gd200 > gd200_vor_10d

    return pd.DataFrame({
        "close": close,
        "high": high,
        "low": low,
        "gd200": gd200,
        "gd200_vor_10d": gd200_vor_10d,
        "gd200_steigt": gd200_steigt,
        "ema50": ema50,
        "rsi": rsi,
        "rsi_gestern": rsi.shift(1),
        "avg_gain": avg_gain,
        "avg_loss": avg_loss,
        "atr14": atr14,
        "vortages_hoch": high.shift(1),
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
    tages_tief = float(row["low"]) if not pd.isna(row["low"]) else c_today
    rsi_today = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0
    rsi_gestern = float(row["rsi_gestern"]) if not pd.isna(row["rsi_gestern"]) else rsi_today
    gd200_heute = float(row["gd200"]) if not pd.isna(row["gd200"]) else 0.0
    ema50_heute = float(row["ema50"]) if not pd.isna(row["ema50"]) else c_today
    gd200_steigt = bool(row["gd200_steigt"]) if not pd.isna(row["gd200_steigt"]) else False
    atr14_heute = float(row["atr14"]) if not pd.isna(row["atr14"]) else 0.0
    vortages_hoch = float(row["vortages_hoch"]) if not pd.isna(row["vortages_hoch"]) else c_today

    # 1) RSI-Überverkauft-Tiefe (max. 20 Punkte)
    rsi_score = min(20.0, max(0.0, (45.0 - rsi_today)) * 1.5)

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

    # 5) Turnaround-Qualität (max. 35 Punkte - größte Einzelkomponente)
    bounce_atr_ratio = (
        (c_today - tages_tief) / atr14_heute if atr14_heute > 0 else 0.0
    )
    turnaround_bounce_score = min(15.0, max(0.0, bounce_atr_ratio) * 40.0)

    turnaround_rsi_score = min(10.0, max(0.0, rsi_today - rsi_gestern) * 3.0)

    if c_today >= vortages_hoch:
        turnaround_bestaetigung = 10.0
    elif atr14_heute > 0:
        naehe_faktor = max(0.0, 1.0 - (vortages_hoch - c_today) / atr14_heute)
        turnaround_bestaetigung = round(min(6.0, naehe_faktor * 6.0), 1)
    else:
        turnaround_bestaetigung = 0.0

    turnaround_score = min(
        35.0,
        turnaround_bounce_score + turnaround_rsi_score + turnaround_bestaetigung,
    )

    basis_score = rsi_score + trend_score + gd200_score + ema50_score  # max. 65
    dip_score_roh = basis_score + turnaround_score  # max. 100

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
        "rsi_score": round(rsi_score, 1),
        "trend_score": round(trend_score, 1),
        "gd200_score": round(gd200_score, 1),
        "ema50_score": round(ema50_score, 1),
        "turnaround_score": round(turnaround_score, 1),
        "regime_multiplier": regime_multiplier,
        "gd200_bruch_malus_faktor": round(gd200_bruch_malus_faktor, 3),
        "dip_score": dip_score,
    }
