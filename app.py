from datetime import datetime
import logging
import warnings
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

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
DEINE_PIN = "1337"  # 👈 Deine persönliche PIN


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
# 1. DEIN PORTFOLIO (NUR GEKAUFTE POSITIONEN)
# ==========================================
PORTFOLIO = [
    {
        "ticker": "QUTM.DE",
        "isin": "IE0007Y8Y157",
        "name": "VanEck Quantum Computing",
        "buy_date": "2026-07-23",
        "buy_price": 23.79,
        "shares": 21,
        "partially_sold": False,  # True setzen, sobald Tranche 1 am EMA50 verkauft wurde
        "t1_sell_date": None,  # Datum von Teilverkauf 1 (z. B. '2026-07-15')
    },
]

# ==========================================
# 2. MATCHING & ISIN LISTE
# ==========================================
MANUAL_TICKERS = {
    "LU2090063327": ["SEMD.MI", "6B7A.DE", "CHIP.PA"],
    "IE00BCHWNV48": ["XIND.MI", "XIND.L", "XCHA.DE", "XINW.DE"],
    "IE00BLCHJB90": ["WCLD.L", "WCLD.DE"],
    "IE000E7EI9P0": ["SEMI.L", "SEMI.DE"],
    "IE00BJ5JNZ06": ["WTAI.L", "WTAI.DE", "WTAI.MI"],
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

    # Intraday-Zeitstempel
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
    gd200 = close.rolling(window=200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rsi = 100 - (100 / (1 + (avg_gain / avg_loss)))

    # --- MATH.-LOGIK FÜR ZIELKURS RSI 35 ---
    ag_today = float(avg_gain.iloc[-1])
    al_today = float(avg_loss.iloc[-1])
    c_today = float(close.iloc[-1])
    rsi_today = float(rsi.iloc[-1])

    if rsi_today > 35.0:
        drop_needed = (169.0 * ag_today - 91.0 * al_today) / 7.0
        rsi35_preis = c_today - drop_needed
    else:
        rise_needed = (91.0 * al_today - 169.0 * ag_today) / 13.0
        rsi35_preis = c_today + rise_needed

    gd200_heute = float(gd200.iloc[-1])
    gd200_vor_10d = (
        float(gd200.iloc[-11]) if len(gd200) >= 11 else gd200_heute
    )
    gd200_steigt = gd200_heute > gd200_vor_10d

    perf_1w = 0.0
    if len(close) >= 6:
        close_1w = float(close.iloc[-6])
        perf_1w = ((c_today - close_1w) / close_1w) * 100

    return {
        "close": c_today,
        "rsi": rsi_today,
        "rsi35_preis": float(rsi35_preis),
        "gd200": gd200_heute,
        "ema50": float(ema50.iloc[-1]),
        "gd200_steigt": gd200_steigt,
        "perf_1w": perf_1w,
        "is_fallendes_messer": perf_1w < -3.0,
        "yahoo_zeit": yahoo_zeit,
    }, erfolgreicher_ticker


# ==========================================
# 3. APP USER INTERFACE
# ==========================================
st.title("📈 ETF Dip-Scanner & Portfolio-Manager")

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

# SCANNER LOGIK LADE
if "kauf_signale" not in st.session_state:
    kauf_signale, watchlist_signale = [], []
    letzter_zeitstempel = "k.A."
    progress_bar = st.progress(0, text="Lade Kursdaten für Scanner...")

    for i, item in enumerate(etfs, 1):
        progress_bar.progress(
            i / len(etfs), text=f"Prüfe ETF {i}/{len(etfs)}: {item['isin']}"
        )
        data, ticker = berechne_indikatoren(item["isin"])

        if not data:
            continue

        if data.get("yahoo_zeit") and data["yahoo_zeit"] != "k.A.":
            letzter_zeitstempel = data["yahoo_zeit"]

        c, rsi, rsi35, gd200, ema50, gd200_steigt, perf_1w, messer = (
            data["close"],
            data["rsi"],
            data["rsi35_preis"],
            data["gd200"],
            data["ema50"],
            data["gd200_steigt"],
            data["perf_1w"],
            data["is_fallendes_messer"],
        )

        grundtrend_ok = ema50 > gd200 and gd200_steigt
        gd200_abstand = ((gd200 - c) / c) * 100
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
            "EMA50": ema50,
            "1W Perf.": perf_1w,
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
    st.session_state["letztes_update"] = letzter_zeitstempel

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
            " kein fallendes Messer)"
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
            "💡 **Farblegende:** 🟩 **Grün** = Bedingung erfüllt (RSI < 35"
            " bzw. GD200 ≤ Kurs) | 🟥 **Rot** = Nicht erfüllt | 🟪 **Lila** = Im"
            " Portfolio"
        )
        df_watch = pd.DataFrame(watch).sort_values(by="RSI", ascending=True)

        display_df = pd.DataFrame()
        display_df["Sektor"] = df_watch["Sektor"]
        display_df["ISIN"] = df_watch["ISIN"]
        display_df["Ticker"] = df_watch["Ticker"]
        display_df["Kurs"] = df_watch["Kurs"].map(lambda x: f"{x:.2f} €")
        display_df["RSI"] = df_watch["RSI"].map(lambda x: f"{x:.1f}")

        display_df["GD200"] = df_watch.apply(
            lambda r: (
                f"{r['GD200']:.2f} €"
                f" ({((r['GD200'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
            ),
            axis=1,
        )

        display_df["RSI 35 Preis"] = df_watch.apply(
            lambda r: (
                f"{r['RSI 35 Preis']:.2f} €"
                f" ({((r['RSI 35 Preis'] - r['Kurs']) / r['Kurs']) * 100:+.1f}%)"
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
        display_df["Zeitstempel"] = df_watch["Zeitstempel"]

        def style_watchlist_cells(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for idx in df.index:
                row_raw = df_watch.loc[idx]

                # Portfolio-Hintergrund für Standardspalten
                if row_raw.get("Ist_Portfolio", False):
                    for col in df.columns:
                        styles.loc[idx, col] = (
                            "background-color: #e8daef; color: #111111;"
                        )

                # 1. GD200 <= Kurs -> Grün, sonst Rot
                if row_raw["GD200"] <= row_raw["Kurs"]:
                    styles.loc[idx, "GD200"] = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                else:
                    styles.loc[idx, "GD200"] = (
                        "background-color: #f8d7da; color: #721c24;"
                        " font-weight: bold;"
                    )

                # 2. RSI < 35 -> Grün, sonst Rot
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
            df = yf.download(pos["ticker"], period="1y", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

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

            current_price = float(df["Close"].iloc[-1])
            rsi_today = float(df["RSI"].iloc[-1])
            ema50_today = float(df["EMA50"].iloc[-1])
            ema20_today = float(df["EMA20"].iloc[-1])

            dist_ema50_pct = (
                (current_price - ema50_today) / ema50_today
            ) * 100
            dist_ema20_pct = (
                (current_price - ema20_today) / ema20_today
            ) * 100

            investment = pos["buy_price"] * pos["shares"]
            current_value = current_price * pos["shares"]
            profit_eur = current_value - investment
            profit_pct = (
                (current_price - pos["buy_price"]) / pos["buy_price"]
            ) * 100

            is_partially_sold = pos.get("partially_sold", False)
            buy_date_str = pos.get("buy_date")
            t1_sell_date_str = pos.get("t1_sell_date")

            days_held = 0
            if buy_date_str:
                buy_date = pd.to_datetime(buy_date_str)
                today_date = pd.to_datetime(
                    datetime.now().strftime("%Y-%m-%d")
                )
                days_held = (today_date - buy_date).days

            signal_type = "info"
            if not is_partially_sold:
                if current_price >= ema50_today:
                    signal_type = "success"
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    signal = (
                        "🎯 **ZIEL 1 ERREICHT: Jetzt 50% VERKAUFEN!**\n\n"
                        "👉 Im Skript anpassen: `'partially_sold': True` &"
                        f" `'t1_sell_date': '{today_str}'`"
                    )
                else:
                    signal = (
                        "🟢 **100% IM DEPOT:** Warten auf EMA50 bei"
                        f" **{ema50_today:.2f} €** (Abstand:"
                        f" {dist_ema50_pct:+.2f}%)"
                    )
            else:
                trading_days_since_t1 = 0
                max_rsi_since_t1 = rsi_today

                if t1_sell_date_str:
                    t1_date = pd.to_datetime(t1_sell_date_str)
                    df_since_t1 = df[df.index >= t1_date]
                    trading_days_since_t1 = len(df_since_t1)
                    if not df_since_t1.empty:
                        max_rsi_since_t1 = float(df_since_t1["RSI"].max())

                if rsi_today >= 60:
                    signal_type = "success"
                    signal = (
                        "🚀 **ZIEL 2 ERREICHT:** Restliche 50% VERKAUFEN (RSI"
                        " >= 60)!"
                    )
                elif max_rsi_since_t1 >= 55 and rsi_today < 50:
                    signal_type = "error"
                    signal = (
                        f"⚠️ **RSI-TRENDBRUCH:** Peak war"
                        f" {max_rsi_since_t1:.1f}, aktuell RSI {rsi_today:.1f}"
                        " < 50!\n\n👉 **Gewinn sichern:** Restliche 50%"
                        " VERKAUFEN."
                    )
                elif trading_days_since_t1 >= 20:
                    signal_type = "warning"
                    signal = (
                        f"⏱️ **TIME-STOP** ({trading_days_since_t1} Handelstage"
                        " seit T1 abgelaufen)!\n\n👉 **Kapital freigeben:**"
                        " Restliche 50% VERKAUFEN."
                    )
                elif current_price < ema20_today:
                    signal_type = "error"
                    signal = (
                        f"🚨 **EMA20 UNTERSCHRITTEN** ({current_price:.2f} € <"
                        f" {ema20_today:.2f} €)!\n\n👉 **Dynamic Stop**"
                        " gegriffen: Rest-Position glattstellen."
                    )
                else:
                    signal = (
                        f"🛡️ **2. HÄLFTE LÄUFT** (Tag"
                        f" {trading_days_since_t1}/20 seit T1)\n\n"
                        f"• EMA20-Stop im Broker: **{ema20_today:.2f} €**"
                        f" (Abstand: {dist_ema20_pct:+.2f}%)\n"
                        f"• Aktueller RSI: **{rsi_today:.1f}** (Peak seit T1:"
                        f" {max_rsi_since_t1:.1f})"
                    )

            with st.container(border=True):
                st.markdown(
                    f"### {pos['name']} (`{pos['ticker']}`) — ISIN:"
                    f" `{pos['isin']}`"
                )

                c1, c2, c3, c4 = st.columns(4)
                c1.metric(
                    "Depot-Status",
                    "✅ 50% Verkauft"
                    if is_partially_sold
                    else "⏳ 100% Im Depot",
                    f"{days_held} Tage gehalten",
                )
                c2.metric(
                    "Kaufkurs / Aktuell",
                    f"{pos['buy_price']:.2f} €",
                    f"Aktuell: {current_price:.2f} €",
                )
                c3.metric(
                    "Performance",
                    f"{profit_pct:+.2f}%",
                    f"{profit_eur:+.2f} €",
                )
                c4.metric(
                    "Indikatoren",
                    f"RSI: {rsi_today:.1f}",
                    f"EMA50: {ema50_today:.2f} €",
                )

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
