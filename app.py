"""
app.py

Haupteinstiegspunkt der Streamlit-App - nach dem Refactor nur noch
Orchestrator: PIN-Schutz, Grundkonfiguration, TODO-Liste, ruft die
ausgelagerten Module (scanner_data, signal_log, watchlist_tab,
portfolio_tab, historie_tab, info_texte) in der richtigen Reihenfolge auf.

Die eigentliche Logik lebt jetzt in:
- dip_score.py       - Score-Formel (einzige Quelle der Wahrheit)
- scanner_data.py    - Yahoo-Anbindung, Fallback-Cache, Regime-Check
- signal_log.py       - Google-Sheets-Forward-Tracking
- watchlist_tab.py    - Tab 1: Scan, Diversifikationsfilter, Anzeige
- portfolio_tab.py    - Tab 2: Positions-Tracking
- historie_tab.py     - Tab 3: Verkaufte Positionen
- info_texte.py       - Erklär-/Statistik-Textblöcke
"""

import warnings
import logging

import streamlit as st

import importlib
import portfolio
importlib.reload(portfolio)  # Zwingt Python, portfolio.py bei jedem Rerun neu zu lesen

from portfolio import DEINE_PIN, PORTFOLIO

from scanner_data import parse_isin_file, berechne_indikatoren
from watchlist_tab import fuehre_scan_durch, render_watchlist_tab
from portfolio_tab import render_portfolio_tab
from historie_tab import render_historie_tab
from info_texte import (
    render_kaufsignal_erklaerung,
    render_verkaufsziel_erklaerung,
    render_offene_punkte,
    render_statistik_tab,
)

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
# OFFENE PUNKTE & TODOS (manuell gepflegt)
# ==========================================
# Dinge, die wir im Blick behalten oder noch genauer untersuchen sollten -
# wird in der App unter "📝 Offene Punkte & TODOs" angezeigt. Bei Bedarf
# einfach hier ergänzen/entfernen/als erledigt streichen.
OFFENE_PUNKTE = [
    {
        "titel": "Cloud & Cyber Security als schwache Sektoren",
        "kontext": (
            "Schneiden im Backtest durchgehend unterdurchschnittlich ab, auch "
            "bei höheren Schwellen (z.B. nur 10-13% quote_10pct bei Schwelle 80, "
            "gegenüber ~55% im Gesamtdurchschnitt). Cluster-Zahl (10-16) noch "
            "zu dünn für eine endgültige Entscheidung (Sektor-Aufschlag o.ä.)."
        ),
        "status": "🔍 Beobachten",
    },
    {
        "titel": "Softes-Signal-Ziel 3% vs. 4%",
        "kontext": (
            "Nach der Trend/GD200-Umkehr zeigte 3% im letzten Lauf leicht "
            "bessere Rendite/Tag-Effizienz als das aktuell eingestellte 4%-Ziel "
            "(ZIEL_RENDITE_SOFT_PCT). Könnte Rauschen aus einem einzelnen Lauf "
            "sein - über 1-2 weitere Backtest-Läufe bestätigen, bevor geändert wird."
        ),
        "status": "🔍 Beobachten",
    },
    {
        "titel": "Marktregime-Bonus statt nur Neutralisierung",
        "kontext": (
            "Idee: bei Bärenmarkt einen Bonus-Multiplikator (>1.0) statt nur "
            "Neutralität einsetzen. Erste Zahlen sprechen dafür in niedrigen/"
            "mittleren Rohscore-Bereichen (30-75), drehen sich aber im höchsten "
            "Bereich (75-100, der eigentlichen Kaufsignal-Schwelle) leicht um - "
            "und dort ist die Stichprobe am dünnsten (10 Cluster). Braucht eine "
            "eigene, gründlichere Prüfung (echte Formel simulieren, nicht nur "
            "gleiche-Rohscore-Vergleich), nicht einfach übernehmen."
        ),
        "status": "🔬 Genauer testen",
    },
    {
        "titel": "Diversifikations-/Korrelationsfilter empirisch validieren",
        "kontext": (
            "Bisher nur logisch hergeleitet und mit aktuellen Korrelationswerten "
            "verifiziert (z.B. SEC0.DE↔AIFS.DE), nicht rückblickend gebacktestet, "
            "ob das Ausblenden korrelierter Signale tatsächlich zu besseren "
            "Ergebnissen geführt hätte. Das Forward-Tracking-Log sammelt dafür "
            "bereits Daten (loggt auch ausgeblendete Signale mit Grund) - "
            "Auswertung ergibt erst nach einigen Monaten Sinn."
        ),
        "status": "⏳ Daten sammeln",
    },
    {
        "titel": "Marktphasen-Wechsel als Re-Backtest-Anlass",
        "kontext": (
            "Die Bulle/Bär-Anzeige (mit Dauer in Handelstagen) ist jetzt da, "
            "aber rein informativ - es gibt keinen automatischen Hinweis, WANN "
            "genau ein Phasenwechsel bedeutsam genug für einen Re-Backtest ist. "
            "Bleibt vorerst eine manuelle Einschätzung."
        ),
        "status": "🔍 Beobachten",
    },
]

# ==========================================
# APP USER INTERFACE
# ==========================================
st.title("📈 ETF Dip-Scanner & Portfolio-Manager")

render_kaufsignal_erklaerung()
render_verkaufsziel_erklaerung()
render_offene_punkte(OFFENE_PUNKTE)
render_statistik_tab()

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

        debug_data, debug_used_ticker, debug_fehler = berechne_indikatoren(
            debug_isin.strip(), debug_ticker
        )
        anzeige_ticker = debug_used_ticker or debug_ticker

        if debug_data:
            st.write(f"Aufgelöster Ticker: `{debug_used_ticker}`")
            st.write(
                f"Kurs: **{debug_data['close']:.2f}** | "
                f"RSI: **{debug_data['rsi']:.2f}**"
            )
        else:
            st.error(f"Score nicht berechenbar. Grund: {debug_fehler}")

        # Rohdaten-Diagnose laeuft IMMER, auch wenn der Score oben fehlschlug -
        # genau dafuer gedacht (siehe Chat: "GD200 nicht berechenbar" nur ueber
        # die Fehlermeldung zu sehen, hilft nicht zu verstehen WO im 200-Tage-
        # Fenster die Luecke tatsaechlich sitzt).
        if anzeige_ticker:
            try:
                import yfinance as yf
                import pandas as pd

                raw = yf.download(
                    anzeige_ticker, period="2y", progress=False, auto_adjust=False
                )
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.get_level_values(0)

                gesamt_zeilen = len(raw)
                close_gueltig = int(raw["Close"].notna().sum())
                close_nan = int(raw["Close"].isna().sum())
                letzte_200_gueltig = int(raw["Close"].tail(200).notna().sum())

                st.caption(
                    f"**Rohdaten-Diagnose ({anzeige_ticker}):** {gesamt_zeilen} Zeilen "
                    f"gesamt, davon {close_gueltig} gültig / {close_nan} NaN. "
                    f"In den letzten 200 Zeilen: {letzte_200_gueltig} gültig "
                    f"(mind. 190 nötig für GD200)."
                )
                st.caption("Letzte 20 Handelstage (Close) - NaN-Lücken sind hier direkt sichtbar:")
                st.dataframe(raw[["Close"]].tail(20), use_container_width=True)
            except Exception as e:
                st.error(f"Rohdaten-Abruf fehlgeschlagen: {e}")

etfs = parse_isin_file("isin.txt")
sektor_lookup = {e["isin"]: e["sektor"] for e in etfs}

portfolio_isins = [p["isin"] for p in PORTFOLIO if not p.get("sold", False)]
etfs_isins = {e["isin"] for e in etfs}
for p in PORTFOLIO:
    if p["isin"] not in etfs_isins:
        etfs.append({"sektor": "Portfolio", "isin": p["isin"], "ticker": p.get("ticker")})

st.sidebar.info(f"📋 **{len(etfs)} ETFs** werden überwacht.")

fuehre_scan_durch(etfs, portfolio_isins)

# Aktive Positionen für Tab 2 ermitteln (noch nicht vollständig verkauft)
aktive_positionen = [p for p in PORTFOLIO if not p.get("sold", False)]
historie_positionen = [p for p in PORTFOLIO if p.get("partially_sold", False) or p.get("sold", False)]

# TABS REGISTER
tab1, tab2, tab3 = st.tabs([
    f"📋 Watchlist & Kaufsignale ({len(st.session_state.get('watchlist_signale', []))})",
    f"💼 Mein Portfolio ({len(aktive_positionen)})",
    f"📜 Historie ({len(historie_positionen)})",
])

with tab1:
    render_watchlist_tab(sektor_lookup, portfolio_isins, aktive_positionen)

with tab2:
    render_portfolio_tab(aktive_positionen, sektor_lookup)

with tab3:
    render_historie_tab(historie_positionen)
