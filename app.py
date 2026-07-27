import logging
import warnings
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# --- STREAMLIT SEITEN-SETUP ---
st.set_page_config(
    page_title="ETF Dip-Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

MANUAL_TICKERS = {
    "LU2090063327": ["SEMD.MI", "6B7A.DE", "CHIP.PA"],
    "IE00BCHWNV48": ["XIND.MI", "XIND.L", "XCHA.DE", "XINW.DE"],
    "IE00BLCHJB90": ["WCLD.L", "WCLD.DE"],
    "IE000E7EI9P0": ["SEMI.L", "SEMI.DE"],
    "IE00BJ5JNZ06": ["WTAI.L", "WTAI.DE"],
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
}


def parse_isin_file(filename="isin.txt"):
    etf_liste = []
    aktueller_sektor = "Allgemein"
    try:
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    aktueller_sektor = line.lstrip("#").strip().capitalize()
                else:
                    etf_liste.append({"sektor": aktueller_sektor, "isin": line})
    except FileNotFoundError:
        st.error(f"❌ Datei '{filename}' nicht gefunden!")
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


def berechne_indikatoren(isin):
    kandidaten = (
        MANUAL_TICKERS[isin]
        if isin in MANUAL_TICKERS
        else ([isin_zu_ticker(isin)] if isin_zu_ticker(isin) else [])
    )

    data, erfolgreicher_ticker, ticker_obj = None, None, None
    for ticker_symbol in kandidaten:
        try:
            t_obj = yf.Ticker(ticker_symbol)
            df = t_obj.history(period="2y")  # 2 Jahre für exakten RSI-Abgleich
            if not df.empty and len(df) >= 200:
                data, erfolgreicher_ticker, ticker_obj = (
                    df,
                    ticker_symbol,
                    t_obj,
                )
                break
        except Exception:
            continue

    if data is None:
        return None, (kandidaten[0] if kandidaten else "N/A")

    yahoo_zeit = "k.A."
    try:
        intraday = ticker_obj.history(period="1d", interval="1m")
        if not intraday.empty:
            last_ts = intraday.index[-1]
            if last_ts.tzinfo is not None:
                last_ts = last_ts.tz_convert("Europe/Berlin")
            yahoo_zeit = last_ts.strftime("%H:%M Uhr (%d.%m.)")
    except Exception:
        pass

    close = data["Close"].dropna()
    gd200 = close.rolling(window=200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

    # Preis für RSI 35
    avg_gain_prev, avg_loss_prev, prev_close = (
        float(avg_gain.iloc[-2]),
        float(avg_loss.iloc[-2]),
        float(close.iloc[-2]),
    )
    rsi35_preis = prev_close + ((7 * avg_loss_prev) - (13 * avg_gain_prev))

    gd200_heute = float(gd200.iloc[-1])
    gd200_vor_10d = (
        float(gd200.iloc[-11]) if len(gd200) >= 11 else gd200_heute
    )
    gd200_steigt = gd200_heute > gd200_vor_10d

    perf_1w = 0.0
    if len(close) >= 6:
        close_1w = float(close.iloc[-6])
        perf_1w = ((float(close.iloc[-1]) - close_1w) / close_1w) * 100

    is_fallendes_messer = perf_1w < -3.0

    return {
        "close": float(close.iloc[-1]),
        "rsi": float(rsi.iloc[-1]),
        "rsi35_preis": float(rsi35_preis),
        "gd200": gd200_heute,
        "ema50": float(ema50.iloc[-1]),
        "gd200_steigt": gd200_steigt,
        "yahoo_zeit": yahoo_zeit,
        "perf_1w": perf_1w,
        "is_fallendes_messer": is_fallendes_messer,
    }, erfolgreicher_ticker


# --- APP INTERFACE ---
st.title("📈 ETF Dip-Scanner Dashboard")
st.caption("Echtzeit-Analyse für Trend-ETFs im Dip (RSI < 35 & über GD200)")

# Sidebar
st.sidebar.header("⚙️ Steuerung")
run_scan = st.sidebar.button("🔎 Scan jetzt starten", use_container_width=True)

etfs = parse_isin_file("isin.txt")
st.sidebar.info(f"📋 **{len(etfs)} ETFs** in `isin.txt` hinterlegt.")

if run_scan or "kauf_signale" not in st.session_state:
    if run_scan:
        st.session_state.clear()

    kauf_signale, watchlist_signale = [], []
    progress_bar = st.progress(0, text="Starte Scan...")

    for i, item in enumerate(etfs, 1):
        progress_bar.progress(
            i / len(etfs), text=f"Prüfe ETF {i}/{len(etfs)}: {item['isin']}"
        )
        data, ticker = berechne_indikatoren(item["isin"])

        if not data:
            continue

        c, rsi, rsi35, gd200, ema50, gd200_steigt, zeit, perf_1w, messer = (
            data["close"],
            data["rsi"],
            data["rsi35_preis"],
            data["gd200"],
            data["ema50"],
            data["gd200_steigt"],
            data["yahoo_zeit"],
            data["perf_1w"],
            data["is_fallendes_messer"],
        )

        grundtrend_ok = ema50 > gd200 and gd200_steigt

        entry = {
            "Sektor": item["sektor"],
            "ISIN": item["isin"],
            "Ticker": ticker,
            "Kurs": f"{c:.2f} €",
            "RSI": round(rsi, 1),
            "RSI 35 Preis": f"{rsi35:.2f} €",
            "GD200": f"{gd200:.2f} € ({((c-gd200)/gd200)*100:+.1f}%)",
            "EMA50": f"{ema50:.2f} € ({((c-ema50)/ema50)*100:+.1f}%)",
            "1W Perf.": f"{perf_1w:+.1f}%",
            "Zeit": zeit,
        }

        if grundtrend_ok:
            if rsi < 35 and c > gd200 and not messer:
                kauf_signale.append(entry)
            elif rsi < 40 and c >= (gd200 * 0.97):
                watchlist_signale.append(entry)

    progress_bar.empty()
    st.session_state["kauf_signale"] = kauf_signale
    st.session_state["watchlist_signale"] = watchlist_signale

# --- ERGEBNIS-AUSGABE ---
tab1, tab2 = st.tabs([
    f"🔥 Kaufsignale ({len(st.session_state.get('kauf_signale', []))})",
    f"📋 Watchlist ({len(st.session_state.get('watchlist_signale', []))})",
])

with tab1:
    kauf = st.session_state.get("kauf_signale", [])
    if kauf:
        st.success(
            f"**{len(kauf)} Kaufsignal(e) gefunden!** (RSI < 35, Kurs > GD200,"
            " geordneter Rücksetzer)"
        )
        for item in kauf:
            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Sektor / ETF", item["Sektor"], item["ISIN"])
                col2.metric(
                    "Aktueller Kurs", item["Kurs"], f"RSI: {item['RSI']}"
                )
                col3.metric("GD200 (Makro)", item["GD200"])
                col4.metric(
                    "Ziel 1 (EMA50)", item["EMA50"], f"1W: {item['1W Perf.']}"
                )
                st.caption(
                    f"⏱️ Stand: {item['Zeit']} | Kauf-Limit für RSI"
                    f" <35: **{item['RSI 35 Preis']}**"
                )
    else:
        st.info("Aktuell keine Kaufsignale (kein ETF erfüllt strikt alle Kriterien).")

with tab2:
    watch = st.session_state.get("watchlist_signale", [])
    if watch:
        st.dataframe(pd.DataFrame(watch), use_container_width=True)
    else:
        st.write("Keine ETFs in der erweiterten Watchlist.")