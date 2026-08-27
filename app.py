from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
import warnings
import re
import os
import json
import time
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import importlib
import portfolio
importlib.reload(portfolio)

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
# PERSISTENTER ISIN-ZU-TICKER CACHE
# ==========================================
CACHE_FILE = "isin_cache.json"


def load_isin_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_isin_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


ISIN_CACHE = load_isin_cache()

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
    "IE00BGV5VR99": ["XMOV.DE"],
    "IE00BCHWNS19": ["XUEN.DE"],
    "IE00BM67HL84": ["XDWF.DE"],
    "IE00BKLF1R75": ["W1TA.DE"],
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
    if isin in MANUAL_TICKERS:
        return MANUAL_TICKERS[isin][0]
    if isin in ISIN_CACHE and ISIN_CACHE[isin]:
        return ISIN_CACHE[isin]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    endpoints = [
        f"https://query1.finance.yahoo.com/v1/finance/search?q={isin}&quotesCount=5",
        f"https://query2.finance.yahoo.com/v1/finance/search?q={isin}&quotesCount=5",
    ]

    for url in endpoints:
        try:
            time.sleep(0.05)  # Minimale Verpufferung gegen IP-Sperre
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                quotes = response.json().get("quotes", [])
                found_symbol = None
                for q in quotes:
                    symbol = q.get("symbol", "")
                    if symbol.endswith(".DE"):
                        found_symbol = symbol
                        break
                if not found_symbol:
                    for suffix in [".L", ".PA", ".AS", ".MI", ".SW", ".F"]:
                        for q in quotes:
                            symbol = q.get("symbol", "")
                            if symbol.endswith(suffix):
                                found_symbol = symbol
                                break
                        if found_symbol:
                            break
                if not found_symbol and quotes:
                    for q in quotes:
                        symbol = q.get("symbol", "")
                        if symbol and not any(
                            symbol.endswith(bad) for bad in [".SG", ".BE", ".MU", ".DU"]
                        ):
                            found_symbol = symbol
                            break

                if found_symbol:
                    ISIN_CACHE[isin] = found_symbol
                    save_isin_cache(ISIN_CACHE)
                    return found_symbol
        except Exception:
            continue

    return None


@st.cache_data(ttl=1800)
def lade_alle_kursdaten_batch(ticker_liste):
    unique_tickers = list(set([t for t in ticker_liste if t]))
    if not unique_tickers:
        return {}

    chunk_size = 35
    ergebnisse = {}

    for i in range(0, len(unique_tickers), chunk_size):
        chunk = unique_tickers[i : i + chunk_size]
        try:
            df_batch = yf.download(
                tickers=chunk,
                period="2y",
                auto_adjust=False,
                group_by="ticker",
                progress=False,
                threads=True,
            )

            if len(chunk) == 1:
                t = chunk[0]
                if not df_batch.empty:
                    ergebnisse[t] = df_batch
            else:
                for t in chunk:
                    if (
                        isinstance(df_batch.columns, pd.MultiIndex)
                        and t in df_batch.columns.levels[0]
                    ):
                        sub_df = df_batch[t].dropna(subset=["Close"])
                        if not sub_df.empty and len(sub_df) >= 200:
                            ergebnisse[t] = sub_df
        except Exception:
            pass

    # Einzel-Fallback für fehlende Ticker
    for t in unique_tickers:
        if t not in ergebnisse:
            try:
                t_obj = yf.Ticker(t)
                df_single = t_obj.history(period="2y", auto_adjust=False)
                if isinstance(df_single.columns, pd.MultiIndex):
                    df_single.columns = df_single.columns.get_level_values(0)
                df_single = df_single.dropna(subset=["Close"])
                if not df_single.empty and len(df_single) >= 200:
                    ergebnisse[t] = df_single
            except Exception:
                pass

    return ergebnisse


def berechne_indikatoren_aus_df(df, ticker_symbol):
    data = df.copy()

    # Echtzeit-Aktualisierung des letzten Kurses
    yahoo_zeit = "k.A."
    try:
        t_obj = yf.Ticker(ticker_symbol)
        fi = getattr(t_obj, "fast_info", None)
        if fi and getattr(fi, "last_price", None):
            live_price = fi.last_price
            if live_price and not pd.isna(live_price) and live_price > 0:
                data.loc[data.index[-1], "Close"] = float(live_price)

        if fi and getattr(fi, "last_trade_time", None):
            ts = pd.to_datetime(fi.last_trade_time, unit="s", utc=True).tz_convert(
                "Europe/Berlin"
            )
            yahoo_zeit = ts.strftime("%H:%M Uhr (%d.%m.)")
    except Exception:
        pass

    close = data["Close"].ffill().dropna()
    low = data["Low"].ffill().dropna() if "Low" in data else close
    high = data["High"].ffill().dropna() if "High" in data else close

    if yahoo_zeit == "k.A.":
        last_dt = close.index[-1]
        yahoo_zeit = (
            last_dt.strftime("%d.%m.%Y") if hasattr(last_dt, "strftime") else "k.A."
        )

    high_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())

    gd200 = close.rolling(window=200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

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
    gd200_vor_10d = float(gd200.iloc[-11]) if len(gd200) >= 11 else gd200_heute
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

    return {
        "close": c_today,
        "rsi": rsi_today,
        "rsi35_preis": float(rsi35_preis),
        "gd200": gd200_heute,
        "gd200_vor_10d": gd200_vor_10d,
        "ema50": ema50_heute,
        "high_52w": high_52w,
        "gd200_steigt": gd200_steigt,
        "perf_1w": perf_1w,
        "dip_score": dip_score,
        "is_fallendes_messer": is_fallendes_messer,
        "yahoo_zeit": yahoo_zeit,
    }


# ==========================================
# APP USER INTERFACE
# ==========================================
st.title("📈 ETF Dip-Scanner & Portfolio-Manager")

with st.expander("ℹ️ Wann entsteht ein Kaufsignal? (Hier klicken)"):
    st.markdown("""
    ### 🔄 Kriterien für ein Kaufsignal
    1. **RSI < 35:** Der Sektor-ETF ist kurzfristig überverkauft.
    2. **Kurs > GD200:** Der ETF notiert über seiner langfristigen 200-Tage-Linie.
    3. **Intakter Grundtrend:** 
        * **EMA50 > GD200:** Mittelfristiger Trend liegt über dem Langfristtrend.
        * **GD200 steigt an:** GD200 heute ist höher als der GD200 vor 10 Handelstagen (`GD200 v10T`).
    4. **Turnaround-Logik bestätigt:** Kein fallendes Messer!
    """)

with st.expander("📊 Wie werden Kauf- & Verkaufstranchen gesetzt? (Regelwerk)", expanded=False):
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown("""
        ### 🟢 Tranche 1 (50 % Verkauf)
        * **Ziel:** Erreichen der **EMA50-Linie** (Mean Reversion).
        * **Sinn:** Schnelle Teilgewinnmitnahme nach dem Dip zur Risiko-Reduzierung.
        """)
    with col_t2:
        st.markdown("""
        ### 🎯 Tranche 2 (50 % Verkauf)
        * **Ziel:** **52-Wochen-Hoch (-1 % Puffer)**.
        * **Absicherung:** Dynamic Stop Loss bei **Kaufkurs + 50 % des T1-Gewinns**.
        """)

if "letztes_update" in st.session_state:
    st.caption(
        f"⏱️ **Letzter allgemeiner Scan-Stand:** {st.session_state['letztes_update']}"
    )

st.sidebar.header("⚙️ Steuerung")

if st.sidebar.button("🔄 Daten aktualisieren", use_container_width=True):
    st.cache_data.clear()
    if "kauf_signale" in st.session_state:
        del st.session_state["kauf_signale"]
    st.rerun()

etfs = parse_isin_file("isin.txt")
portfolio_isins = [p["isin"] for p in PORTFOLIO if not p.get("sold", False)]
etfs_isins = {e["isin"] for e in etfs}
for p in PORTFOLIO:
    if p["isin"] not in etfs_isins:
        etfs.append({"sektor": "Portfolio", "isin": p["isin"]})

st.sidebar.info(f"📋 **{len(etfs)} ETFs** werden überwacht.")

# --- BATCH-DATEN SCANNER ---
if "kauf_signale" not in st.session_state:
    progress_bar = st.progress(0, text="🔍 Schritt 1/2: Zuordnung ISIN ➔ Ticker...")

    isin_ticker_map = {}
    for idx, item in enumerate(etfs):
        isin = item["isin"]
        ticker = isin_zu_ticker(isin)
        isin_ticker_map[isin] = ticker
        progress_bar.progress((idx + 1) / len(etfs), text=f"🔍 Zuordnung: {isin} ➔ {ticker or 'N/A'}")

    progress_bar.progress(1.0, text="⚡ Schritt 2/2: Lade Kursdaten im Stapel...")

    alle_ticker = list(set([t for t in isin_ticker_map.values() if t]))
    geladene_dfs = lade_alle_kursdaten_batch(alle_ticker)

    kauf_signale, watchlist_signale, fehlgeschlagene_etfs = [], [], []
    letzter_zeitstempel = "k.A."

    for item in etfs:
        isin = item["isin"]
        candidates = MANUAL_TICKERS[isin] if isin in MANUAL_TICKERS else []
        t_found = isin_ticker_map.get(isin)
        if t_found and t_found not in candidates:
            candidates.append(t_found)

        df_etf = None
        erfolgreicher_ticker = None

        for cand in candidates:
            if cand in geladene_dfs:
                df_etf = geladene_dfs[cand]
                erfolgreicher_ticker = cand
                break

        if df_etf is None or df_etf.empty:
            fehlgeschlagene_etfs.append({
                "Sektor": item["sektor"],
                "ISIN": isin,
                "Ticker": candidates[0] if candidates else "N/A",
            })
            continue

        data = berechne_indikatoren_aus_df(df_etf, erfolgreicher_ticker)

        if data.get("yahoo_zeit") and data["yahoo_zeit"] != "k.A.":
            letzter_zeitstempel = data["yahoo_zeit"]

        c, rsi, rsi35, gd200, gd200_10d, ema50, gd200_steigt, perf_1w, score, messer = (
            data["close"],
            data["rsi"],
            data["rsi35_preis"],
            data["gd200"],
            data["gd200_vor_10d"],
            data["ema50"],
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
        is_in_portfolio = isin in portfolio_isins

        entry = {
            "Sektor": item["sektor"],
            "ISIN": isin,
            "Ticker": erfolgreicher_ticker,
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
    st.session_state["geladene_dfs"] = geladene_dfs

# --- FEHLERMELDUNGEN ANZEIGEN ---
failed_list = st.session_state.get("fehlgeschlagene_etfs", [])
if failed_list:
    with st.expander(f"⚠️ Hinweis: {len(failed_list)} ETF(s) konnten nicht geladen werden"):
        st.warning(
            "Für folgende ISINs konnte Yahoo Finance keine Kursdaten liefern (z.B. illiquide Exoten):"
        )
        st.dataframe(pd.DataFrame(failed_list), use_container_width=True, hide_index=True)

aktive_positionen = [p for p in PORTFOLIO if not p.get("sold", False)]
historie_positionen = [p for p in PORTFOLIO if p.get("partially_sold", False) or p.get("sold", False)]

tab1, tab2, tab3, tab4 = st.tabs([
    f"🔥 Kaufsignale ({len(st.session_state.get('kauf_signale', []))})",
    f"📋 Watchlist ({len(st.session_state.get('watchlist_signale', []))})",
    f"💼 Mein Portfolio ({len(aktive_positionen)})",
    f"📜 Historie ({len(historie_positionen)})",
])

# --- TAB 1: KAUFSIGNALE ---
with tab1:
    kauf = st.session_state.get("kauf_signale", [])
    if kauf:
        st.success(
            f"**{len(kauf)} Kaufsignal(e) gefunden!** (RSI < 35, Kurs > GD200, Turnaround bestätigt)"
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
            "💡 **Farblegende:** 🥇/🥈/🥉 **Top Dip-Scores** | 🟩 **GD200:** Kurs > GD200 | "
            "🟩 **GD200 v10T:** GD200 steigt | 🟩 **EMA50:** EMA50 > GD200 | 🟩 **RSI:** RSI < 35 | 🟪 **Lila:** Im Portfolio"
        )

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
        df_watch = df_watch.drop_duplicates(subset=["Ticker_Base"], keep="first")

        df_watch["Dip_Rank"] = range(1, len(df_watch) + 1)

        if sort_kriterium == "🔥 RSI (Niedrigster zuerst)":
            df_watch = df_watch.sort_values(by="RSI", ascending=True)
        elif sort_kriterium == "🚀 Dip-Potential Score":
            df_watch = df_watch.sort_values(by="Dip Score", ascending=False)
        elif sort_kriterium == "🎯 Abstand zu RSI 35 Zielkurs":
            df_watch = df_watch.sort_values(by="RSI35_Abstand", ascending=False)
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

        display_df["1W Perf."] = df_watch["1W Perf."].map(lambda x: f"{x:+.2f}%")
        display_df["Dip Score"] = df_watch["Dip Score"].map(lambda x: f"🔥 {x:.1f}")
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
                        "background-color: #d4edda; color: #155724; font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "GD200"] = (
                        "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                    )

                if row_raw["GD200_steigt"]:
                    styles.loc[idx, "GD200 v10T"] = (
                        "background-color: #d4edda; color: #155724; font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "GD200 v10T"] = (
                        "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                    )

                if row_raw["EMA50"] > row_raw["GD200"]:
                    styles.loc[idx, "EMA50"] = (
                        "background-color: #d4edda; color: #155724; font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "EMA50"] = (
                        "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                    )

                if row_raw["RSI"] < 35.0:
                    styles.loc[idx, "RSI"] = (
                        "background-color: #d4edda; color: #155724; font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "RSI"] = (
                        "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                    )

                dip_rank = row_raw.get("Dip_Rank", 999)
                if dip_rank == 1:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #fef9e7; color: #7d6608; font-weight: bold;"
                    )
                elif dip_rank == 2:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #f2f3f4; color: #424949; font-weight: bold;"
                    )
                elif dip_rank == 3:
                    styles.loc[idx, "Ticker"] = (
                        "background-color: #fbeee6; color: #7e5109; font-weight: bold;"
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

    if not aktive_positionen:
        st.info("Aktuell keine aktiven offene Positionen im Portfolio.")

    geladene_dfs = st.session_state.get("geladene_dfs", {})

    for pos in aktive_positionen:
        try:
            isin = pos["isin"]
            candidates = MANUAL_TICKERS[isin] if isin in MANUAL_TICKERS else []
            t_found = isin_zu_ticker(isin)
            if t_found and t_found not in candidates:
                candidates.append(t_found)

            df_pos = None
            ticker_used = pos.get("ticker", "N/A")

            for cand in candidates:
                if cand in geladene_dfs:
                    df_pos = geladene_dfs[cand]
                    ticker_used = cand
                    break

            if df_pos is None:
                st.error(f"Keine Kursdaten für {pos['name']} ({pos['isin']}) verfügbar.")
                continue

            data = berechne_indikatoren_aus_df(df_pos, ticker_used)

            current_price = data["close"]
            rsi_today = data["rsi"]
            ema50_today = data["ema50"]
            high_52w = data["high_52w"]

            ath_target_price = high_52w * 0.99
            dist_ath_pct = ((current_price - ath_target_price) / current_price) * 100

            is_partially_sold = pos.get("partially_sold", False)
            buy_price = float(pos["buy_price"])

            if is_partially_sold and pos.get("t1_sell_price"):
                t1_price = float(pos["t1_sell_price"])
                t1_profit = t1_price - buy_price
                stop_loss_limit = buy_price + (t1_profit / 2.0)
                sl_label = "Tranche 2: Stop Loss Limit"
            else:
                if ema50_today > buy_price:
                    stop_loss_limit = buy_price + ((ema50_today - buy_price) / 2.0)
                else:
                    stop_loss_limit = buy_price
                sl_label = "Tranche 2: Stop Loss (Vorschau)"

            dist_stop_loss_pct = ((current_price - stop_loss_limit) / current_price) * 100

            investment = buy_price * pos["shares"]
            current_value = current_price * pos["shares"]
            profit_eur = current_value - investment
            profit_pct = ((current_price - buy_price) / buy_price) * 100

            days_held = 0
            if pos.get("buy_date"):
                buy_date = pd.to_datetime(pos["buy_date"])
                today_date = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
                days_held = (today_date - buy_date).days

            signal_type = "info"
            if not is_partially_sold:
                if current_price >= ema50_today:
                    signal_type = "success"
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    signal = (
                        "🎯 **TRANCHE 1 ERREICHT: Jetzt 50% VERKAUFEN!**\n\n"
                        "👉 In `portfolio.py` anpassen: `'partially_sold': True` &"
                        f" `'t1_sell_date': '{today_str}'` &"
                        f" `'t1_sell_price': {current_price:.2f}`"
                    )
                else:
                    dist_ema50_pct = ((current_price - ema50_today) / current_price) * 100
                    signal = (
                        f"🟢 **100% IM DEPOT:** Warten auf Tranche 1 am EMA50 bei **{ema50_today:.2f} €** "
                        f"(Abstand: {dist_ema50_pct:+.2f}%)"
                    )
            else:
                if current_price >= ath_target_price:
                    signal_type = "success"
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    signal = (
                        f"🚀 **TRANCHE 2 ZIEL (52W-HOCH -1%) ERREICHT!**\n\n"
                        f"👉 52-Wochen-Hoch liegt bei {high_52w:.2f} € → Restliche 50% Verkaufen bei **{ath_target_price:.2f} €**!\n"
                        f"👉 Nach Verkauf in `portfolio.py` eintragen: `'sold': True`, `'t2_sell_date': '{today_str}'`, `'t2_sell_price': {current_price:.2f}`"
                    )
                elif current_price <= stop_loss_limit:
                    signal_type = "error"
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    signal = (
                        f"🚨 **DYNAMISCHER STOP LOSS UNTERSCHRITTEN!**\n\n"
                        f"Kurs ({current_price:.2f} €) ist unter die 50%-Gewinn-Absicherung ({stop_loss_limit:.2f} €) gefallen.\n"
                        f"Restliche 50% glattstellen! In `portfolio.py` eintragen: `'sold': True`, `'t2_sell_date': '{today_str}'`, `'t2_sell_price': {current_price:.2f}`"
                    )
                else:
                    signal = (
                        f"🛡️ **2. HÄLFTE LÄUFT:** Warten auf 52W-Hoch-Verkauf bei **{ath_target_price:.2f} €** "
                        f"oder Absicherung beim dynamischen Stop-Loss (50% T1-Gewinn) bei **{stop_loss_limit:.2f} €**."
                    )

            with st.container(border=True):
                st.markdown(f"### {pos['name']} (`{ticker_used}`) — ISIN: `{pos['isin']}`")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric(
                    "Depot-Status",
                    "✅ 50% Verkauft" if is_partially_sold else "⏳ 100% Im Depot",
                    f"{days_held} Tage gehalten",
                )
                c2.metric("Kaufkurs / Aktuell", f"{buy_price:.2f} €", f"Aktuell: {current_price:.2f} €")
                c3.metric("Performance Gesamt", f"{profit_pct:+.2f}%", f"{profit_eur:+.2f} €")
                c4.metric("Aktueller RSI", f"{rsi_today:.1f}", f"EMA50: {ema50_today:.2f} €")

                st.markdown("---")

                t1, t2, t3 = st.columns(3)

                if is_partially_sold and pos.get("t1_sell_price"):
                    t1_price = float(pos["t1_sell_price"])
                    t1_profit_pct = ((t1_price - buy_price) / buy_price) * 100
                    t1_profit_eur = (t1_price - buy_price) * (pos["shares"] / 2.0)

                    t1.metric(
                        "Tranche 1 (Realisiert)",
                        f"{t1_price:.2f} €",
                        f"Gewinn: {t1_profit_pct:+.2f}% ({t1_profit_eur:+.2f} €)",
                    )
                else:
                    t1.metric(
                        "Tranche 1 Ziel (EMA50)",
                        f"{ema50_today:.2f} €",
                        f"{((ema50_today - current_price) / current_price) * 100:+.2f}% zum Kurs",
                    )

                t2.metric(
                    "Tranche 2: 52-Wochen-Hoch (Ziel: -1%)",
                    f"{ath_target_price:.2f} €",
                    f"52W-Hoch: {high_52w:.2f} € ({dist_ath_pct:+.2f}%)",
                )

                t3.metric(
                    sl_label,
                    f"{stop_loss_limit:.2f} €",
                    f"Puffer: {dist_stop_loss_pct:+.2f}%",
                    help="Dynamischer Stop Loss: Kaufkurs + 50% des T1-Gewinns.",
                )

                if signal_type == "success":
                    st.success(signal)
                elif signal_type == "error":
                    st.error(signal)
                else:
                    st.info(signal)

        except Exception as e:
            st.error(f"Fehler bei Position {pos.get('isin')}: {e}")

# --- TAB 4: HISTORIE & GESCHLOSSENE / TEILVERKAUFTE TRADES ---
with tab4:
    st.subheader("📜 History & Ausgewertete Trades")

    if not historie_positionen:
        st.info("Noch keine Teilverkäufe oder abgeschlossenen Trades in der Historie vorhanden.")
    else:
        historie_liste = []
        for pos in historie_positionen:
            is_sold = pos.get("sold", False)
            has_t1 = pos.get("t1_sell_price") is not None
            has_t2 = pos.get("t2_sell_price") is not None

            buy_price = float(pos["buy_price"])
            shares = float(pos["shares"])
            einsatz = buy_price * shares
            half_shares = shares / 2.0

            buy_dt = pd.to_datetime(pos.get("buy_date", datetime.now().strftime("%Y-%m-%d")))

            if is_sold and has_t1 and not has_t2:
                t1_price = float(pos["t1_sell_price"])
                t1_gewinn_eur = (t1_price - buy_price) * shares
                t1_gewinn_pct = ((t1_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"
                t2_str = "- "
                gesamt_gewinn_eur = t1_gewinn_eur
                gesamt_gewinn_pct = t1_gewinn_pct
                gesamt_str = f"{gesamt_gewinn_eur:+.2f} € ({gesamt_gewinn_pct:+.2f}%)"
                status_label = "✅ Vollständig verkauft"
                v_datum_str = pos.get("t1_sell_date", "-")
                end_dt = pd.to_datetime(v_datum_str) if v_datum_str != "-" else buy_dt

            elif is_sold and has_t2:
                t1_price = float(pos.get("t1_sell_price", buy_price))
                t2_price = float(pos.get("t2_sell_price", buy_price))

                t1_gewinn_eur = (t1_price - buy_price) * half_shares
                t1_gewinn_pct = ((t1_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"

                t2_gewinn_eur = (t2_price - buy_price) * half_shares
                t2_gewinn_pct = ((t2_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
                t2_str = f"{t2_gewinn_eur:+.2f} € ({t2_gewinn_pct:+.2f}%)"

                gesamt_gewinn_eur = t1_gewinn_eur + t2_gewinn_eur
                gesamt_gewinn_pct = (gesamt_gewinn_eur / einsatz) * 100 if einsatz > 0 else 0.0
                gesamt_str = f"{gesamt_gewinn_eur:+.2f} € ({gesamt_gewinn_pct:+.2f}%)"

                status_label = "✅ Vollständig verkauft"
                v_datum_str = pos.get("t2_sell_date", pos.get("t1_sell_date", "-"))
                end_dt = pd.to_datetime(v_datum_str) if v_datum_str != "-" else buy_dt

            else:
                t1_price = float(pos.get("t1_sell_price", buy_price))
                t1_gewinn_eur = (t1_price - buy_price) * half_shares
                t1_gewinn_pct = ((t1_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
                t1_str = f"{t1_gewinn_eur:+.2f} € ({t1_gewinn_pct:+.2f}%)"
                t2_str = "- "
                gesamt_gewinn_eur = t1_gewinn_eur
                gesamt_gewinn_pct = t1_gewinn_pct
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
                "Name / Ticker": f"{pos.get('name', pos['ticker'])} ({pos['ticker']})",
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
                else:
                    return "background-color: #45b77d; color: #04190b; font-weight: bold;"

            for col in target_cols:
                if col in df.columns:
                    styles[col] = df[col].apply(get_color_style)

            return styles

        styled_df_hist = df_hist.style.apply(style_historie_table, axis=None)
        st.dataframe(styled_df_hist, use_container_width=True, hide_index=True)
