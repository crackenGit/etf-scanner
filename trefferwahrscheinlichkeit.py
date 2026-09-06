"""
trefferwahrscheinlichkeit.py

Zweites, vom Dip Score bewusst GETRENNTES Modell ("Meta-Labeling"):
schaetzt, wie wahrscheinlich ein BEREITS AUSGELOESTES Kaufsignal sein
eigenes Ziel erreicht - und mit welcher durchschnittlichen Rendite.
Basiert auf den zwei staerksten, unabhaengig voneinander wirkenden
Faktoren aus dem Signal-Qualitaets-Screening in backtest.py: ATR-
normalisierter Rueckgang und EMA50-Potenzial-Score.

WICHTIG: Fliesst NICHT in den Dip Score (dip_score.py) ein. Der Dip Score
beantwortet "ist das ueberhaupt ein Kaufsignal", dieses Modul beantwortet
separat "wie sehr vertraue ich einem Signal, das schon ausgeloest hat" -
zwei unterschiedliche Fragen, siehe Chat fuer die Herleitung.

Herkunft der Tabellenwerte: backtest.py-Lauf NACH Entfernung von
trend_score aus dem Score (FORMEL_VERSION v4), Basis Score>=40 (12.827
Episoden) auf dem gesamten ETF-Universum - bewusst eine breitere Basis als
nur die (jetzt strengere) Kaufsignal-Schwelle, da die feineren Bins der
vorigen Tabellen-Version mit der neuen, selektiveren Schwelle zu duenn
besetzt waren. Nur 2 ATR-Bins statt vormals 4, aus demselben Grund
(Zellenbesetzung). Bei einer erneuten Formel- oder Datenbasis-Aenderung
hier manuell nachziehen (siehe Statistik-Tab in der App fuer die
Herleitung).
"""

# (atr_von, atr_bis, ema50_von, ema50_bis, trefferquote_pct, avg_rendite_21t_pct, n_episoden)
TREFFERWAHRSCHEINLICHKEIT_TABELLE = [
    (0, 4, 0, 10, 27.6, 1.63, 6292),
    (0, 4, 10, 15, 60.4, 3.63, 280),
    (0, 4, 15, 21, 85.0, 5.88, 100),
    (4, 1000, 0, 10, 19.4, 0.45, 2933),
    (4, 1000, 10, 15, 45.6, 0.84, 57),
    (4, 1000, 15, 21, 63.6, 4.62, 33),
]

# Mindeststichprobe, unterhalb derer eine Zelle als "keine ausreichenden
# Daten" behandelt wird, statt eine unzuverlaessige Schaetzung zu liefern.
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
