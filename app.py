from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import logging
import warnings
import re
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from dip_score import (
    berechne_indikator_serien,
    score_am_punkt,
    signal_stufe,
    KAUFSIGNAL_SCHWELLE,
    SOFT_KAUFSIGNAL_SCHWELLE,
    ZIEL_RENDITE_SOFT_PCT,
    ZIEL_RENDITE_VOLL_PCT,
    RSI_WATCHLIST_SCHWELLE,
    REGIME_MALUS_FAKTOR,
    MARKT_BENCHMARK_TICKER,
)

import importlib
import portfolio
importlib.reload(portfolio)  # Zwingt Python, portfolio.py bei jedem Rerun neu zu lesen

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
# MANUELLE TICKER-ZUORDNUNG (Fallback)
# ==========================================
# Wird nur genutzt, wenn eine ISIN weder in isin.txt einen Ticker mitbringt
# noch über die automatische Yahoo-Suche (isin_zu_ticker) aufgelöst werden kann.
# Leer lassen, solange isin.txt für jede Zeile bereits einen passenden Ticker liefert.
MANUAL_TICKERS = {}

# ==========================================
# SCANNER-KONFIGURATION (hier einfach anpassbar)
# ==========================================
DATENSTAND_CUTOFF_STUNDE = 19    # Vor dieser Uhrzeit (Europe/Berlin) gilt der heutige Schlusskurs
                                  # noch als nicht final bestätigt -> letzter Vortag wird verwendet


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
                            line.lstrip("#").strip()
                        )
                    continue

                if ";" in line:
                    isin, ticker = line.split(";", 1)
                    etf_liste.append({
                        "sektor": aktueller_sektor,
                        "isin": isin.strip(),
                        "ticker": ticker.strip(),
                    })
                else:
                    etf_liste.append({
                        "sektor": aktueller_sektor,
                        "isin": line.strip(),
                        "ticker": None,
                    })
    except FileNotFoundError:
        return []
    return etf_liste


@st.cache_data(ttl=86400)  # 24h Caching zur Vermeidung von Rate Limits
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


@st.cache_data(ttl=3600)
def markt_regime_ok(benchmark_ticker=None):
    """
    Prüft, ob ein breiter Marktindex über seinem eigenen GD200 liegt.
    Dient als globaler Kontextfilter: In einer echten Marktkorrektur sollen
    Kaufsignale seltener werden, auch wenn ein einzelner ETF isoliert
    betrachtet noch "sauber" aussieht.
    """
    ticker = benchmark_ticker or MARKT_BENCHMARK_TICKER
    try:
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        if len(close) < 200:
            return True  # nicht genug Historie -> im Zweifel keinen Malus anwenden
        gd200 = close.rolling(window=200).mean()
        return bool(float(close.iloc[-1]) > float(gd200.iloc[-1]))
    except Exception:
        return True  # Fail-safe: bei Datenproblem keinen Malus anwenden


@st.cache_data(ttl=300)
def berechne_indikatoren(isin, ticker=None):
    if ticker:
        kandidaten = [ticker]
    elif isin in MANUAL_TICKERS:
        kandidaten = MANUAL_TICKERS[isin]
    else:
        t = isin_zu_ticker(isin)
        kandidaten = [t] if t else []

    data, erfolgreicher_ticker = None, None
    for ticker_symbol in kandidaten:
        try:
            df = yf.download(ticker_symbol, period="2y", progress=False, auto_adjust=False)
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

    # --- LIVE-WERTE: immer der neueste verfügbare Punkt, bevor der
    #     konservative Modus ihn ggf. verwirft. Rein zur Beobachtung/Tendenz,
    #     fließt NICHT in den Dip Score ein. ---
    live_close, live_rsi = None, None
    try:
        live_close = float(close.iloc[-1])
        live_delta = close.diff()
        live_gain = live_delta.clip(lower=0)
        live_loss = -1 * live_delta.clip(upper=0)
        live_avg_gain = live_gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        live_avg_loss = live_loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        live_rsi_series = 100 - (100 / (1 + (live_avg_gain / live_avg_loss)))
        live_rsi = float(live_rsi_series.iloc[-1])
    except Exception:
        pass

    # --- KONSERVATIVER MODUS: heutigen Datenpunkt ggf. ausblenden ---
    # Solange der heutige Schlusskurs noch nicht sicher final bestätigt ist
    # (Börse noch offen ODER Schluss liegt noch keine DATENSTAND_CUTOFF_STUNDE
    # zurück), wird stattdessen der letzte bestätigte Vortag verwendet. Das
    # verhindert, dass RSI/Kursrückgang auf Basis einer noch laufenden oder
    # frisch-vorläufigen Tageskerze berechnet werden.
    try:
        jetzt_berlin = datetime.now(ZoneInfo("Europe/Berlin"))
        letztes_datum = close.index[-1].date()
        if (
            letztes_datum == jetzt_berlin.date()
            and jetzt_berlin.time() < dt_time(DATENSTAND_CUTOFF_STUNDE, 0)
            and len(close) > 1
        ):
            close = close.iloc[:-1]
            low = low.iloc[:-1]
            high = high.iloc[:-1]
    except Exception:
        pass

    # 52-Wochen-Hoch (ca. 252 Handelstage) - separat, da nicht Teil des
    # gemeinsamen Scores (wird fuer das T2-Exit-Ziel in Tab 2 benoetigt)
    high_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())

    # --- GEMEINSAMES SCORE-MODUL (dip_score.py) ---
    # Einzige Quelle der Wahrheit fuer die Score-Formel, identisch zu dem,
    # was backtest.py verwendet.
    indikatoren = berechne_indikator_serien(close, high, low)
    regime_ok = markt_regime_ok()
    score_ergebnis = score_am_punkt(indikatoren, -1, regime_ok=regime_ok)

    c_today = score_ergebnis["close"]
    rsi_today = float(indikatoren["rsi"].iloc[-1])

    # RSI-35-Zielpreis (reiner Info-/Sortier-Wert, kein Score-Bestandteil)
    ag_today = float(indikatoren["avg_gain"].iloc[-1])
    al_today = float(indikatoren["avg_loss"].iloc[-1])
    if rsi_today > 35.0:
        drop_needed = (169.0 * ag_today - 91.0 * al_today) / 7.0
        rsi35_preis = c_today - drop_needed
    else:
        rise_needed = (91.0 * al_today - 169.0 * ag_today) / 13.0
        rsi35_preis = c_today + rise_needed

    gd200_vor_10d_wert = indikatoren["gd200_vor_10d"].iloc[-1]
    gd200_vor_10d = (
        float(gd200_vor_10d_wert)
        if not pd.isna(gd200_vor_10d_wert)
        else score_ergebnis["gd200"]
    )

    perf_1w = 0.0
    if len(close) >= 6:
        close_1w = float(close.iloc[-6])
        perf_1w = ((c_today - close_1w) / close_1w) * 100

    return {
        "close": c_today,
        "rsi": rsi_today,
        "rsi35_preis": float(rsi35_preis),
        "live_close": live_close if live_close is not None else c_today,
        "live_rsi": live_rsi if live_rsi is not None else rsi_today,
        "gd200": score_ergebnis["gd200"],
        "gd200_vor_10d": gd200_vor_10d,
        "ema50": score_ergebnis["ema50"],
        "high_52w": high_52w,
        "gd200_steigt": score_ergebnis["gd200_steigt"],
        "perf_1w": perf_1w,
        "dip_score": score_ergebnis["dip_score"],
        "rsi_score": score_ergebnis["rsi_score"],
        "trend_score": score_ergebnis["trend_score"],
        "gd200_score": score_ergebnis["gd200_score"],
        "ema50_score": score_ergebnis["ema50_score"],
        "drawdown_score": score_ergebnis["drawdown_score"],
        "drawdown_20t_pct": score_ergebnis["drawdown_20t_pct"],
        "regime_ok": regime_ok,
        "yahoo_zeit": yahoo_zeit,
    }, erfolgreicher_ticker


# ==========================================
# APP USER INTERFACE
# ==========================================
st.title("📈 ETF Dip-Scanner & Portfolio-Manager")

with st.expander("ℹ️ Wann entsteht ein Kaufsignal? (Hier klicken)"):
    st.markdown(f"""
    ### 🎯 Zwei Signalstufen statt Ja/Nein
    Es gibt keinen separaten Ja/Nein-Filter - alle Kriterien (RSI, Trend,
    Kursrückgang, Marktumfeld) fließen in **einen einzigen Score** von
    maximal ca. 100 Punkten ein. Der Backtest über ~277.000 ETF-Tage zeigt
    zwei sinnvolle Schwellen mit unterschiedlicher Renditeerwartung:

    | Stufe | Score | Backtest-Trefferquote (40 Handelstage) |
    |---|---|---|
    | 🟡 Softes Signal | {SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE - 1:.0f} | ~80% erreichen +5% |
    | 🔥 Kaufsignal | ≥ {KAUFSIGNAL_SCHWELLE:.0f} | ~63% erreichen +10% |

    Bei der Order lohnt es sich, die Stufe zu notieren (z. B. in
    `portfolio.py`) - ein softes Signal rechtfertigt eher ein niedrigeres
    Ziel (~5%) als ein volles Kaufsignal (~10%).

    | Komponente | Max. Punkte | Was gemessen wird |
    |---|---|---|
    | RSI-Sweet-Spot | 20 | Abstand zu RSI 25 (siehe unten) |
    | Trend intakt | 15 | EMA50 > GD200 **und** GD200 steigt |
    | Puffer über GD200 | 15 | Sicherheitsabstand zur langfristigen Stütze |
    | Mean-Reversion-Potenzial | 15 | Rebound-Distanz bis zur EMA50 |
    | **Kursrückgang-Tiefe** | **35** | Wie stark der Kurs vor dem Signal fiel |

    **Wichtig:** Ohne nennenswerten vorherigen Kursrückgang sind maximal
    **65** der 100 Punkte erreichbar (RSI + Trend + GD200-Puffer +
    EMA50-Potenzial). Ein Kaufsignal ab {KAUFSIGNAL_SCHWELLE:.0f} Punkten
    ist damit rechnerisch nur möglich, wenn der Kurs auch tatsächlich
    spürbar gefallen ist.

    ### 🎯 RSI-Sweet-Spot statt "je tiefer desto besser"
    Der Backtest zeigte einen Peak bei RSI 20-30 - RSI unter 20 performte
    **schlechter** (vermutlich eher Crash-Signal als normaler Dip). Der
    Score hat deshalb sein Maximum bei RSI 25 und fällt zu **beiden**
    Seiten linear ab, statt einfach mit sinkendem RSI immer weiter zu
    steigen.

    ### 📉 Kursrückgang-Tiefe (größte Einzelkomponente)
    Rückgang vom 20-Tage-Hoch bis heute. Das war im Backtest das mit
    Abstand stärkste Einzelsignal: Die Wahrscheinlichkeit, +10% zu
    erreichen, stieg sauber von ~23% (flacher Rückgang) auf ~80% (Rückgang
    über 30%) - "größerer Dip = größerer Rebound" bestätigte sich klar,
    "fallendes Messer" nicht. Volle Punktzahl ab ca. 29% Rückgang.

    ### 🌍 Marktregime-Filter
    Zusätzlich wird geprüft, ob der breite Referenzindex (`{MARKT_BENCHMARK_TICKER}`)
    selbst über seinem GD200 notiert. Falls nicht, werden **alle** Scores mit
    ×{REGIME_MALUS_FAKTOR} multipliziert - in einer echten Marktkorrektur werden
    Kaufsignale dadurch automatisch seltener, auch wenn ein einzelner ETF
    isoliert betrachtet noch sauber aussieht.

    ### 📉 GD200-Bruch-Malus
    Der Trend-Score (EMA50 vs. GD200, GD200-Richtung) kann "grün" bleiben,
    obwohl der Kurs selbst schon spürbar unter seinem GD200 liegt - der GD200
    reagiert als 200-Tage-Schnitt sehr träge. Deshalb dämpft ein zusätzlicher,
    graduell wirkender Malus den **gesamten** Score, je weiter der Kurs unter
    dem GD200 liegt: kein Abzug bei Kurs auf/über GD200, bis zu ×0.6 (also
    -40%) ab 10% Abstand oder mehr.
    """)

with st.expander("📊 Wie wird das Verkaufsziel gesetzt? (Regelwerk)", expanded=False):
    col_t1, col_t2 = st.columns(2)

    with col_t1:
        st.markdown(f"""
        ### 🟡 Softes Signal → +{ZIEL_RENDITE_SOFT_PCT:.0f}%
        * **Ziel:** Kompletter Verkauf bei Kaufkurs + {ZIEL_RENDITE_SOFT_PCT:.0f}%.
        * **Sinn:** Backtest-optimiert - schlägt die alte EMA50-Regel in
          Rendite pro Tag deutlich (0,32 vs. 0,13 %/Tag).
        """)

    with col_t2:
        st.markdown(f"""
        ### 🔥 Volles Signal → +{ZIEL_RENDITE_VOLL_PCT:.0f}%
        * **Ziel:** Kompletter Verkauf bei Kaufkurs + {ZIEL_RENDITE_VOLL_PCT:.0f}%.
        * **Sinn:** Schlägt die alte EMA50/52W-Hoch-Regel auch absolut
          (+3,58% vs. +3,27%) bei weniger als halber Haltedauer.
        """)

    st.caption(
        "Ersetzt die frühere zweistufige EMA50/52-Wochen-Hoch-Tranchenlogik "
        "(Kompletteverkauf statt Teilverkauf) - siehe Portfolio-Tab für den "
        "aktuellen Abstand zum Ziel je Position. Damit ein Kursziel berechnet "
        "werden kann, muss die Position `'signal_stufe': 'soft'` oder "
        "`'voll'` (oder ersatzweise `'dip_score_bei_kauf'`) in `portfolio.py` haben."
    )

if "letztes_update" in st.session_state:
    st.caption(
        f"⏱️ **Letzter allgemeiner Scan-Stand:**"
        f" {st.session_state['letztes_update']}"
    )

st.sidebar.header("⚙️ Steuerung")

if st.sidebar.button("🔄 Daten aktualisieren", use_container_width=True):
    st.cache_data.clear()
    if "watchlist_signale" in st.session_state:
        del st.session_state["watchlist_signale"]
    st.rerun()

with st.sidebar.expander("🔍 Debug: Einzelne ISIN prüfen"):
    debug_isin = st.text_input("ISIN eingeben", key="debug_isin_input")
    if debug_isin:
        debug_ticker = None
        for e in parse_isin_file("isin.txt"):
            if e["isin"] == debug_isin.strip():
                debug_ticker = e.get("ticker")
                break
        if not debug_ticker:
            for p in PORTFOLIO:
                if p["isin"] == debug_isin.strip():
                    debug_ticker = p.get("ticker")
                    break

        debug_data, debug_used_ticker = berechne_indikatoren(
            debug_isin.strip(), debug_ticker
        )
        if debug_data:
            st.write(f"Aufgelöster Ticker: `{debug_used_ticker}`")
            st.write(
                f"Kurs: **{debug_data['close']:.2f}** | "
                f"RSI: **{debug_data['rsi']:.2f}**"
            )
            try:
                raw = yf.download(
                    debug_used_ticker,
                    period="15d",
                    progress=False,
                    auto_adjust=False,
                )
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)
                st.caption("Rohdaten der letzten Handelstage (Close vs. Adj Close):")
                st.dataframe(
                    raw[["Close", "Adj Close"]].tail(10),
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Rohdaten-Abruf fehlgeschlagen: {e}")
        else:
            st.error("Keine Daten für diese ISIN/diesen Ticker gefunden.")

etfs = parse_isin_file("isin.txt")

portfolio_isins = [p["isin"] for p in PORTFOLIO if not p.get("sold", False)]
etfs_isins = {e["isin"] for e in etfs}
for p in PORTFOLIO:
    if p["isin"] not in etfs_isins:
        etfs.append({"sektor": "Portfolio", "isin": p["isin"], "ticker": p.get("ticker")})

st.sidebar.info(f"📋 **{len(etfs)} ETFs** werden überwacht.")

# --- PARALLELER DATEN-SCANNER ---
if "watchlist_signale" not in st.session_state:
    watchlist_signale, fehlgeschlagene_etfs = [], []
    letzter_zeitstempel = "k.A."
    progress_bar = st.progress(0, text="⚡ Lade Kursdaten (Parallel-Scan)...")

    total_etfs = len(etfs)
    completed_count = 0

    def load_etf_data(item):
        data, ticker = berechne_indikatoren(item["isin"], item.get("ticker"))
        return item, data, ticker

    with ThreadPoolExecutor(max_workers=3) as executor:
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

            c = data["close"]
            rsi = data["rsi"]
            rsi35 = data["rsi35_preis"]
            gd200 = data["gd200"]
            gd200_10d = data["gd200_vor_10d"]
            ema50 = data["ema50"]
            gd200_steigt = data["gd200_steigt"]
            perf_1w = data["perf_1w"]
            dip_score = data["dip_score"]
            drawdown_score = data["drawdown_score"]
            drawdown_20t_pct = data["drawdown_20t_pct"]
            regime_ok = data["regime_ok"]
            live_kurs = data["live_close"]
            live_rsi = data["live_rsi"]

            gd200_abstand = ((gd200 - c) / c) * 100
            gd200_10d_abstand = ((gd200_10d - c) / c) * 100
            rsi35_abstand = ((rsi35 - c) / c) * 100

            stufe = signal_stufe(dip_score)
            ist_kaufsignal = stufe == "voll"
            ist_soft_signal = stufe == "soft"
            ist_in_portfolio = item["isin"] in portfolio_isins
            # Hauptkriterium: RSI < 40. Portfolio-Positionen erscheinen immer,
            # unabhängig von ihren aktuellen Werten.
            ist_watchlist_kandidat = (rsi < RSI_WATCHLIST_SCHWELLE) or ist_in_portfolio

            entry = {
                "Sektor": item["sektor"],
                "ISIN": item["isin"],
                "Ticker": ticker,
                "Kurs": c,
                "RSI": round(rsi, 1),
                "RSI 35 Preis": rsi35,
                "RSI35_Abstand": rsi35_abstand,
                "Live_Kurs": live_kurs,
                "Live_RSI": round(live_rsi, 1),
                "GD200": gd200,
                "GD200_Abstand": gd200_abstand,
                "GD200_10d": gd200_10d,
                "GD200_10d_Abstand": gd200_10d_abstand,
                "GD200_steigt": gd200_steigt,
                "EMA50": ema50,
                "1W Perf.": perf_1w,
                "Dip Score": dip_score,
                "Drawdown Score": drawdown_score,
                "Drawdown_20t_Pct": drawdown_20t_pct,
                "Marktregime_OK": regime_ok,
                "Zeitstempel": data["yahoo_zeit"],
                "Ist_Kaufsignal": ist_kaufsignal,
                "Ist_Soft_Signal": ist_soft_signal,
                "Ist_Portfolio": ist_in_portfolio,
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

# Aktive Positionen für Tab 2 ermitteln (noch nicht vollständig verkauft)
aktive_positionen = [p for p in PORTFOLIO if not p.get("sold", False)]
historie_positionen = [p for p in PORTFOLIO if p.get("partially_sold", False) or p.get("sold", False)]

# TABS REGISTER
tab1, tab2, tab3 = st.tabs([
    f"📋 Watchlist & Kaufsignale ({len(st.session_state.get('watchlist_signale', []))})",
    f"💼 Mein Portfolio ({len(aktive_positionen)})",
    f"📜 Historie ({len(historie_positionen)})",
])

# --- TAB 1: WATCHLIST & KAUFSIGNALE (konsolidiert) ---
with tab1:
    watch = st.session_state.get("watchlist_signale", [])

    regime_status = watch[0]["Marktregime_OK"] if watch else True
    if regime_status:
        st.caption("🟢 **Marktregime:** Referenzindex über GD200 - normales Scoring.")
    else:
        st.caption(
            f"🔴 **Marktregime:** Referenzindex unter GD200 - alle Scores werden mit "
            f"×{REGIME_MALUS_FAKTOR} gedämpft (Kaufsignale seltener)."
        )

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
            "GD200 & GD200 v10T: 🟩 klar drüber/steigend, ⬜ knapp (≤1%), 🟥 drunter/fallend"
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
                    "📉 Stärkster 1W-Rücksetzer",
                ],
                index=1,
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
            if row["Ist_Kaufsignal"]:
                praefix = "🔥 "
            elif row["Ist_Soft_Signal"]:
                praefix = "🟡 "
            else:
                praefix = ""
            if rank == 1:
                return f"{praefix}🥇 {t}"
            elif rank == 2:
                return f"{praefix}🥈 {t}"
            elif rank == 3:
                return f"{praefix}🥉 {t}"
            else:
                return f"{praefix}{t}"

        display_df = pd.DataFrame()
        display_df["Sektor"] = df_watch["Sektor"]
        display_df["ISIN"] = df_watch["ISIN"]
        display_df["Ticker"] = [
            format_ticker_rank(df_watch.iloc[i]) for i in range(len(df_watch))
        ]

        display_df["Kurs"] = df_watch["Kurs"].map(lambda x: f"{x:.2f} €")
        display_df["RSI"] = df_watch["RSI"].map(lambda x: f"{x:.1f}")

        display_df["Live"] = df_watch.apply(
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
        display_df["Rückgang"] = df_watch.apply(
            lambda r: f"{r['Drawdown_20t_Pct']:.1f}% ({r['Drawdown Score']:.0f}/35)",
            axis=1,
        )
        display_df["Dip Score"] = df_watch["Dip Score"].map(lambda x: f"{x:.1f}")

        def signal_label(row):
            if row["Ist_Kaufsignal"]:
                return "🔥 KAUFEN (Ziel ~10%+)"
            elif row["Ist_Soft_Signal"]:
                return "🟡 Softes Signal (Ziel ~5%+)"
            else:
                return "👀 Beobachten"

        display_df["Signal"] = [
            signal_label(df_watch.iloc[i]) for i in range(len(df_watch))
        ]
        display_df["Zeitstempel"] = df_watch["Zeitstempel"]

        def style_watchlist_cells(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for idx in df.index:
                row_raw = df_watch.loc[idx]

                if row_raw.get("Ist_Portfolio", False):
                    color = "background-color: #e8daef; color: #111111;"
                elif row_raw["Ist_Kaufsignal"]:
                    color = (
                        "background-color: #d4edda; color: #155724;"
                        " font-weight: bold;"
                    )
                elif row_raw.get("Ist_Soft_Signal", False):
                    color = (
                        "background-color: #fff3cd; color: #856404;"
                        " font-weight: bold;"
                    )
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
                    styles.loc[idx, "Ticker"] = (
                        f"background-color: {medaillen[dip_rank]}; font-weight: bold;"
                    )

                # GD200-Zelle: Kurs vs. GD200 (grün = klar drüber, grau = knapp
                # drüber (<=1%), rot = drunter)
                kurs_val = row_raw["Kurs"]
                gd200_val = row_raw["GD200"]
                gd200_10d_val = row_raw["GD200_10d"]
                rsi_val = row_raw["RSI"]

                # RSI-Zelle: <=31.9 grün, 32-35 grau, Rest (>35) rot
                if rsi_val <= 31.9:
                    styles.loc[idx, "RSI"] = "background-color: #d4edda; color: #155724;"
                elif rsi_val <= 35:
                    styles.loc[idx, "RSI"] = "background-color: #e2e3e5; color: #383d41;"
                else:
                    styles.loc[idx, "RSI"] = "background-color: #f8d7da; color: #721c24;"

                # Live-Zelle: Tendenz Live-RSI vs. bestätigter RSI
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

                # GD200 v10T-Zelle: GD200 heute vs. GD200 vor 10 Tagen (Trendrichtung)
                gd200_10d_diff_pct = (
                    ((gd200_val - gd200_10d_val) / gd200_10d_val) * 100
                    if gd200_10d_val
                    else 0.0
                )
                if gd200_10d_diff_pct <= 0:
                    styles.loc[idx, "GD200 v10T"] = "background-color: #f8d7da; color: #721c24;"
                elif gd200_10d_diff_pct <= 1:
                    styles.loc[idx, "GD200 v10T"] = "background-color: #e2e3e5; color: #383d41;"
                else:
                    styles.loc[idx, "GD200 v10T"] = "background-color: #d4edda; color: #155724;"

                styles.loc[idx, "Dip Score"] = (
                    "font-weight: bold; text-align: center;"
                )

            return styles

        styled_df = display_df.style.apply(style_watchlist_cells, axis=None)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    else:
        st.write("Keine ETFs in der Watchlist.")

# --- TAB 2: PORTFOLIO-MANAGER ---

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


with tab2:
    st.subheader("📊 Aktive Positionen")

    if not aktive_positionen:
        st.info("Aktuell keine aktiven offenen Positionen im Portfolio.")
    else:
        sektor_lookup = {e["isin"]: e["sektor"] for e in etfs}
        portfolio_zeilen = []
        fehler_liste = []

        for pos in aktive_positionen:
            try:
                data, ticker_used = berechne_indikatoren(pos["isin"], pos.get("ticker"))
                if not data:
                    fehler_liste.append(f"{pos.get('name', pos.get('isin', '?'))} (keine Kursdaten)")
                    continue

                current_price = data["close"]
                buy_price = float(pos["buy_price"])
                shares = float(pos.get("shares", 0))
                ist_alt_position = pos.get("partially_sold", False)

                # Signalstufe: manuelle Zuordnung oben ist für die dort
                # gelisteten ISINs bindend > sonst portfolio.py ('signal_stufe')
                # > gespeicherter Score ('dip_score_bei_kauf') > unbekannt.
                stufe = MANUELLE_SIGNAL_STUFEN.get(pos["isin"])
                if stufe not in ("soft", "voll"):
                    stufe = pos.get("signal_stufe")
                if stufe not in ("soft", "voll"):
                    gespeicherter_score = pos.get("dip_score_bei_kauf")
                    stufe = (
                        signal_stufe(float(gespeicherter_score))
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
                    "Name": pos.get("name", ticker_used),
                    "ISIN": pos["isin"],
                    "Sektor": pos.get("sektor") or sektor_lookup.get(pos["isin"], "-"),
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

# --- TAB 3: HISTORIE & GESCHLOSSENE / TEILVERKAUFTE TRADES ---
with tab3:
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
