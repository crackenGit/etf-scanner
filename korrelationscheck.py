"""
korrelations_check.py

Zeigt die tatsaechliche Verteilung der Kurskorrelationen in eurem
ETF-Universum - innerhalb desselben Sektors vs. zwischen verschiedenen
Sektoren. Hilft, eine evidenzbasierte KORRELATIONS_SCHWELLE fuer den
Diversifikations-Filter in app.py zu waehlen, statt zu raten.

Braucht Internetzugang (yfinance) - lokal ausfuehren.
Aufruf: python korrelations_check.py
"""

import sys
import pandas as pd
import numpy as np
import yfinance as yf

from backtest import parse_isin_file  # bereits vorhanden, keine Duplikation


def main():
    etfs = parse_isin_file("isin.txt")
    print(f"Lade Kursdaten fuer {len(etfs)} ETFs (das dauert etwas)...")

    returns = {}
    sektor_je_ticker = {}
    for idx, item in enumerate(etfs, start=1):
        ticker = item.get("ticker")
        if not ticker:
            continue
        print(f"  [{idx}/{len(etfs)}] {ticker}...")
        try:
            df = yf.download(ticker, period="1y", progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            close = df["Close"].dropna()
            if len(close) < 60:
                continue
            returns[ticker] = close.pct_change().dropna()
            sektor_je_ticker[ticker] = item["sektor"]
        except Exception:
            continue

    print(f"\n{len(returns)} ETFs erfolgreich geladen. Berechne Korrelationsmatrix...")
    rendite_df = pd.DataFrame(returns)
    korr_matrix = rendite_df.corr()

    innerhalb_sektor = []
    zwischen_sektoren = []
    ticker_liste = list(returns.keys())
    for i in range(len(ticker_liste)):
        for j in range(i + 1, len(ticker_liste)):
            t1, t2 = ticker_liste[i], ticker_liste[j]
            korr = korr_matrix.loc[t1, t2]
            if pd.isna(korr):
                continue
            if sektor_je_ticker[t1] == sektor_je_ticker[t2]:
                innerhalb_sektor.append(korr)
            else:
                zwischen_sektoren.append(korr)

    print("\n" + "=" * 60)
    print("ERGEBNIS")
    print("=" * 60)
    print(f"\nINNERHALB desselben Sektors ({len(innerhalb_sektor)} Paare):")
    print(f"  Median: {np.median(innerhalb_sektor):.2f}")
    print(f"  25.-75. Perzentil: {np.percentile(innerhalb_sektor, 25):.2f} - {np.percentile(innerhalb_sektor, 75):.2f}")

    print(f"\nZWISCHEN verschiedenen Sektoren ({len(zwischen_sektoren)} Paare):")
    print(f"  Median: {np.median(zwischen_sektoren):.2f}")
    print(f"  25.-75. Perzentil: {np.percentile(zwischen_sektoren, 25):.2f} - {np.percentile(zwischen_sektoren, 75):.2f}")
    print(f"  90. Perzentil: {np.percentile(zwischen_sektoren, 90):.2f}  <- die staerksten sektor-uebergreifenden Ueberschneidungen")

    print("\nTop 15 staerkste sektor-uebergreifende Paare (Kandidaten fuer eine sinnvolle Schwelle):")
    paare = []
    for i in range(len(ticker_liste)):
        for j in range(i + 1, len(ticker_liste)):
            t1, t2 = ticker_liste[i], ticker_liste[j]
            if sektor_je_ticker[t1] != sektor_je_ticker[t2]:
                korr = korr_matrix.loc[t1, t2]
                if not pd.isna(korr):
                    paare.append((korr, t1, sektor_je_ticker[t1], t2, sektor_je_ticker[t2]))
    paare.sort(reverse=True)
    for korr, t1, s1, t2, s2 in paare[:15]:
        print(f"  {korr:.2f}  {t1} ({s1})  <->  {t2} ({s2})")


if __name__ == "__main__":
    main()
