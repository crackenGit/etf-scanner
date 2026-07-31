from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
import warnings
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# Externe Portfolio- und PIN-Datei importieren
from portfolio import DEINE_PIN, PORTFOLIO

# --- STREAMLIT SEITEN-SETUP ---
st.set_page_config(
    page_title="ETF Dip-Scanner & Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# PIN-SCHUTZ / PASSWORT
# ==========================================
def pin_abfrage():
    if "pin_ok" not in st.session_state:
        st.session_state["pin_ok"] = False

    if not st.session_state["pin_ok"]:
        st.title("🔒 Zugriff geschützt")
        eingabe = st.text_input(
            "Bitte PIN eingeben:", type="password", key="pin_eingabe"
        )

        if st.button("Anmelden", use_container_width=True):
            if eingabe == DEINE_PIN:
                st.session_state["pin_ok"] = True
                st.rerun()
            else:
                st.error("❌ Falsche PIN!")

        return False
    return True


if not pin_abfrage():
    st.stop()

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ==========================================
# MATCHING & ISIN LISTE
# ==========================================
MANUAL_TICKERS = {
    "LU2090063327": ["SEMD.MI", "6B7A.DE", "CHIP.PA"],
    "IE00BCHWNV48": ["XIND.MI", "XIND.L", "XCHA.DE", "XINW.DE"],
    "IE00BLCHJB90": ["WCLD.L", "WCLD.DE"],
    "IE000E7EI9P0": ["WELU.DE"],
    "IE00BJ5JNZ06": ["CBUF.DE", "CBUF.L"],
    "IE00BLPK3577": ["CYBR.L", "ISPY.DE"],
    "IE00B3Q19T94": ["IUFS.L", "S5FP.DE"],
    "IE00B5MTWD60": ["WIFI.L", "QDVE.DE"],
    "IE000KYX7IP4": ["FINX.L"],
    "IE000NXF88S1": ["ENRG.L"],
    "IE000J0LN0R5": ["WNRG.L"],
    "IE00BJ5JP105": ["INRG.L", "IQQH.DE"],
    "IE00BM67HN00": ["XDWS.DE", "XDWG.L"],
    "IE00BWBXM279": ["WCOS.L", "STPL.DE"],
    "IE00BM67HR13": ["XDWD.DE", "XDWG.L"],
    "IE00BWBXM386": ["ZPDD.DE", "SXLY.L"],
    "IE000I8KRLL9": ["SEC0.DE", "SEMI.AS"],
    "IE0003A512E4": ["AAKI.DE"],
}


def parse_isin_file(filename="isin.txt"):
    etf_liste = []
    aktueller_sektor = "Allgemein"
    try:
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#"):
                        aktueller_sektor = (
                            line.lstrip("#").strip().capitalize()
                        )
                    continue
                etf_liste.append({"sektor": aktueller_sektor, "isin": line})
    except FileNotFoundError:
        return []
    return etf_liste


def isin_zu_ticker(isin):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            quotes = response.json().get("quotes", [])
            for q in quotes:
                symbol = q.get("symbol", "")
                if symbol.endswith(".DE"):
                    return symbol
            for suffix in [".L", ".PA", ".AS", ".MI", ".SW", ".F"]:
                for q in quotes:
                    symbol = q.get("symbol", "")
                    if symbol.endswith(suffix):
                        return symbol
            for q in quotes:
                symbol = q.get("symbol", "")
                if not any(
                    symbol.endswith(bad) for bad in [".SG", ".BE", ".MU", ".DU"]
                ):
                    return symbol
    except Exception:
        pass
    return None


@st.cache_data(ttl=300)
def berechne_indikatoren(isin):
    kandidaten = (
        MANUAL_TICKERS[isin]
        if isin in MANUAL_TICKERS
        else ([isin_zu_ticker(isin)] if isin_zu_ticker(isin) else [])
    )

    data, erfolgreicher_ticker = None, None
    for ticker_symbol in kandidaten:
        try:
            df = yf.download(ticker_symbol, period="2y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if not df.empty and len(df) >= 200:
                data, erfolgreicher_ticker = df, ticker_symbol
                break
        except Exception:
            continue

    if data is None:
        return None, (kandidaten[0] if kandidaten else "N/A")

    yahoo_zeit = "k.A."
    try:
        if erfolgreicher_ticker:
            t_obj = yf.Ticker(erfolgreicher_ticker)
            fi = getattr(t_obj, "fast_info", None)
            if fi and getattr(fi, "last_trade_time", None):
                ts = pd.to_datetime(
                    fi.last_trade_time, unit="s", utc=True
                ).tz_convert("Europe/Berlin")
                yahoo_zeit = ts.strftime("%H:%M Uhr (%d.%m.)")
            else:
                intraday = t_obj.history(period="1d", interval="1m")
                if not intraday.empty:
                    last_ts = intraday.index[-1]
                    if last_ts.tzinfo is not None:
                        last_ts = last_ts.tz_convert("Europe/Berlin")
                    yahoo_zeit = last_ts.strftime("%H:%M Uhr (%d.%m.)")
    except Exception:
        pass

    close = data["Close"].dropna()
    low = data["Low"].dropna() if "Low" in data else close
    high = data["High"].dropna() if "High" in data else close

    gd200 = close.rolling(window=200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

    # ATR 14 (Average True Range)
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr14 = true_range.rolling(window=14).mean()
    atr_today = float(atr14.iloc[-1]) if not atr14.empty else 0.0

    ag_today = float(avg_gain.iloc[-1])
    al_today = float(avg_loss.iloc[-1])
    c_today = float(close.iloc[-1])
    tages_tief = float(low.iloc[-1]) if not low.empty else c_today
    rsi_today = float(rsi.iloc[-1])

    if rsi_today > 35.0:
        drop_needed = (169.0 * ag_today - 91.0 * al_today) / 7.0
        rsi35_preis = c_today - drop_needed
    else:
        rise_needed = (91.0 * al_today - 169.0 * ag_today) / 13.0
        rsi35_preis = c_today + rise_needed

    gd200_heute = float(gd200.iloc[-1])
    ema50_heute = float(ema50.iloc[-1])
    gd200_vor_10d = (
        float(gd200.iloc[-11]) if len(gd200) >= 11 else gd200_heute
    )
    gd200_steigt = gd200_heute > gd200_vor_10d

    perf_1w = 0.0
    if len(close) >= 6:
        close_1w = float(close.iloc[-6])
        perf_1w = ((c_today - close_1w) / close_1w) * 100

    rsi_heute = rsi_today
    rsi_gestern = float(rsi.iloc[-2]) if len(rsi) >= 2 else rsi_heute

    intraday_turnaround = (rsi_heute < 35.0) and (c_today >= tages_tief * 1.005)
    vortag_turnaround = (rsi_gestern < 35.0) and (rsi_heute > rsi_gestern)

    turnaround_erkannt = intraday_turnaround or vortag_turnaround
    is_fallendes_messer = not turnaround_erkannt

    rsi_score = max(0, (45 - rsi_today)) * 1.5
    ema50_upside_pct = max(0, ((ema50_heute - c_today) / c_today) * 100)
    ema50_score = ema50_upside_pct * 2.0

    rsi35_dist_abs = abs(rsi35_preis - c_today) / c_today * 100
    rsi35_proximity_score = max(0, 15 - rsi35_dist_abs) * 1.5

    gd200_buffer_pct = ((c_today - gd200_heute) / gd200_heute) * 100
    if gd200_buffer_pct > 0:
        gd200_score = min(25.0, gd200_buffer_pct * 1.2)
    else:
        gd200_score = -20.0

    dip_score = round(
        rsi_score + ema50_score + rsi35_proximity_score + gd200_score, 1
    )

    # 52-Wochen-Hoch (ca. 252 Handelstage)
    df_1y = data.tail(252)
    high_52w = float(df_1y["High"].max()) if "High" in df_1y else c_today

    return {
        "close": c_today,
        "rsi": rsi_today,
        "rsi35_preis": float(rsi35_preis),
        "gd200": gd200_heute,
        "gd200_vor_10d": gd200_vor_10d,
        "ema50": ema50_heute,
        "atr14": atr_today,
        "high_52w": high_52w,
        "gd200_steigt": gd200_steigt,
        "perf_1w": perf_1w,
        "dip_score": dip_score,
        "is_fallendes_messer": is_fallendes_messer,
        "yahoo_zeit": yahoo_zeit,
    }, erfolgreicher_ticker


# ==========================================
# APP USER INTERFACE
# ==========================================
st.title("📈 ETF Dip-Scanner & Portfolio-Manager")

# --- ERKLÄRUNG DER TURNAROUND-LOGIK & REGELWERK ---
with st.expander("ℹ️ Wann entsteht ein Kaufsignal? (Hier klicken)"):
    st.markdown("""
    ### 🔄 Kriterien für ein Kaufsignal
    1. **RSI < 35:** Der Sektor-ETF ist kurzfristig überverkauft.
    2. **Kurs > GD200:** Der ETF notiert über seiner langfristigen 200-Tage-Linie.
    3. **Intakter Grundtrend:** 
        * **EMA50 > GD200:** Mittelfristiger Trend liegt über dem Langfristtrend.
        * **GD200 steigt an:** GD200 heute ist höher als der GD200 vor 10 Handelstagen (`GD200 v10T`).
    4. **Turnaround-Logik bestätigt:** Kein fallendes Messer!
    
    ### 🔄 Turnaround-Logik (Erholung vor dem Kauf)
    Ein ETF mit **RSI < 35** ist erst dann ein **echtes Kaufsignal**, wenn der Verkaufsdruck nachlässt und Käufer in den Markt zurückkehren.
    
    Das Skript prüft automatisch zwei Wege:
    
    1. **Intraday-Turnaround (Einstieg am selben Tag):**
       * Der RSI liegt aktuell **unter 35** **UND** der Kurs hat sich um mindestens **+0,5 % vom heutigen Tagestief erholt**.
    
    2. **Vortags-Turnaround (Klassischer V-Haken):**
       * Der RSI lag **gestern bereits unter 35** **UND** der RSI ist heute höher als gestern (`RSI_heute > RSI_gestern`).
    
    *Solange keiner dieser beiden Fälle zutrifft, gilt der Wert als **'fallendes Messer'** und das Kaufsignal wird blockiert.*
    """)

# --- NEU: ERKLÄRBLOCK FÜR VERKAUFSTRANCHEN & ATR-STOP ---
with st.expander("📊 Wie werden Kauf- & Verkaufstranchen gesetzt? (Regelwerk)", expanded=False):
    col_t1, col_t2 = st.columns(2)
    
    with col_t1:
        st.markdown("""
        ### 🟢 Tranche 1 (50 % Verkauf)
        * **Ziel:** Erreichen der **EMA50-Linie** (Mean Reversion).
        * **Sinn:** Schnelle Teilgewinnmitnahme nach dem Dip, um das Risiko des Gesamtrades sofort drastisch zu reduzieren.
        """)
        
    with col_t2:
        st.markdown("""
        ### 🎯 Tranche 2 (50 % Verkauf)
        * **Ziel:** **52-Wochen-Hoch (-1 % Puffer)**.
        * **Sinn:** Realistisches Maximalziel für einen 1–3 Monats-Swing-Trade in volatilen Sektoren.
        """)
        
    st.divider()
    
    st.markdown("""
    ### 🛡️ Die Absicherung: Der ATR-Stop Loss
    Nach dem Verkauf von Tranche 1 wird die verbleibende Position dynamisch über die **Average True Range (ATR)** abgesichert:
    
    $$\\text{Stop-Loss} = \\text{Aktueller Kurs / Höchstkurs} - (2 \\times \\text{ATR}_{14})$$
    
    * **Was ist die ATR?** Die ATR misst die durchschnittliche tägliche Schwankungsbreite (Volatilität) der letzten 14 Handelstage.
    * **Warum ATR statt starrer Stoppkurs?** 
      * Bei **stark schwankenden ETFs** erweitert sich der Stopp automatisch, um nicht durch normales Marktrauschen (*Shakeout*) herausgeworfen zu werden.
      * Bei **ruhigeren ETFs** zieht der Stopp enger an, um erzielte Gewinne konsequent zu sichern.
      * Der Stopp zieht bei neuen Höchstkursen **automatisch nach oben mit** (*Trailing Stop*).
    """)

if "letztes_update" in st.session_state:
    st.caption(
        f"⏱️ **Letzter allgemeiner Scan-Stand:**"
        f" {st.session_state['letztes_update']}"
    )

st.sidebar.header("⚙️ Steuerung")

if st.sidebar.button("🔄 Daten aktualisieren", use_container_width=True):
    st.cache_data.clear()
    if "kauf_signale" in st.session_state:
        del st.session_state["kauf_signale"]
    st.rerun()

etfs = parse_isin_file("isin.txt")

portfolio_isins = [p["isin"] for p in PORTFOLIO]
etfs_isins = {e["isin"] for e in etfs}
for p in PORTFOLIO:
    if p["isin"] not in etfs_isins:
        etfs.append({"sektor": "Portfolio", "isin": p["isin"]})

st.sidebar.info(f"📋 **{len(etfs)} ETFs** werden überwacht.")

# --- PARALLELER DATEN-SCANNER ---
if "kauf_signale" not in st.session_state:
    kauf_signale, watchlist_signale, fehlgeschlagene_etfs = [], [], []
    letzter_zeitstempel = "k.A."
    progress_bar = st.progress(0, text="⚡ Lade Kursdaten (Parallel-Scan)...")

    total_etfs = len(etfs)
    completed_count = 0

    def load_etf_data(item):
        data, ticker = berechne_indikatoren(item["isin"])
        return item, data, ticker

    # Parallelisierung mit bis zu 8 Threads
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(load_etf_data, item) for item in etfs]

        for future in as_completed(futures):
            completed_count += 1
            item, data, ticker = future.result()

            progress_bar.progress(
                completed_count / total_etfs,
                text=f"⚡ Scanne ETF {completed_count}/{total_etfs}: {item['isin']}",
            )

            if not data:
                fehlgeschlagene_etfs.append({
                    "Sektor": item["sektor"],
                    "ISIN": item["isin"],
                    "Ticker": ticker,
                })
                continue

            if data.get("yahoo_zeit") and data["yahoo_zeit"] != "k.A.":
                letzter_zeitstempel = data["yahoo_zeit"]

            c, rsi, rsi35, gd200, gd200_10d, ema50, atr14, high_52w, gd200_steigt, perf_1w, score, messer = (
                data["close"],
                data["rsi"],
                data["rsi35_preis"],
                data["gd200"],
                data["gd200_vor_10d"],
                data["ema50"],
                data["atr14"],
                data["high_52w"],
                data["gd200_steigt"],
                data["perf_1w"],
                data["dip_score"],
                data["is_fallendes_messer"],
            )

            grundtrend_ok = ema50 > gd200 and gd200_steigt
            gd200_abstand = ((gd200 - c) / c) * 100
            gd200_10d_abstand = ((gd200_10d - c) / c) * 100
            rsi35_abstand = ((rsi35 - c) / c) * 100

            is_kauf = grundtrend_ok and (rsi < 35) and (c > gd200) and (not messer)
            is_watch = rsi < 40 and c >= (gd200 * 0.97)
            is_in_portfolio = item["isin"] in portfolio_isins

            entry = {
                "Sektor": item["sektor"],
                "ISIN": item["isin"],
                "Ticker": ticker,
                "Kurs": c,
                "RSI": round(rsi, 1),
                "RSI 35 Preis": rsi35,
                "RSI35_Abstand": rsi35_abstand,
                "GD200": gd200,
                "GD200_Abstand": gd200_abstand,
                "GD200_10d": gd200_10d,
                "GD200_10d_Abstand": gd200_10d_abstand,
                "GD200_steigt": gd200_steigt,
                "EMA50": ema50,
                "ATR14": atr14,
                "High_52W": high_52w,
                "1W Perf.": perf_1w,
                "Dip Score": score,
                "Zeitstempel": data["yahoo_zeit"],
                "Ist_Kaufsignal": is_kauf,
                "Ist_Portfolio": is_in_portfolio,
            }

            if is_kauf:
                kauf_signale.append(entry)

            if is_kauf or is_watch or is_in_portfolio:
                watchlist_signale.append(entry)

    progress_bar.empty()
    st.session_state["kauf_signale"] = kauf_signale
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

# TABS REGISTER
tab1, tab2, tab3 = st.tabs([
    f"🔥 Kaufsignale ({len(st.session_state.get('kauf_signale', []))})",
    f"📋 Watchlist ({len(st.session_state.get('watchlist_signale', []))})",
    f"💼 Mein Portfolio ({len(PORTFOLIO)})",
])

# --- TAB 1: KAUFSIGNALE ---
with tab1:
    kauf = st.session_state.get("kauf_signale", [])
    if kauf:
        st.success(
            f"**{len(kauf)} Kaufsignal(e) gefunden!** (RSI < 35, Kurs > GD200,"
            " Turnaround bestätigt)"
        )
        for item in kauf:
            rsi_bg = "#d4edda" if item["RSI"] < 35 else "#f8d7da"
            rsi_fg = "#155724" if item["RSI"] < 35 else "#721c24"

            gd_bg = "#d4edda" if item["GD200"] <= item["Kurs"] else "#f8d7da"
            gd_fg = "#155724" if item["GD200"] <= item["Kurs"] else "#721c24"

            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)

                col1.markdown(f"**{item['Sektor']}**")
                col1.caption(f"Ticker: `{item['Ticker']}` | {item['ISIN']}")

                col2.markdown(
                    f"""
                    <div style="background-color: {rsi_bg}; color: {rsi_fg}; padding: 8px 12px; border-radius: 6px; text-align: center;">
                        <div style="font-size: 0.75em; font-weight: bold; text-transform: uppercase;">Aktueller Kurs / RSI</div>
                        <div style="font-size: 1.15em; font-weight: bold;">{item['Kurs']:.2f} €</div>
                        <div style="font-size: 0.85em;">RSI: <b>{item['RSI']}</b> ({item.get('Zeitstempel', 'k.A.')})</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                col3.markdown(
                    f"""
                    <div style="background-color: {gd_bg}; color: {gd_fg}; padding: 8px 12px; border-radius: 6px; text-align: center;">
                        <div style="font-size: 0.75em; font-weight: bold; text-transform: uppercase;">GD200</div>
                        <div style="font-size: 1.15em; font-weight: bold;">{item['GD200']:.2f} €</div>
                        <div style="font-size: 0.85em;">{item['GD200_Abstand']:+.2f}% zum Kurs</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                col4.metric(
                    "Kauf-Limit für RSI <35",
                    f"{item['RSI 35 Preis']:.2f} €",
                    f"{item['RSI35_Abstand']:+.2f}% zum Kurs",
                )
    else:
        st.info("Aktuell keine strikten Kaufsignale.")

# --- TAB 2: WATCHLIST ---
with tab2:
    watch = st.session_state.get("watchlist_signale", [])
    if watch:
        st.caption(
            "💡 **Farblegende:** 🥇/🥈/🥉 **Top Dip-Scores** | 🟩 **GD200:**"
            " Kurs > GD200 | 🟩 **GD200 v10T:** GD200 steigt | 🟩 **EMA50:**"
            " EMA50 > GD200 | 🟩 **RSI:** RSI < 35 | 🟪 **Lila:** Im Portfolio"
        )

        col_sort1, col_sort2 = st.columns([2, 2])
        with col_sort1:
            sort_kriterium = st.selectbox(
                "🏆 Watchlist Sortierung nach:",
                [
                    "🔥 RSI (Niedrigster zuerst)",
                    "🚀 Dip-Potential Score",
                    "🎯 Abstand zu RSI 35 Zielkurs",
                    "📊 Nähe zu GD200-Unterstützung",
                    "📉 Stärkster 1W-Rücksetzer",
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
        elif sort_kriterium == "📉 Stärkster 1W-Rücksetzer":
            df_watch = df_watch.sort_values(by="1W Perf.", ascending=True)

        df_watch = df_watch.reset_index(drop=True)

        def format_ticker_rank(row):
            t = row["Ticker"]
            rank = row["Dip_Rank"]
            if rank == 1:
                return f"🥇 {t}"
            elif rank == 2:
                return f"🥈 {t}"
            elif rank == 3:
                return f"🥉 {t}"
            else:
                return t

        display_df = pd.DataFrame()
        display_df["Sektor"] = df_watch["Sektor"]
        display_df["ISIN"] = df_watch["ISIN"]
        display_df["Ticker"] = [
            format_ticker_rank(df_watch.iloc[i]) for i in range(len(df_watch))
        ]

        display_df["Kurs"] = df_watch["Kurs"].map(lambda x: f"{x:.2f} €")
        display_df["RSI"] = df_watch["RSI"].map(lambda x: f"{x:.1f}")

        display_df["RSI 35 Preis"] = df_watch.apply(
            lambda r: (
                f"{r['RSI 35 Preis']:.2f} €"
                f" ({((r['RSI 35 Preis'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
            ),
            axis=1,
        )

        display_df["GD200"] = df_watch.apply(
            lambda r: (
                f"{r['GD200']:.2f} €"
                f" ({((r['GD200'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
            ),
            axis=1,
        )

        display_df["GD200 v10T"] = df_watch.apply(
            lambda r: (
                f"{r['GD200_10d']:.2f} €"
                f" ({((r['GD200_10d'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
            ),
            axis=1,
        )

        display_df["EMA50"] = df_watch.apply(
            lambda r: (
                f"{r['EMA50']:.2f} €"
                f" ({((r['EMA50'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
            ),
            axis=1,
        )

        display_df["1W Perf."] = df_watch["1W Perf."].map(
            lambda x: f"{x:+.2f}%"
        )
        display_df["Dip Score"] = df_watch["Dip Score"].map(
            lambda x: f"🔥 {x:.1f}"
        )
        display_df["Zeitstempel"] = df_watch["Zeitstempel"]

        def style_watchlist_cells(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for idx in df.index:
                row_raw = df_watch.loc[idx]

                if row_raw.get("Ist_Portfolio", False):
                    for col in df.columns:
                        styles.loc[idx, col] = (
                            "background-color: #e8daef; color: #111111;"
                        )

                if row_raw["GD200"] < row_raw["Kurs"]:
                    styles.loc[idx, "GD200"] = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "GD200"] = (
                        "background-color: #f8d7da; color: #721c24;"
                        " font-weight: bold;"
                    )

                if row_raw["GD200_steigt"]:
                    styles.loc[idx, "GD200 v10T"] = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "GD200 v10T"] = (
                        "background-color: #f8d7da; color: #721c24;"
                        " font-weight: bold;"
                    )

                if row_raw["EMA50"] > row_raw["GD200"]:
                    styles.loc[idx, "EMA50"] = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "EMA50"] = (
                        "background-color: #f8d7da; color: #721c24;"
                        " font-weight: bold;"
                    )

                if row_raw["RSI"] < 35.0:
                    styles.loc[idx, "RSI"] = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "RSI"] = (
                        "background-color: #f8d7da; color: #721c24;"
                        " font-weight: bold;"
                    )

                dip_rank = row_raw.get("Dip_Rank", 999)
                if dip_rank == 1:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #fef9e7; color: #7d6608;"
                        " font-weight: bold;"
                    )
                elif dip_rank == 2:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #f2f3f4; color: #424949;"
                        " font-weight: bold;"
                    )
                elif dip_rank == 3:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #fbeee6; color: #7e5109;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "Ticker"] = "font-weight: bold;"

                styles.loc[idx, "Dip Score"] = (
                    "font-weight: bold; text-align: center;"
                )

            return styles

        styled_df = display_df.style.apply(style_watchlist_cells, axis=None)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    else:
        st.write("Keine ETFs in der erweiterten Watchlist.")

# --- TAB 3: HIGH-END PORTFOLIO MANAGER ---
with tab3:
    st.subheader("📊 Aktive Positionen & Ausstiegs-Manager")

    for pos in PORTFOLIO:
        try:
            df = yf.download(pos["ticker"], period="5y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Indikatoren berechnen
            delta = df["Close"].diff()
            gain = (
                delta.where(delta > 0, 0).ewm(alpha=1 / 14, adjust=False).mean()
            )
            loss = (
                (-delta.where(delta < 0, 0))
                .ewm(alpha=1 / 14, adjust=False)
                .mean()
            )
            rs = gain / loss
            df["RSI"] = 100 - (100 / (1 + rs))

            df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
            df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()

            # ATR 14 Berechnen
            high = df["High"] if "High" in df else df["Close"]
            low = df["Low"] if "Low" in df else df["Close"]
            high_low = high - low
            high_close = (high - df["Close"].shift()).abs()
            low_close = (low - df["Close"].shift()).abs()
            true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["ATR"] = true_range.rolling(window=14).mean()

            current_price = float(df["Close"].iloc[-1])
            rsi_today = float(df["RSI"].iloc[-1])
            ema50_today = float(df["EMA50"].iloc[-1])
            ema20_today = float(df["EMA20"].iloc[-1])
            atr_today = float(df["ATR"].iloc[-1])

            # 52-Wochen-Hoch (1 Jahr = ca. 252 Handelstage)
            df_1y = df.tail(252)
            high_52w = float(df_1y["High"].max()) if "High" in df_1y else float(df_1y["Close"].max())
            t2_target_price = high_52w * 0.99
            dist_52w_pct = ((current_price - t2_target_price) / current_price) * 100

            is_partially_sold = pos.get("partially_sold", False)
            buy_price = float(pos["buy_price"])

            # NEU: Dynamic ATR-Stop Loss (2x ATR Abstand vom aktuellen Kurs)
            atr_stop_loss = current_price - (2.0 * atr_today)
            dist_stop_loss_pct = ((current_price - atr_stop_loss) / current_price) * 100

            investment = buy_price * pos["shares"]
            current_value = current_price * pos["shares"]
            profit_eur = current_value - investment
            profit_pct = ((current_price - buy_price) / buy_price) * 100

            days_held = 0
            if pos.get("buy_date"):
                buy_date = pd.to_datetime(pos["buy_date"])
                today_date = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
                days_held = (today_date - buy_date).days

            # --- SIGNAL-LOGIK & BERICHTS-ERSTELLUNG ---
            signal_type = "info"
            
            if not is_partially_sold:
                # Tranche 1 noch NICHT erfolgt
                if current_price >= ema50_today:
                    signal_type = "success"
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    signal = (
                        "🎯 **TRANCHE 1 ERREICHT: Jetzt 50% VERKAUFEN!**\n\n"
                        "👉 In `portfolio.py` anpassen: `'partially_sold': True` &"
                        f" `'t1_sell_date': '{today_str}'` & `'t1_sell_price': {current_price:.2f}`"
                    )
                else:
                    dist_ema50_pct = ((current_price - ema50_today) / current_price) * 100
                    signal = (
                        f"🟢 **100% IM DEPOT:** Warten auf Tranche 1 am EMA50 bei **{ema50_today:.2f} €** "
                        f"(Abstand: {dist_ema50_pct:+.2f}%)"
                    )
            else:
                # Tranche 1 BEREITS ERFOLGT -> Nur hier wird Tranche 2 (52W-Hoch) & ATR-Stop ausgewertet
                if current_price >= t2_target_price:
                    signal_type = "success"
                    signal = (
                        f"🚀 **TRANCHE 2 ZIEL (52W-Hoch -1%) ERREICHT!**\n\n"
                        f"👉 52W-Hoch liegt bei {high_52w:.2f} € → Restliche 50% Verkaufen bei **{t2_target_price:.2f} €**!"
                    )
                elif current_price <= atr_stop_loss:
                    signal_type = "error"
                    signal = (
                        f"🚨 **ATR-STOP LOSS UNTERSCHRITTEN!**\n\n"
                        f"Kurs ({current_price:.2f} €) ist unter den dynamischen ATR-Stop ({atr_stop_loss:.2f} €) gefallen. "
                        f"Restliche 50% glattstellen zur Gewinnabsicherung!"
                    )
                else:
                    signal = (
                        f"🛡️ **2. HÄLFTE LÄUFT:** Warten auf 52W-Hoch-Verkauf bei **{t2_target_price:.2f} €** "
                        f"oder Absicherung via ATR-Stop bei **{atr_stop_loss:.2f} €**."
                    )

            # --- ANZEIGE IN CONTAINERN ---
            with st.container(border=True):
                st.markdown(
                    f"### {pos['name']} (`{pos['ticker']}`) — ISIN: `{pos['isin']}`"
                )

                # Zeile 1: Basis-Kennzahlen + Aktueller RSI
                c1, c2, c3, c4 = st.columns(4)
                c1.metric(
                    "Depot-Status",
                    "✅ 50% Verkauft" if is_partially_sold else "⏳ 100% Im Depot",
                    f"{days_held} Tage gehalten",
                )
                c2.metric(
                    "Kaufkurs / Aktuell",
                    f"{buy_price:.2f} €",
                    f"Aktuell: {current_price:.2f} €",
                )
                c3.metric(
                    "Performance Gesamt",
                    f"{profit_pct:+.2f}%",
                    f"{profit_eur:+.2f} €",
                )
                c4.metric(
                    "Aktueller RSI / ATR",
                    f"RSI: {rsi_today:.1f}",
                    f"ATR(14): {atr_today:.2f} €",
                )

                st.markdown("---")

                # Zeile 2: Tranche 1 Ziel, Tranche 2 (52W-Hoch), ATR-Stop Loss
                t1, t2, t3 = st.columns(3)
                
                t1.metric(
                    "Tranche 1 Ziel (EMA50)",
                    f"{ema50_today:.2f} €",
                    f"{((ema50_today - current_price) / current_price) * 100:+.2f}% zum Kurs",
                )

                t2.metric(
                    "Tranche 2: 52W-Hoch (Ziel: -1%)",
                    f"{t2_target_price:.2f} €",
                    f"52W-Hoch: {high_52w:.2f} € ({dist_52w_pct:+.2f}%)",
                )
                
                t3.metric(
                    "Tranche 2: ATR-Stop Loss (2x ATR)",
                    f"{atr_stop_loss:.2f} €",
                    f"Puffer: -{(2.0 * atr_today):.2f} € ({((atr_stop_loss - current_price) / current_price) * 100:+.2f}%)",
                    help="Dynamischer Trailing-Stop: Aktueller Kurs minus 2x ATR(14) Volatilität"
                )

                # Signal-Box
                if signal_type == "success":
                    st.success(signal)
                elif signal_type == "error":
                    st.error(signal)
                elif signal_type == "warning":
                    st.warning(signal)
                else:
                    st.info(signal)

        except Exception as e:
            st.error(
                f"Fehler beim Laden von Position {pos.get('ticker')}: {e}"
            )
