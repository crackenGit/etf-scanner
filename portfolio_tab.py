"""
portfolio_tab.py

Tab 2 (Mein Portfolio): Positions-Tracking, Zielkurs-Berechnung und
Performance-Anzeige fuer die aktiven (nicht verkauften) Positionen.

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung.
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from dip_score import signal_stufe, ZIEL_RENDITE_SOFT_PCT, ZIEL_RENDITE_VOLL_PCT
from scanner_data import berechne_indikatoren

# Manuelle Signalstufen-Zuordnung: greift nur, wenn portfolio.py für die
# ISIN weder 'signal_stufe' noch 'dip_score_bei_kauf' gesetzt hat - damit
# ihr Zielkurs/Abstand seht, ohne portfolio.py sofort anfassen zu müssen.
# Werte in portfolio.py haben immer Vorrang.
MANUELLE_SIGNAL_STUFEN = {
    "IE0007Y8Y157": "soft",
    "IE000I8KRLL9": "voll",
}


def performance_farbe(pct):
    """5-stufiger Grün/Rot-Farbverlauf in 2-Prozentpunkt-Schritten.
    Referenzpunkt: der Grünton aus der Watchlist (#d4edda) entspricht 4-6%."""
    if pct is None or pd.isna(pct):
        return ""
    if pct > 0:
        if pct < 2:
            return "background-color: #eef9f1; color: #155724;"
        elif pct < 4:
            return "background-color: #dcf1e0; color: #155724;"
        elif pct < 6:
            return "background-color: #d4edda; color: #155724;"
        elif pct < 8:
            return "background-color: #a8dab5; color: #0e3a1d; font-weight: bold;"
        else:
            return "background-color: #7cc794; color: #0e3a1d; font-weight: bold;"
    elif pct < 0:
        if pct >= -2:
            return "background-color: #fdf2f2; color: #721c24;"
        elif pct >= -4:
            return "background-color: #f8d7da; color: #721c24;"
        elif pct >= -6:
            return "background-color: #f1b0b7; color: #58151c;"
        elif pct >= -8:
            return "background-color: #e78088; color: #58151c; font-weight: bold;"
        else:
            return "background-color: #dc3545; color: #ffffff; font-weight: bold;"
    return ""


# Manuelle Signalstufen-Zuordnung: greift nur, wenn portfolio.py für die
# ISIN weder 'signal_stufe' noch 'dip_score_bei_kauf' gesetzt hat - damit
# ihr Zielkurs/Abstand seht, ohne portfolio.py sofort anfassen zu müssen.
# Werte in portfolio.py haben immer Vorrang.
MANUELLE_SIGNAL_STUFEN = {
    "IE0007Y8Y157": "soft",
    "IE000I8KRLL9": "voll",
}


def performance_farbe(pct):
    """5-stufiger Grün/Rot-Farbverlauf in 2-Prozentpunkt-Schritten.
    Referenzpunkt: der Grünton aus der Watchlist (#d4edda) entspricht 4-6%."""
    if pct is None or pd.isna(pct):
        return ""
    if pct > 0:
        if pct < 2:
            return "background-color: #eef9f1; color: #155724;"
        elif pct < 4:
            return "background-color: #dcf1e0; color: #155724;"
        elif pct < 6:
            return "background-color: #d4edda; color: #155724;"
        elif pct < 8:
            return "background-color: #a8dab5; color: #0e3a1d; font-weight: bold;"
        else:
            return "background-color: #7cc794; color: #0e3a1d; font-weight: bold;"
    elif pct < 0:
        if pct >= -2:
            return "background-color: #fdf2f2; color: #721c24;"
        elif pct >= -4:
            return "background-color: #f8d7da; color: #721c24;"
        elif pct >= -6:
            return "background-color: #f1b0b7; color: #58151c;"
        elif pct >= -8:
            return "background-color: #e78088; color: #58151c; font-weight: bold;"
        else:
            return "background-color: #dc3545; color: #ffffff; font-weight: bold;"
    return ""


def render_portfolio_tab(aktive_positionen, sektor_lookup):
    st.subheader("📊 Aktive Positionen")

    if not aktive_positionen:
        st.info("Aktuell keine aktiven offenen Positionen im Portfolio.")
    else:
        portfolio_zeilen = []
        fehler_liste = []

        for pos in aktive_positionen:
            try:
                data, ticker_used, fehler = berechne_indikatoren(pos["isin"], pos.get("ticker"))
                if not data:
                    fehler_liste.append(
                        f"{pos.get('name', pos.get('isin', '?'))} (keine Kursdaten - {fehler})"
                    )
                    continue

                current_price = data["live_close"]
                if current_price is None or pd.isna(current_price):
                    fehler_liste.append(
                        f"{pos.get('name', pos.get('isin', '?'))} (Kursdaten unvollständig/NaN)"
                    )
                    continue

                buy_price = float(pos["buy_price"])
                shares = float(pos.get("shares", 0))

                # Manuelle Zuordnung ist bindend und überschreibt auch die
                # Alt-System-Einstufung (partially_sold), nicht nur die Stufe.
                manuelle_stufe = MANUELLE_SIGNAL_STUFEN.get(pos["isin"])
                ist_alt_position = pos.get("partially_sold", False) and manuelle_stufe is None

                # Signalstufe: manuelle Zuordnung oben ist für die dort
                # gelisteten ISINs bindend > sonst portfolio.py ('signal_stufe')
                # > gespeicherter Score ('dip_score_bei_kauf') > unbekannt.
                pos_sektor = pos.get("sektor") or sektor_lookup.get(pos["isin"], "-")
                stufe = manuelle_stufe
                if stufe not in ("soft", "voll"):
                    stufe = pos.get("signal_stufe")
                if stufe not in ("soft", "voll"):
                    gespeicherter_score = pos.get("dip_score_bei_kauf")
                    stufe = (
                        signal_stufe(float(gespeicherter_score), sektor=pos_sektor)
                        if gespeicherter_score is not None
                        else None
                    )
                    if stufe == "kein":
                        stufe = None

                if ist_alt_position:
                    ziel_pct, ziel_kurs = None, None
                elif stufe == "voll":
                    ziel_pct = ZIEL_RENDITE_VOLL_PCT
                    ziel_kurs = buy_price * (1 + ziel_pct / 100)
                elif stufe == "soft":
                    ziel_pct = ZIEL_RENDITE_SOFT_PCT
                    ziel_kurs = buy_price * (1 + ziel_pct / 100)
                else:
                    ziel_pct, ziel_kurs = None, None

                if ziel_kurs is not None:
                    abstand_euro = ziel_kurs - current_price
                    abstand_pct = (abstand_euro / current_price) * 100
                    ziel_erreicht = current_price >= ziel_kurs
                else:
                    abstand_euro, abstand_pct = None, None
                    ziel_erreicht = False

                performance_pct = ((current_price - buy_price) / buy_price) * 100
                gewinn_euro = (current_price - buy_price) * shares

                days_held = 0
                if pos.get("buy_date"):
                    buy_date = pd.to_datetime(pos["buy_date"])
                    today_date = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
                    days_held = (today_date - buy_date).days

                portfolio_zeilen.append({
                    "Name": (
                        f"🕒 {pos.get('name', ticker_used)}"
                        if data.get("ist_stale", False)
                        else pos.get("name", ticker_used)
                    ),
                    "ISIN": pos["isin"],
                    "Sektor": pos_sektor,
                    "Ticker": ticker_used,
                    "Kurs": current_price,
                    "Kaufkurs": buy_price,
                    "Stückzahl": shares,
                    "Signal": "alt" if ist_alt_position else (stufe or "unbekannt"),
                    "Zielkurs": ziel_kurs,
                    "Abstand_Euro": abstand_euro,
                    "Abstand_Pct": abstand_pct,
                    "Performance_Pct": performance_pct,
                    "Gewinn_Euro": gewinn_euro,
                    "Tage": days_held,
                    "Ziel_Erreicht": ziel_erreicht,
                })
            except Exception as e:
                fehler_liste.append(f"{pos.get('name', pos.get('isin', '?'))} ({e})")

        if fehler_liste:
            with st.expander(f"⚠️ {len(fehler_liste)} Position(en) mit Fehler"):
                for f in fehler_liste:
                    st.write(f"- {f}")

        if portfolio_zeilen:
            df_portfolio = pd.DataFrame(portfolio_zeilen)

            anzahl_ziel_erreicht = int(df_portfolio["Ziel_Erreicht"].sum())
            if anzahl_ziel_erreicht > 0:
                st.success(
                    f"🎯 **{anzahl_ziel_erreicht} Position(en) haben ihr Kursziel erreicht!**"
                )

            anzahl_unbekannt = int((df_portfolio["Signal"] == "unbekannt").sum())
            if anzahl_unbekannt > 0:
                st.caption(
                    f"❓ {anzahl_unbekannt} Position(en) ohne bekannte Signalstufe - "
                    "'signal_stufe' (oder 'dip_score_bei_kauf') in portfolio.py ergänzen, "
                    "um dafür ein Kursziel zu berechnen."
                )

            display_df = pd.DataFrame()
            display_df["Name"] = df_portfolio["Name"]
            display_df["ISIN"] = df_portfolio["ISIN"]
            display_df["Sektor"] = df_portfolio["Sektor"]
            display_df["Kurs"] = df_portfolio["Kurs"].map(lambda x: f"{x:.2f} €")
            display_df["Kaufkurs"] = df_portfolio["Kaufkurs"].map(lambda x: f"{x:.2f} €")
            display_df["Stückzahl"] = df_portfolio["Stückzahl"].map(lambda x: f"{x:g}")
            display_df["Signal"] = df_portfolio["Signal"].map(
                lambda s: {
                    "voll": "🔥 Voll",
                    "soft": "🟡 Soft",
                    "alt": "⚪ Alt-System",
                    "unbekannt": "❓ unbekannt",
                }.get(s, "❓ unbekannt")
            )
            display_df["Zielkurs"] = df_portfolio.apply(
                lambda r: (
                    f"{r['Zielkurs']:.2f} € 🔥" if r["Ziel_Erreicht"]
                    else (f"{r['Zielkurs']:.2f} €" if pd.notna(r["Zielkurs"]) else "-")
                ),
                axis=1,
            )
            display_df["Abstand z. Ziel"] = df_portfolio.apply(
                lambda r: (
                    f"{r['Abstand_Euro']:+.2f} € ({r['Abstand_Pct']:+.1f}%)"
                    if pd.notna(r["Abstand_Euro"])
                    else "-"
                ),
                axis=1,
            )
            display_df["Performance"] = df_portfolio["Performance_Pct"].map(
                lambda x: f"{x:+.2f}%"
            )
            display_df["Gewinn"] = df_portfolio["Gewinn_Euro"].map(lambda x: f"{x:+.2f} €")
            display_df["Tage"] = df_portfolio["Tage"]

            def style_portfolio(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                for idx in df.index:
                    row_raw = df_portfolio.loc[idx]
                    styles.loc[idx, "Performance"] = performance_farbe(
                        row_raw["Performance_Pct"]
                    )
                return styles

            styled_portfolio = display_df.style.apply(style_portfolio, axis=None)
            st.dataframe(styled_portfolio, use_container_width=True, hide_index=True)

            st.caption(
                f"💡 Exit-Ziele: 🔥 Volles Signal → +{ZIEL_RENDITE_VOLL_PCT:.0f}% | "
                f"🟡 Softes Signal → +{ZIEL_RENDITE_SOFT_PCT:.0f}% "
                "(Backtest-optimiert, siehe Kaufsignal-Info oben). "
                "⚪ Alt-System = vor der Umstellung nach der alten Tranchen-Logik gekauft, "
                "kein neues Kursziel berechnet."
            )
        else:
            st.info("Keine auswertbaren Positionen.")

