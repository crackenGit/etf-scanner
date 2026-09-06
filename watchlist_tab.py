"""
watchlist_tab.py

Tab 1 (Watchlist & Kaufsignale): der parallele ETF-Scan, der
Diversifikations-/Korrelationsfilter und die sektor-gruppierte Anzeige.

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from dip_score import (
    signal_stufe,
    effektive_schwelle,
    KAUFSIGNAL_SCHWELLE,
    SOFT_KAUFSIGNAL_SCHWELLE,
    RSI_WATCHLIST_SCHWELLE,
    DRAWDOWN_SCORE_MAX,
    MAX_DIP_SCORE,
)
from scanner_data import berechne_indikatoren, hole_etf_name
from signal_log import logge_signale, signal_log_status


def fuehre_scan_durch(etfs, portfolio_isins):
    """Fuehrt den parallelen Watchlist-Scan aus (nur, wenn noch nicht in
    dieser Sitzung geschehen - session_state-Gate) und legt das Ergebnis
    in st.session_state ab. Rendert danach direkt den Hinweis auf
    fehlgeschlagene ETFs, falls es welche gibt."""
    # --- PARALLELER DATEN-SCANNER ---
    if "watchlist_signale" not in st.session_state:
        watchlist_signale, fehlgeschlagene_etfs = [], []
        letzter_zeitstempel = "k.A."
        progress_bar = st.progress(0, text="⚡ Lade Kursdaten (Parallel-Scan)...")

        total_etfs = len(etfs)
        completed_count = 0

        def load_etf_data(item):
            data, ticker, fehler = berechne_indikatoren(item["isin"], item.get("ticker"))
            return item, data, ticker, fehler

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(load_etf_data, item) for item in etfs]

            for future in as_completed(futures):
                completed_count += 1
                item, data, ticker, fehler = future.result()

                progress_bar.progress(
                    completed_count / total_etfs,
                    text=f"⚡ Scanne ETF {completed_count}/{total_etfs}: {item['isin']}",
                )

                if not data:
                    fehlgeschlagene_etfs.append({
                        "Sektor": item["sektor"],
                        "ISIN": item["isin"],
                        "Ticker": ticker,
                        "Grund": fehler,
                    })
                    continue

                if data.get("yahoo_zeit") and data["yahoo_zeit"] != "k.A.":
                    letzter_zeitstempel = data["yahoo_zeit"]

                c = data["close"]
                rsi = data["rsi"]
                rsi35 = data["rsi35_preis"]
                gd200 = data["gd200"]
                ema50 = data["ema50"]
                gd200_steigt = data["gd200_steigt"]
                dip_score = data["dip_score"]
                drawdown_score = data["drawdown_score"]
                drawdown_20t_pct = data["drawdown_20t_pct"]
                drawdown_atr_multiple = data["drawdown_atr_multiple"]
                trefferwahrsch_pct = data.get("trefferwahrscheinlichkeit_pct")
                trefferwahrsch_rendite = data.get("trefferwahrscheinlichkeit_rendite")
                trefferwahrsch_n = data.get("trefferwahrscheinlichkeit_n", 0)
                regime_ok = data["regime_ok"]
                regime_seit_tagen = data.get("regime_seit_tagen")
                ist_stale = data.get("ist_stale", False)
                stale_seit = data.get("stale_seit")
                live_kurs = data["live_close"]
                live_rsi = data["live_rsi"]
                rsi_score = data["rsi_score"]
                trend_score = data["trend_score"]
                gd200_score = data["gd200_score"]
                ema50_score = data["ema50_score"]

                gd200_abstand = ((gd200 - c) / c) * 100
                rsi35_abstand = ((rsi35 - c) / c) * 100
                etf_name = hole_etf_name(ticker)

                stufe = signal_stufe(dip_score, sektor=item["sektor"])
                noetige_punkte = effektive_schwelle(item["sektor"])
                ist_kaufsignal = stufe == "voll"
                ist_soft_signal = stufe == "soft"
                ist_in_portfolio = item["isin"] in portfolio_isins
                # Hauptkriterium: RSI < 40. Portfolio-Positionen erscheinen immer,
                # unabhängig von ihren aktuellen Werten.
                ist_watchlist_kandidat = (rsi < RSI_WATCHLIST_SCHWELLE) or ist_in_portfolio

                entry = {
                    "Name": etf_name,
                    "Sektor": item["sektor"],
                    "ISIN": item["isin"],
                    "Ticker": ticker,
                    "Kurs": c,
                    "RSI": round(rsi, 1),
                    "RSI_Score": rsi_score,
                    "RSI 35 Preis": rsi35,
                    "RSI35_Abstand": rsi35_abstand,
                    "Live_Kurs": live_kurs,
                    "Live_RSI": round(live_rsi, 1),
                    "Trend_Score": trend_score,
                    "GD200": gd200,
                    "GD200_Abstand": gd200_abstand,
                    "GD200_Score": gd200_score,
                    "GD200_steigt": gd200_steigt,
                    "EMA50": ema50,
                    "EMA50_Score": ema50_score,
                    "Dip Score": dip_score,
                    "Noetige_Punkte": noetige_punkte,
                    "Drawdown Score": drawdown_score,
                    "Drawdown_20t_Pct": drawdown_20t_pct,
                    "Drawdown_ATR_Multiple": drawdown_atr_multiple,
                    "Trefferwahrsch_Pct": trefferwahrsch_pct,
                    "Trefferwahrsch_Rendite": trefferwahrsch_rendite,
                    "Trefferwahrsch_N": trefferwahrsch_n,
                    "Marktregime_OK": regime_ok,
                    "Marktregime_Seit_Tagen": regime_seit_tagen,
                    "Ist_Stale": ist_stale,
                    "Stale_Seit": stale_seit,
                    "Zeitstempel": (
                        f"🕒 {stale_seit} (nicht live)" if ist_stale else data["yahoo_zeit"]
                    ),
                    "Ist_Kaufsignal": ist_kaufsignal,
                    "Ist_Soft_Signal": ist_soft_signal,
                    "Ist_Portfolio": ist_in_portfolio,
                    "Return_Serie": data["return_serie"],
                }

                if ist_watchlist_kandidat:
                    watchlist_signale.append(entry)

        progress_bar.empty()
        st.session_state["watchlist_signale"] = watchlist_signale
        st.session_state["fehlgeschlagene_etfs"] = fehlgeschlagene_etfs
        st.session_state["letztes_update"] = letzter_zeitstempel

    # --- GEPUFFERTE FEHLERMELDUNGEN ANZEIGEN ---
    failed_list = st.session_state.get("fehlgeschlagene_etfs", [])
    if failed_list:
        with st.expander(f"⚠️ Hinweis: {len(failed_list)} ETF(s) konnten nicht geladen werden"):
            st.warning(
                "Für folgende Werte konnten bei Yahoo Finance keine Kursdaten abgerufen werden "
                "(z. B. fehlerhafte ISIN oder vorübergehende API-Sperre):"
            )
            st.dataframe(pd.DataFrame(failed_list), use_container_width=True, hide_index=True)


def render_watchlist_tab(sektor_lookup, portfolio_isins, aktive_positionen):
    watch = st.session_state.get("watchlist_signale", [])

    regime_status = watch[0]["Marktregime_OK"] if watch else True
    regime_seit = watch[0].get("Marktregime_Seit_Tagen") if watch else None
    seit_text = f" (seit {regime_seit} Handelstagen)" if regime_seit else ""
    if regime_status:
        st.caption(f"🐂 **Marktphase: Bulle**{seit_text} - Referenzindex über GD200. Rein informativ, kein Einfluss auf den Score.")
    else:
        st.caption(f"🐻 **Marktphase: Bär**{seit_text} - Referenzindex unter GD200. Rein informativ, kein Einfluss auf den Score.")

    anzahl_kaufsignale = sum(1 for e in watch if e["Ist_Kaufsignal"])
    anzahl_soft_signale = sum(1 for e in watch if e["Ist_Soft_Signal"])

    if watch:
        if anzahl_kaufsignale > 0:
            st.success(
                f"**{anzahl_kaufsignale} Kaufsignal(e) gefunden!** "
                f"(Dip Score ≥ {KAUFSIGNAL_SCHWELLE:.0f}, Backtest-Ziel ~10%+)"
                + (
                    f" · zusätzlich {anzahl_soft_signale} softe(s) Signal(e) "
                    f"({SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE - 1:.0f}, Ziel ~5%+)"
                    if anzahl_soft_signale > 0
                    else ""
                )
            )
        elif anzahl_soft_signale > 0:
            st.info(
                f"Kein volles Kaufsignal, aber **{anzahl_soft_signale} softe(s) Signal(e)** "
                f"({SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE - 1:.0f} Punkte, Backtest-Ziel ~5%+)."
            )
        else:
            st.info(
                f"Aktuell kein ETF über der soften Signal-Schwelle von "
                f"{SOFT_KAUFSIGNAL_SCHWELLE:.0f} Punkten."
            )

        st.caption(
            "💡 **Farblegende:** 🥇/🥈/🥉 Top Dip-Scores | 🔥 Kaufsignal "
            "(Score ≥ Schwelle, Ziel ~10%+) | 🟡 Softes Signal (Ziel ~5%+) | "
            "🟪 Lila: Im Portfolio | "
            "RSI: 🟩 ≤31.9, ⬜ 32-35, 🟥 >35 | "
            "Live: 🟩 RSI-Tendenz ↑ (Erholung), ⬜ unverändert, 🟥 RSI-Tendenz ↓ (noch fallend) | "
            "GD200: 🟩 klar drüber, ⬜ knapp (≤1%), 🟥 drunter | "
            "Trefferwahrsch.: 🟩 ≥75%, ⬜ 60-75%, 🟥 <60%"
        )
        st.caption(
            "🎯 **Trefferwahrscheinlichkeit** ist ein zweites, vom Dip Score "
            "komplett getrenntes Modell - schätzt anhand von ATR-Rückgang und "
            "EMA50-Potenzial, wie oft ein *bereits ausgelöstes* Signal historisch "
            "sein eigenes Ziel erreicht hat, inkl. Ø Rendite und Stichprobengröße. "
            "Beeinflusst den Dip Score selbst nicht. Details im Statistik-Bereich oben."
        )
        st.caption(
            "💎 **Verstecktes Juwel:** Score reicht (noch) nicht für ein Signal, "
            "aber die beiden stärksten bekannten Erfolgsfaktoren (ATR-Rückgang + "
            "EMA50-Potenzial) sind schon günstig (Trefferwahrsch. ≥75%) - lohnt "
            "einen Blick, auch ohne 🔥/🟡."
        )

        col_sort1, col_sort2 = st.columns([2, 2])
        with col_sort1:
            sort_kriterium = st.selectbox(
                "🏆 Watchlist Sortierung nach:",
                [
                    "🚀 Dip-Potential Score",
                    "🔥 RSI (Niedrigster zuerst)",
                    "🎯 Abstand zu RSI 35 Zielkurs",
                    "📊 Nähe zu GD200-Unterstützung",
                ],
                index=0,
            )

        df_watch = pd.DataFrame(watch)
        df_watch = df_watch.sort_values(by="Dip Score", ascending=False)
        df_watch["Ticker_Base"] = df_watch["Ticker"].apply(
            lambda x: str(x).split(".")[0] if x else ""
        )
        df_watch = df_watch.drop_duplicates(subset=["ISIN"], keep="first")
        df_watch = df_watch.drop_duplicates(
            subset=["Ticker_Base"], keep="first"
        )

        df_watch["Dip_Rank"] = range(1, len(df_watch) + 1)

        if sort_kriterium == "🔥 RSI (Niedrigster zuerst)":
            df_watch = df_watch.sort_values(by="RSI", ascending=True)
        elif sort_kriterium == "🚀 Dip-Potential Score":
            df_watch = df_watch.sort_values(by="Dip Score", ascending=False)
        elif sort_kriterium == "🎯 Abstand zu RSI 35 Zielkurs":
            df_watch = df_watch.sort_values(
                by="RSI35_Abstand", ascending=False
            )
        elif sort_kriterium == "📊 Nähe zu GD200-Unterstützung":
            df_watch = df_watch.sort_values(by="GD200_Abstand", ascending=True)

        df_watch = df_watch.reset_index(drop=True)

        # --- DIVERSIFIKATIONS-FILTER ---
        # Ziel: keine neuen Signale aus Sektoren zeigen, in denen bereits
        # investiert ist, und keine Signale, die stark mit einer bestehenden
        # Position korrelieren (deckt z.B. "AI-ETF haengt stark an Halbleiter"
        # ab, auch wenn die Sektor-Etiketten unterschiedlich sind). Die eigene
        # gehaltene Position selbst wird nie ausgeblendet (Nachkauf-Check
        # bleibt sichtbar). Siehe Chat fuer die Herleitung.
        KORRELATIONS_SCHWELLE = 0.80

        investierte_sektoren = {
            sektor_lookup.get(isin) for isin in portfolio_isins if sektor_lookup.get(isin)
        }

        portfolio_returns = {}
        for pos in aktive_positionen:
            isin = pos["isin"]
            treffer = df_watch[df_watch["ISIN"] == isin]
            if not treffer.empty and treffer.iloc[0]["Return_Serie"] is not None:
                portfolio_returns[isin] = treffer.iloc[0]["Return_Serie"]
            else:
                pos_data, _, _ = berechne_indikatoren(isin, pos.get("ticker"))
                if pos_data and pos_data.get("return_serie") is not None:
                    portfolio_returns[isin] = pos_data["return_serie"]

        def pruefe_ueberlappung(row):
            if row["Ist_Portfolio"]:
                return False, ""
            gruende = []
            if row["Sektor"] in investierte_sektoren:
                gruende.append(f"Sektor „{row['Sektor']}“ bereits investiert")

            max_korr, korr_mit_isin = 0.0, None
            eigene_serie = row.get("Return_Serie")
            if eigene_serie is not None and len(eigene_serie) > 30:
                for isin, serie in portfolio_returns.items():
                    if serie is None or len(serie) < 30:
                        continue
                    gemeinsam = pd.concat([eigene_serie, serie], axis=1, join="inner")
                    if len(gemeinsam) < 30:
                        continue
                    korr = gemeinsam.iloc[:, 0].corr(gemeinsam.iloc[:, 1])
                    if korr is not None and korr > max_korr:
                        max_korr, korr_mit_isin = korr, isin

            if max_korr > KORRELATIONS_SCHWELLE:
                pos_name = next(
                    (p.get("name", korr_mit_isin) for p in aktive_positionen
                     if p["isin"] == korr_mit_isin),
                    korr_mit_isin,
                )
                gruende.append(f"{max_korr * 100:.0f}% korreliert mit {pos_name}")

            return (len(gruende) > 0), " · ".join(gruende)

        _ueberlappung = df_watch.apply(pruefe_ueberlappung, axis=1)
        df_watch["Ausgeblendet"] = [r[0] for r in _ueberlappung]
        df_watch["Ausblend_Grund"] = [r[1] for r in _ueberlappung]

        df_neu = df_watch[~df_watch["Ausgeblendet"]].reset_index(drop=True)
        df_ausgeblendet = df_watch[df_watch["Ausgeblendet"]].reset_index(drop=True)

        if "signale_geloggt" not in st.session_state:
            logge_signale(df_watch)
            st.session_state["signale_geloggt"] = True
        signal_log_status()

        VERSTECKTES_JUWEL_SCHWELLE = 75.0  # ab dieser Trefferwahrsch. gilt ein
                                            # Nicht-Signal als "verstecktes Juwel"

        def ist_verstecktes_juwel(row):
            """Kein Kauf-/Softsignal (Score reicht nicht), aber die beiden
            staerksten bekannten Erfolgsfaktoren (ATR-Ruckgang + EMA50) sind
            bereits guenstig - siehe Chat: dieses Muster hielt nachweislich
            auch unterhalb der Softsignal-Schwelle stand, keine reine
            Extrapolation."""
            if row["Ist_Kaufsignal"] or row["Ist_Soft_Signal"]:
                return False
            pct = row.get("Trefferwahrsch_Pct")
            return pct is not None and pd.notna(pct) and pct >= VERSTECKTES_JUWEL_SCHWELLE

        def format_name_rank(row):
            name = row["Name"]
            rank = row["Dip_Rank"]
            if row.get("Ist_Stale", False):
                name = f"🕒 {name}"
            if row["Ist_Kaufsignal"]:
                praefix = "🔥 "
            elif row["Ist_Soft_Signal"]:
                praefix = "🟡 "
            elif ist_verstecktes_juwel(row):
                praefix = "💎 "
            else:
                praefix = ""
            if rank == 1:
                return f"{praefix}🥇 {name}"
            elif rank == 2:
                return f"{praefix}🥈 {name}"
            elif rank == 3:
                return f"{praefix}🥉 {name}"
            else:
                return f"{praefix}{name}"

        def signal_label(row):
            if row["Ist_Kaufsignal"]:
                return "🔥 KAUFEN (Ziel ~10%+)"
            elif row["Ist_Soft_Signal"]:
                return "🟡 Softes Signal (Ziel ~5%+)"
            elif ist_verstecktes_juwel(row):
                return "💎 Beobachten (hohe Trefferwahrsch.!)"
            else:
                return "👀 Beobachten"

        def rendere_watchlist_gruppe(df_gruppe):
            """Rendert eine (Teil-)Watchlist als formatierte, eingefärbte
            Tabelle. Nimmt df_gruppe explizit als Parameter (statt auf ein
            äußeres df_watch zuzugreifen), damit dieselbe Funktion sowohl für
            die volle Liste als auch für einzelne Sektor-Gruppen nutzbar ist."""
            display_df = pd.DataFrame()

            def format_dip_score(r):
                if r["Ist_Kaufsignal"]:
                    symbol = " 🔥"
                elif r["Ist_Soft_Signal"]:
                    symbol = " 🟡"
                else:
                    symbol = ""
                return f"{r['Dip Score']:.1f}/{MAX_DIP_SCORE:.0f}{symbol}"

            display_df["Dip Score"] = df_gruppe.apply(format_dip_score, axis=1)

            def format_trefferwahrsch(r):
                pct = r["Trefferwahrsch_Pct"]
                if pct is None or pd.isna(pct):
                    return "– (zu wenig Daten)"
                rendite = r["Trefferwahrsch_Rendite"]
                vorzeichen = "+" if rendite >= 0 else ""
                return f"{pct:.0f}% (Ø {vorzeichen}{rendite:.1f}%, n={int(r['Trefferwahrsch_N'])})"

            display_df["Trefferwahrsch."] = df_gruppe.apply(format_trefferwahrsch, axis=1)

            display_df["Name"] = [
                format_name_rank(df_gruppe.iloc[i]) for i in range(len(df_gruppe))
            ]
            display_df["ISIN"] = df_gruppe["ISIN"]
            display_df["Sektor"] = df_gruppe["Sektor"]
            display_df["Ticker"] = df_gruppe["Ticker"]

            display_df["Kurs"] = df_gruppe["Kurs"].map(lambda x: f"{x:.2f} €")
            display_df["RSI"] = df_gruppe.apply(
                lambda r: f"{r['RSI']:.1f} ({r['RSI_Score']:.0f}/20)", axis=1
            )

            display_df["Live"] = df_gruppe.apply(
                lambda r: (
                    f"{r['Live_Kurs']:.2f} € ("
                    f"{r['Live_RSI']:.1f} "
                    + (
                        "↑"
                        if r["Live_RSI"] > r["RSI"]
                        else ("↓" if r["Live_RSI"] < r["RSI"] else "→")
                    )
                    + ")"
                ),
                axis=1,
            )

            display_df["Trend"] = df_gruppe.apply(
                lambda r: (
                    ("↓" if r["EMA50"] < r["GD200"] else "↑")
                    + ("↓" if not r["GD200_steigt"] else "↑")
                    + f" {r['Trend_Score']:.0f}/15 (Info, nicht im Score)"
                ),
                axis=1,
            )

            display_df["GD200"] = df_gruppe.apply(
                lambda r: (
                    f"{r['GD200']:.2f} €"
                    f" ({((r['Kurs'] - r['GD200']) / r['GD200']) * 100:+.1f}%)"
                    f" · {r['GD200_Score']:.0f}/15"
                ),
                axis=1,
            )

            display_df["EMA50"] = df_gruppe.apply(
                lambda r: (
                    f"{r['EMA50']:.2f} €"
                    f" ({((r['EMA50'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
                    f" · {r['EMA50_Score']:.0f}/20"
                ),
                axis=1,
            )

            display_df["Rückgang"] = df_gruppe.apply(
                lambda r: f"{r['Drawdown_ATR_Multiple']:.1f} ATR ({r['Drawdown_20t_Pct']:.1f}%) · {r['Drawdown Score']:.0f}/{DRAWDOWN_SCORE_MAX:.0f}",
                axis=1,
            )

            display_df["Regime"] = df_gruppe["Marktregime_OK"].map(
                lambda ok: "🐂 Bulle" if ok else "🐻 Bär"
            )

            display_df["Signal"] = [
                signal_label(df_gruppe.iloc[i]) for i in range(len(df_gruppe))
            ]
            display_df["Zeitstempel"] = df_gruppe["Zeitstempel"]

            def style_watchlist_cells(df):
                styles = pd.DataFrame("", index=df.index, columns=df.columns)
                for idx in df.index:
                    row_raw = df_gruppe.loc[idx]

                    if row_raw.get("Ist_Portfolio", False):
                        color = "background-color: #e8daef; color: #111111;"
                    else:
                        color = ""

                    if color:
                        for col in df.columns:
                            styles.loc[idx, col] = color

                    dip_rank = row_raw.get("Dip_Rank", 999)
                    if (
                        dip_rank in (1, 2, 3)
                        and not row_raw["Ist_Kaufsignal"]
                        and not row_raw.get("Ist_Soft_Signal", False)
                        and not row_raw.get("Ist_Portfolio", False)
                    ):
                        medaillen = {1: "#fef9e7", 2: "#f2f3f4", 3: "#fbeee6"}
                        styles.loc[idx, "Name"] = (
                            f"background-color: {medaillen[dip_rank]}; font-weight: bold;"
                        )

                    kurs_val = row_raw["Kurs"]
                    gd200_val = row_raw["GD200"]
                    rsi_val = row_raw["RSI"]

                    if rsi_val <= 31.9:
                        styles.loc[idx, "RSI"] = "background-color: #d4edda; color: #155724;"
                    elif rsi_val <= 35:
                        styles.loc[idx, "RSI"] = "background-color: #e2e3e5; color: #383d41;"
                    else:
                        styles.loc[idx, "RSI"] = "background-color: #f8d7da; color: #721c24;"

                    live_rsi_val = row_raw.get("Live_RSI")
                    if live_rsi_val is not None:
                        if live_rsi_val > rsi_val:
                            styles.loc[idx, "Live"] = "background-color: #d4edda; color: #155724;"
                        elif live_rsi_val < rsi_val:
                            styles.loc[idx, "Live"] = "background-color: #f8d7da; color: #721c24;"
                        else:
                            styles.loc[idx, "Live"] = "background-color: #e2e3e5; color: #383d41;"

                    gd200_diff_pct = (
                        ((kurs_val - gd200_val) / gd200_val) * 100 if gd200_val else 0.0
                    )
                    if gd200_diff_pct <= 0:
                        styles.loc[idx, "GD200"] = "background-color: #f8d7da; color: #721c24;"
                    elif gd200_diff_pct <= 1:
                        styles.loc[idx, "GD200"] = "background-color: #e2e3e5; color: #383d41;"
                    else:
                        styles.loc[idx, "GD200"] = "background-color: #d4edda; color: #155724;"

                    styles.loc[idx, "Dip Score"] = "font-weight: bold; text-align: center;"

                    trefferwahrsch_pct = row_raw.get("Trefferwahrsch_Pct")
                    if trefferwahrsch_pct is not None and pd.notna(trefferwahrsch_pct):
                        if trefferwahrsch_pct >= 75:
                            styles.loc[idx, "Trefferwahrsch."] = "background-color: #d4edda; color: #155724; font-weight: bold;"
                        elif trefferwahrsch_pct >= 60:
                            styles.loc[idx, "Trefferwahrsch."] = "background-color: #e2e3e5; color: #383d41;"
                        else:
                            styles.loc[idx, "Trefferwahrsch."] = "background-color: #f8d7da; color: #721c24;"

                return styles

            styled_df = display_df.style.apply(style_watchlist_cells, axis=None)
            st.dataframe(styled_df, use_container_width=True, hide_index=True)

        st.caption(
            "↓↓ in der Trend-Spalte bedeutet volle Punktzahl (gebrochene "
            "Trendstruktur), ↑↑ bedeutet 0 Punkte - **umgekehrt** zur "
            "klassischen Lesart von 'Trend intakt = gut'. Details dazu oben "
            "unter 'Wann entsteht ein Kaufsignal?'."
        )
        st.caption(
            "🕒 vor dem Namen bedeutet: Live-Abruf gerade nicht möglich "
            "(z. B. außerhalb der Handelszeiten), es wird der letzte "
            "erfolgreich geladene Stand gezeigt - Zeitpunkt siehe Spalte "
            "'Zeitstempel'."
        )

        # --- Sektor-gruppierte Anzeige der "neuen Chancen" ---
        if not df_neu.empty:
            sektor_reihenfolge = (
                df_neu.groupby("Sektor")["Dip Score"].max()
                .sort_values(ascending=False)
                .index.tolist()
            )
            for sektor in sektor_reihenfolge:
                gruppe = df_neu[df_neu["Sektor"] == sektor].reset_index(drop=True)
                st.markdown(f"#### {sektor} ({len(gruppe)})")
                rendere_watchlist_gruppe(gruppe)
        else:
            st.write("Keine neuen (nicht-überlappenden) Signale aktuell.")

        if not df_ausgeblendet.empty:
            with st.expander(
                f"🔕 {len(df_ausgeblendet)} Signal(e) wegen Portfolio-Nähe "
                f"ausgeblendet (bereits investierter Sektor oder "
                f">{KORRELATIONS_SCHWELLE * 100:.0f}% korreliert)"
            ):
                for _, row in df_ausgeblendet.sort_values(
                    "Dip Score", ascending=False
                ).iterrows():
                    st.caption(
                        f"**{row['Name']}** ({row['Sektor']}) - {row['Ausblend_Grund']}"
                    )
    else:
        st.write("Keine ETFs in der Watchlist.")
