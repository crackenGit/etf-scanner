"""
historie_tab.py

Tab 3 (Historie): geschlossene und teilverkaufte Positionen.

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung.
"""

import pandas as pd
import streamlit as st
from datetime import datetime
import re


# --- TAB 3: HISTORIE & GESCHLOSSENE / TEILVERKAUFTE TRADES ---
def render_historie_tab(historie_positionen):
    st.subheader("📜 History & Ausgewertete Trades")

    if not historie_positionen:
        st.info(
            "Noch keine Teilverkäufe oder abgeschlossenen Trades in der Historie"
            " vorhanden."
        )
    else:
        historie_liste = []
        for pos in historie_positionen:
            is_sold = pos.get("sold", False)
            is_partially = pos.get("partially_sold", False)
            has_t1 = pos.get("t1_sell_price") is not None
            has_t2 = pos.get("t2_sell_price") is not None

            buy_price = float(pos["buy_price"])
            shares = float(pos["shares"])
            einsatz = buy_price * shares
            half_shares = shares / 2.0

            buy_dt = pd.to_datetime(
                pos.get("buy_date", datetime.now().strftime("%Y-%m-%d"))
            )

            # -------------------------------------------------------------
            # FALL 1: Komplettverkauf auf einmal (100% verkauft über T1)
            # -------------------------------------------------------------
            if is_sold and has_t1 and not has_t2:
                t1_price = float(pos["t1_sell_price"])
                t1_gewinn_eur = (t1_price - buy_price) * shares
                t1_gewinn_pct = (
                    ((t1_price - buy_price) / buy_price) * 100
                    if buy_price > 0
                    else 0.0
                )
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"

                t2_str = "- "  # Bleibt leer bei Direkt-Komplettverkauf

                gesamt_gewinn_eur = t1_gewinn_eur
                gesamt_gewinn_pct = t1_gewinn_pct
                gesamt_str = f"{gesamt_gewinn_eur:+.2f} € ({gesamt_gewinn_pct:+.2f}%)"

                status_label = "✅ Vollständig verkauft"
                v_datum_str = pos.get("t1_sell_date", "-")
                end_dt = (
                    pd.to_datetime(v_datum_str)
                    if v_datum_str != "-"
                    else buy_dt
                )

            # -------------------------------------------------------------
            # FALL 2: 2-Tranchen-Verkauf abgeschlossen (T1 + T2 realisiert)
            # -------------------------------------------------------------
            elif is_sold and has_t2:
                t1_price = float(pos.get("t1_sell_price", buy_price))
                t2_price = float(pos.get("t2_sell_price", buy_price))

                t1_gewinn_eur = (t1_price - buy_price) * half_shares
                t1_gewinn_pct = (
                    ((t1_price - buy_price) / buy_price) * 100
                    if buy_price > 0
                    else 0.0
                )
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"

                t2_gewinn_eur = (t2_price - buy_price) * half_shares
                t2_gewinn_pct = (
                    ((t2_price - buy_price) / buy_price) * 100
                    if buy_price > 0
                    else 0.0
                )
                t2_str = f"{t2_gewinn_eur:+.2f} € ({t2_gewinn_pct:+.2f}%)"

                gesamt_gewinn_eur = t1_gewinn_eur + t2_gewinn_eur
                gesamt_gewinn_pct = (
                    (gesamt_gewinn_eur / einsatz) * 100 if einsatz > 0 else 0.0
                )
                gesamt_str = f"{gesamt_gewinn_eur:+.2f} € ({gesamt_gewinn_pct:+.2f}%)"

                status_label = "✅ Vollständig verkauft"
                v_datum_str = pos.get(
                    "t2_sell_date", pos.get("t1_sell_date", "-")
                )
                end_dt = (
                    pd.to_datetime(v_datum_str)
                    if v_datum_str != "-"
                    else buy_dt
                )

            # -------------------------------------------------------------
            # FALL 3: Laufender Teilverkauf (T1 realisiert, Rest noch aktiv)
            # -------------------------------------------------------------
            else:
                t1_price = float(pos.get("t1_sell_price", buy_price))
                t1_gewinn_eur = (t1_price - buy_price) * half_shares
                t1_gewinn_pct = (
                    ((t1_price - buy_price) / buy_price) * 100
                    if buy_price > 0
                    else 0.0
                )
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"

                t2_str = "- "  # Bleibt leer, da T2 noch aktiv ist

                gesamt_gewinn_eur = t1_gewinn_eur
                gesamt_gewinn_pct = t1_gewinn_pct  # %Gewinn entspricht exakt T1
                gesamt_str = f"{gesamt_gewinn_eur:+.2f} € ({gesamt_gewinn_pct:+.2f}%)"

                status_label = "🟡 Teilverkauft (Rest aktiv)"
                v_datum_str = pos.get("t1_sell_date", "-")
                end_dt = (
                    pd.to_datetime(v_datum_str)
                    if v_datum_str != "-"
                    else pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
                )

            days_held = max(0, (end_dt - buy_dt).days)

            historie_liste.append({
                "ISIN": pos["isin"],
                "Name / Ticker": (
                    f"{pos.get('name', pos['ticker'])} ({pos['ticker']})"
                ),
                "Einsatz": f"{einsatz:.2f} €",
                "Gesamtgewinn": gesamt_str,
                "Gewinn Tranche 1": t1_str,
                "Gewinn Tranche 2": t2_str,
                "Haltedauer": f"{days_held} Tage",
                "Verkaufsdatum": v_datum_str,
                "Status": status_label,
            })

        df_hist = pd.DataFrame(historie_liste)

        if "Verkaufsdatum" in df_hist.columns:
            df_hist = df_hist.sort_values(by="Verkaufsdatum", ascending=False)

        # -------------------------------------------------------------
        # PASTELL HEATMAP STYLING (HELL & SOFT, 2,5 % SCHRITTE)
        # -------------------------------------------------------------
        def style_historie_table(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            target_cols = ["Gesamtgewinn", "Gewinn Tranche 1", "Gewinn Tranche 2"]

            def get_color_style(val_str):
                if not isinstance(val_str, str) or val_str.strip() in ["-", "- "]:
                    return ""

                match = re.search(r"\(([\+\-]?\d+(?:\.\d+)?)\%\)", val_str)
                if not match:
                    return ""

                pct = float(match.group(1))

                # --- NEGATIV-BEREICHE (Sehr softe Rot/Rosa-Töne) ---
                if pct < -10.0:
                    return "background-color: #f4bebe; color: #5c1d17; font-weight: bold;"
                elif -10.0 <= pct < -7.5:
                    return "background-color: #f8d2d4; color: #5c1d17; font-weight: bold;"
                elif -7.5 <= pct < -5.0:
                    return "background-color: #fbe3e4; color: #5c1d17; font-weight: bold;"
                elif -5.0 <= pct < -2.5:
                    return "background-color: #fdf2f2; color: #5c1d17; font-weight: bold;"
                elif -2.5 <= pct < 0.0:
                    return "background-color: #fff9f9; color: #5c1d17; font-weight: bold;"

                # --- POSITIV-BEREICHE (Helle Mint- & Pastell-Töne) ---
                elif 0.0 <= pct < 2.5:
                    return "background-color: #f4fbf7; color: #0e3a1d; font-weight: bold;"
                elif 2.5 <= pct < 5.0:
                    return "background-color: #e4f6ec; color: #0e3a1d; font-weight: bold;"
                elif 5.0 <= pct < 7.5:
                    return "background-color: #d1f0df; color: #0e3a1d; font-weight: bold;"
                elif 7.5 <= pct < 10.0:
                    return "background-color: #bce9d1; color: #0e3a1d; font-weight: bold;"
                elif 10.0 <= pct < 12.5:
                    return "background-color: #a5e1c2; color: #082813; font-weight: bold;"
                elif 12.5 <= pct < 15.0:
                    return "background-color: #8ed8b2; color: #082813; font-weight: bold;"
                elif 15.0 <= pct < 17.5:
                    return "background-color: #76cea1; color: #082813; font-weight: bold;"
                elif 17.5 <= pct < 20.0:
                    return "background-color: #5ec38f; color: #04190b; font-weight: bold;"
                else:  # >= 20.0 %
                    return "background-color: #45b77d; color: #04190b; font-weight: bold;"

            for col in target_cols:
                if col in df.columns:
                    styles[col] = df[col].apply(get_color_style)

            return styles

        styled_df_hist = df_hist.style.apply(style_historie_table, axis=None)
        st.dataframe(styled_df_hist, use_container_width=True, hide_index=True)
