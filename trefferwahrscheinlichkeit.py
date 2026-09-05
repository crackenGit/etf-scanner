"""
trefferwahrscheinlichkeit.py

Zweites, vom Dip Score bewusst GETRENNTES Modell ("Meta-Labeling"):
schaetzt, wie wahrscheinlich ein BEREITS AUSGELOESTES Kaufsignal sein
eigenes Ziel (4% soft / 7% voll) erreicht - und mit welcher durchschnitt-
lichen Rendite. Basiert auf den zwei staerksten, unabhaengig voneinander
wirkenden Faktoren aus dem Signal-Qualitaets-Screening in backtest.py
(signal_qualitaet_screening): ATR-normalisierter Rueckgang und
EMA50-Potenzial-Score.

WICHTIG: Fliesst NICHT in den Dip Score (dip_score.py) ein. Der Dip Score
beantwortet "ist das ueberhaupt ein Kaufsignal", dieses Modul beantwortet
separat "wie sehr vertraue ich einem Signal, das schon ausgeloest hat" -
zwei unterschiedliche Fragen, siehe Chat fuer die Herleitung.

Herkunft der Tabellenwerte: backtest.py-Lauf mit 3.459 Episoden (Score >=
65) auf dem gesamten ETF-Universum, Kreuztabelle ATR-Vielfaches x
EMA50-Score gegen "eigenes Ziel erreicht" bzw. Ø 21-Tage-Rendite. Bei
einer Formel- oder Datenbasis-Aenderung hier manuell nachziehen (siehe
Statistik-Tab in der App fuer die Herleitung).
"""

# (atr_von, atr_bis, ema50_von, ema50_bis, trefferquote_pct, avg_rendite_21t_pct, n_episoden)
TREFFERWAHRSCHEINLICHKEIT_TABELLE = [
    (0, 3, 10, 15, 82.9, 4.12, 35),
    (0, 3, 15, 21, 91.5, 8.84, 129),
    (3, 5, 0, 10, 62.1, 0.17, 383),
    (3, 5, 10, 15, 76.9, 1.76, 627),
    (3, 5, 15, 21, 87.7, 7.09, 301),
    (5, 7, 0, 10, 59.4, 0.09, 685),
    (5, 7, 10, 15, 72.4, 0.97, 456),
    (5, 7, 15, 21, 76.3, 3.16, 430),
    (7, 1000, 0, 10, 54.3, 0.21, 164),
    (7, 1000, 10, 15, 61.1, 0.79, 113),
    (7, 1000, 15, 21, 62.5, -1.44, 136),
]

# Fallback fuer Kombinationen, die im Backtest praktisch nicht vorkamen
# (z.B. sehr flacher Rueckgang + sehr niedriges EMA50-Potenzial - dafuer
# braeuchte es sonst kaum genug Punkte fuer ein Signal >= 65).
MINDEST_STICHPROBE = 20


def schaetze_trefferwahrscheinlichkeit(drawdown_atr_multiple, ema50_score):
    """Sucht die passende Zelle aus der Backtest-Kreuztabelle.

    Rueckgabe: (trefferquote_pct, avg_rendite_21t_pct, n_episoden) oder
    (None, None, 0), falls keine Zelle mit ausreichender Stichprobe passt.
    """
    if drawdown_atr_multiple is None or ema50_score is None:
        return None, None, 0

    for atr_von, atr_bis, ema_von, ema_bis, quote, rendite, n in TREFFERWAHRSCHEINLICHKEIT_TABELLE:
        if atr_von <= drawdown_atr_multiple < atr_bis and ema_von <= ema50_score < ema_bis:
            if n < MINDEST_STICHPROBE:
                return None, None, n
            return quote, rendite, n

    return None, None, 0
