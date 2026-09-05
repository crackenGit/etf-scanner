"""
scanner_data.py

Datenabruf- und Score-Berechnungs-Schicht: ISIN/Ticker-Auflösung, Yahoo-
Finance-Anbindung (mit Retry und Fallback-Cache), Marktregime-Check und
die zentrale berechne_indikatoren()-Funktion, die pro ETF die Rohdaten
lädt und über dip_score.py (einzige Quelle der Wahrheit für die Formel)
den Dip Score berechnet.

Wird sowohl von watchlist_tab.py als auch von portfolio_tab.py genutzt -
Code hierher ausgelagert, um Duplikation zwischen den beiden Tabs zu
vermeiden (beide brauchen dieselbe Datengrundlage).

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung
(siehe Chat für den Kontext).
"""

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import time

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from dip_score import (
    berechne_indikator_serien,
    score_am_punkt,
    MARKT_BENCHMARK_TICKER,
)
from trefferwahrscheinlichkeit import schaetze_trefferwahrscheinlichkeit

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
    Prüft, ob ein breiter Marktindex über seinem eigenen GD200 liegt - dient
    als Bulle/Bär-Marktphasen-Indikator (rein informativ, beeinflusst den
    Dip Score seit der Regime-Malus-Entfernung nicht mehr - siehe Chat: ein
    Backtest zeigte, dass die fürs Ziel relevante Erreichquote bei
    schlechtem Regime nicht schlechter war, teils sogar besser).

    Gibt zusätzlich zurück, seit wie vielen Handelstagen die aktuelle Phase
    andauert. Ein kürzlicher Phasenwechsel ist ein Hinweis darauf, dass ein
    Re-Backtest sinnvoll sein könnte - die aktuelle Formel ist ja auf die
    Marktbedingungen der bisherigen Backtest-Historie kalibriert.

    Rückgabe: (regime_ok: bool, seit_tagen: int | None)
    """
    ticker = benchmark_ticker or MARKT_BENCHMARK_TICKER
    try:
        df = yf.download(ticker, period="3y", progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        if len(close) < 200:
            return True, None  # nicht genug Historie -> im Zweifel neutral

        gd200 = close.rolling(window=200).mean()
        ueber_gd200 = (close > gd200).dropna()
        if ueber_gd200.empty:
            return True, None

        aktueller_status = bool(ueber_gd200.iloc[-1])
        seit_tagen = 0
        for wert in reversed(ueber_gd200.tolist()):
            if bool(wert) == aktueller_status:
                seit_tagen += 1
            else:
                break

        return aktueller_status, seit_tagen
    except Exception:
        return True, None  # Fail-safe: bei Datenproblem neutral bleiben


@st.cache_data(ttl=86400 * 30)  # 30 Tage - ETF-Namen ändern sich praktisch nie
def hole_etf_name(ticker):
    """Lädt den vollen ETF-Namen über yfinance (schwerere .info-Abfrage,
    deshalb sehr lang gecacht - nur beim allerersten Scan pro Ticker
    wirklich fällig). Fällt bei Fehlern/fehlenden Daten auf den Ticker
    selbst zurück, damit die Spalte nie leer/kaputt aussieht."""
    try:
        info = yf.Ticker(ticker).info
        name = info.get("longName") or info.get("shortName")
        return name if name else ticker
    except Exception:
        return ticker


@st.cache_resource
def _letzte_bekannte_daten():
    """Persistenter Speicher (ueberlebt Streamlit-Reruns, nicht aber einen
    Reboot) fuer den zuletzt ERFOLGREICH berechneten Datensatz je ISIN.
    Dient als Fallback, wenn ein Live-Abruf temporaer fehlschlaegt (z.B.
    vor Handelsbeginn oder am Wochenende, wenn Yahoo Finance die juengsten
    Tagesbalken noch nicht vollstaendig/fehlerfrei liefert - siehe Chat).
    Ein einfaches Dict reicht: @st.cache_resource gibt bei jedem Aufruf
    dasselbe Objekt zurueck, Mutation ist hier bewusst gewollt."""
    return {}


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
    letzter_fehler = None
    for ticker_symbol in kandidaten:
        # Bis zu 3 Versuche pro Ticker mit kurzer Pause dazwischen - faengt
        # kurzzeitiges Rate-Limiting bei Yahoo ab, das v.a. durch den
        # parallelen (ThreadPoolExecutor-)Scan der Watchlist ausgeloest
        # werden kann. Ein sequenzieller Backtest-Lauf mit Pausen zwischen
        # Anfragen ist davon kaum betroffen - dieselben Ticker laden dort
        # praktisch immer erfolgreich (siehe Chat).
        for versuch in range(3):
            try:
                df = yf.download(ticker_symbol, period="2y", progress=False, auto_adjust=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                if not df.empty and len(df) >= 200:
                    data, erfolgreicher_ticker = df, ticker_symbol
                    break
                elif df.empty:
                    letzter_fehler = "leere Antwort von Yahoo Finance (evtl. Rate-Limit)"
                else:
                    letzter_fehler = f"nur {len(df)} Handelstage erhalten (mind. 200 noetig)"
            except Exception as e:
                letzter_fehler = f"{type(e).__name__}: {e}"
            if versuch < 2:
                time.sleep(1.5)
        if data is not None:
            break

    if data is None:
        cache = _letzte_bekannte_daten()
        if isin in cache:
            alt_ergebnis, alt_ticker, alt_zeitpunkt = cache[isin]
            alt_ergebnis = dict(alt_ergebnis)
            alt_ergebnis["ist_stale"] = True
            alt_ergebnis["stale_seit"] = alt_zeitpunkt
            return alt_ergebnis, alt_ticker, None
        return None, (kandidaten[0] if kandidaten else "N/A"), letzter_fehler

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
    regime_ok, regime_seit_tagen = markt_regime_ok()
    score_ergebnis = score_am_punkt(indikatoren, -1, regime_ok=regime_ok)

    # Absicherung: GD200 kann trotz >=200 Rohzeilen NaN bleiben, wenn
    # innerhalb des 200-Tage-Fensters Kurslücken bestehen (z.B. lückenhafte
    # Yahoo-Daten zu fruehen Uhrzeiten, oder knapp zu kurze echte Historie).
    # dip_score.py faengt NaN intern mit einem 0.0-Platzhalter ab, damit die
    # Score-Formel nicht selbst abstuerzt - aber 0.0 ist kein gueltiger
    # GD200-Wert und wuerde downstream (Anzeige, Prozent-Berechnungen) zu
    # Divisionen durch Null fuehren. Deshalb hier explizit als fehlgeschlagen
    # behandeln, statt mit einem unbrauchbaren Platzhalter weiterzurechnen.
    if score_ergebnis["gd200"] == 0.0:
        cache = _letzte_bekannte_daten()
        if isin in cache:
            alt_ergebnis, alt_ticker, alt_zeitpunkt = cache[isin]
            alt_ergebnis = dict(alt_ergebnis)
            alt_ergebnis["ist_stale"] = True
            alt_ergebnis["stale_seit"] = alt_zeitpunkt
            return alt_ergebnis, alt_ticker, None
        return None, erfolgreicher_ticker, "GD200 nicht berechenbar (zu wenig gültige Kursdaten im 200-Tage-Fenster)"

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

    # --- ZWEITES, GETRENNTES MODELL: Trefferwahrscheinlichkeit ---
    # Bewusst NICHT Teil des Dip Score - siehe trefferwahrscheinlichkeit.py
    # und Chat fuer die Herleitung ("Meta-Labeling").
    trefferwahrsch_pct, trefferwahrsch_rendite, trefferwahrsch_n = schaetze_trefferwahrscheinlichkeit(
        score_ergebnis["drawdown_atr_multiple"], score_ergebnis["ema50_score"]
    )

    ergebnis = {
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
        "drawdown_atr_multiple": score_ergebnis["drawdown_atr_multiple"],
        "trefferwahrscheinlichkeit_pct": trefferwahrsch_pct,
        "trefferwahrscheinlichkeit_rendite": trefferwahrsch_rendite,
        "trefferwahrscheinlichkeit_n": trefferwahrsch_n,
        "regime_ok": regime_ok,
        "regime_seit_tagen": regime_seit_tagen,
        "yahoo_zeit": yahoo_zeit,
        "return_serie": close.pct_change().dropna().tail(180),
        "ist_stale": False,
        "stale_seit": None,
    }
    _letzte_bekannte_daten()[isin] = (
        ergebnis,
        erfolgreicher_ticker,
        datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m. %H:%M Uhr"),
    )
    return ergebnis, erfolgreicher_ticker, None
