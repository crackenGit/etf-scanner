"""
signal_log.py

Forward-Tracking-Log ueber Google Sheets: schreibt jedes echte Signal
(soft/voll) aus einem Watchlist-Scan als Zeile in ein externes Sheet -
inklusive ob/warum der Diversifikationsfilter es ausgeblendet hat.

Streamlit Cloud hat kein dauerhaftes Dateisystem - jede lokal geschriebene
Datei geht beim naechsten Reboot verloren. Ein Commit zurueck ins eigene
Repo wuerde ausserdem einen Redeploy (und damit einen App-Neustart)
ausloesen. Deshalb: externes Google Sheet als leichte, dauerhafte,
menschenlesbare Speicherung.

EINRICHTUNG (einmalig, siehe Chat fuer die Schritt-fuer-Schritt-Anleitung):
1. Google-Cloud-Projekt + Service Account anlegen, Sheets-API aktivieren.
2. Ein neues Google Sheet anlegen, mit der Service-Account-E-Mail teilen
   (Bearbeiter-Rechte).
3. In Streamlit Cloud: App -> Settings -> Secrets - dort 'gcp_service_account'
   (kompletter JSON-Key-Inhalt) und 'signal_log_sheet_id' (aus der Sheet-URL)
   eintragen.
Ohne diese Secrets loggt die App einfach nicht mit (kein Fehler, kein Absturz).

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
import traceback

import streamlit as st

from dip_score import FORMEL_VERSION

SIGNAL_LOG_SPALTEN = [
    "Datum", "Zeitstempel", "ISIN", "Ticker", "Name", "Sektor", "Kurs",
    "Dip_Score", "Signal_Stufe", "RSI_Score", "Trend_Score", "GD200_Score",
    "EMA50_Score", "Drawdown_Score", "Ist_Portfolio", "Ausgeblendet",
    "Ausblend_Grund", "Formel_Version", "Trefferwahrsch_Pct", "Trefferwahrsch_Rendite",
    "Gruppe",
]

KONTROLLGRUPPE_GROESSE = 5  # Anzahl der taeglich zusaetzlich geloggten "besten Nicht-
                             # Signale" (Score unter der Schwelle, aber schon RSI<40-
                             # Kandidat) - siehe Chat: sonst sehen wir nie, ob ETFs knapp
                             # unterhalb der Schwelle sich ebenfalls bewaehrt haetten
                             # (reine Praezision statt echter Trennschaerfen-Pruefung).
                             # NEUE Spalte bewusst ANS ENDE gehaengt, nicht dazwischen -
                             # sonst wuerden sich bei bestehenden Sheet-Zeilen alle
                             # nachfolgenden Werte verschieben (siehe Chat).


@st.cache_resource
def hole_signal_sheet():
    """Verbindung zum Google Sheet fuer das Forward-Tracking-Log. Gibt None
    zurueck, falls die Secrets fehlen oder die Verbindung fehlschlaegt - die
    App laeuft dann einfach ohne Logging normal weiter. Der Fehlergrund wird
    zusaetzlich in st.session_state abgelegt, damit er in der UI sichtbar
    gemacht werden kann (siehe signal_log_status()), statt still zu
    verschwinden - Cache-Fehlschlaege sonst schwer zu diagnostizieren."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(st.secrets["signal_log_sheet_id"]).sheet1
        werte = sheet.get_all_values()
        if not werte:
            sheet.append_row(SIGNAL_LOG_SPALTEN)
        elif werte[0] != SIGNAL_LOG_SPALTEN:
            # Header existiert, ist aber veraltet (z.B. neue Spalte wie
            # Formel_Version hinzugekommen) - Zeile 1 in-place ueberschreiben,
            # statt eine zusaetzliche Kopfzeile einzufuegen. Bestehende
            # Datenzeilen bleiben unberuehrt (fehlende neue Spalten zeigen
            # sich einfach als leere Zellen, kein Datenverlust).
            sheet.update("A1", [SIGNAL_LOG_SPALTEN])
        st.session_state["signal_log_fehler"] = None
        return sheet
    except Exception as e:
        st.session_state["signal_log_fehler"] = f"{type(e).__name__}: {e!r}"
        st.session_state["signal_log_traceback"] = traceback.format_exc()
        return None


def signal_log_status():
    """Kleine, sichtbare Diagnose fuer die Watchlist-Seite: laeuft das
    Sheet-Logging gerade, und falls nicht, warum? Ruft hole_signal_sheet()
    bewusst NICHT selbst auf (das passiert schon in logge_signale) -
    liest nur das zuletzt abgelegte Ergebnis aus session_state."""
    fehler = st.session_state.get("signal_log_fehler", "noch nicht versucht")
    if fehler is None:
        st.caption("✅ **Signal-Log:** aktiv (Google Sheet verbunden)")
    else:
        st.caption(
            f"🔕 **Signal-Log:** inaktiv - {fehler}. "
            f"Secrets pruefen (Settings -> Secrets) und danach **Reboot app** "
            f"(nicht nur Rerun) - siehe Chat."
        )
        tb = st.session_state.get("signal_log_traceback")
        if tb:
            with st.expander("🔍 Vollständiger Fehler (für Diagnose)"):
                st.code(tb, language="text")


def logge_signale(df_watch):
    """Schreibt jedes echte Signal (soft/voll) des aktuellen Scans als neue
    Zeile ins Google-Sheet-Log - inklusive ob/warum der Diversifikations-
    Filter es ausgeblendet hat. Zusaetzlich: die besten KONTROLLGRUPPE_GROESSE
    Nicht-Signale des Tages (siehe Chat) - ohne die koennten wir nie pruefen,
    ob die Schwelle wirklich sinnvoll trennt oder ob knapp darunter liegende
    ETFs sich ebenso bewaehrt haetten. Best-effort: Fehler hier duerfen die
    App nie zum Absturz bringen. Dedupliziert gegen bereits heute geloggte
    ISINs, damit mehrfaches Neuladen am selben Tag keine Duplikate erzeugt."""
    sheet = hole_signal_sheet()
    if sheet is None:
        return

    try:
        heute = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%d")

        bestehende_isins_heute = set()
        try:
            werte = sheet.get_all_values()
            for zeile in werte[1:]:
                if len(zeile) >= 3 and zeile[0] == heute:
                    bestehende_isins_heute.add(zeile[2])
        except Exception:
            pass

        def zeile_bauen(row, gruppe, signal_stufe):
            return [
                heute,
                row["Zeitstempel"],
                row["ISIN"],
                row["Ticker"],
                row["Name"],
                row["Sektor"],
                round(row["Kurs"], 2),
                row["Dip Score"],
                signal_stufe,
                row["RSI_Score"],
                row["Trend_Score"],
                row["GD200_Score"],
                row["EMA50_Score"],
                row["Drawdown Score"],
                bool(row["Ist_Portfolio"]),
                bool(row.get("Ausgeblendet", False)),
                row.get("Ausblend_Grund", ""),
                FORMEL_VERSION,
                row["Trefferwahrsch_Pct"] if row["Trefferwahrsch_Pct"] is not None else "",
                row["Trefferwahrsch_Rendite"] if row["Trefferwahrsch_Rendite"] is not None else "",
                gruppe,
            ]

        neue_zeilen = []

        # 1) Echte Signale (soft/voll) - wie bisher
        for _, row in df_watch.iterrows():
            if not (row["Ist_Kaufsignal"] or row["Ist_Soft_Signal"]):
                continue
            if row["ISIN"] in bestehende_isins_heute:
                continue
            signal_stufe = "voll" if row["Ist_Kaufsignal"] else "soft"
            neue_zeilen.append(zeile_bauen(row, "Signal", signal_stufe))

        # 2) Kontrollgruppe: die besten Nicht-Signale des Tages (siehe Chat) -
        # bereits RSI<40-Kandidaten (df_watch ist darauf vorgefiltert), aber
        # unter der Score-Schwelle geblieben. Zeigt langfristig, ob die
        # Schwelle sinnvoll trennt oder ob wir knapp darunter Chancen verpassen.
        nicht_signale = df_watch[
            ~(df_watch["Ist_Kaufsignal"] | df_watch["Ist_Soft_Signal"])
        ].copy()
        nicht_signale = nicht_signale[~nicht_signale["ISIN"].isin(bestehende_isins_heute)]
        nicht_signale = nicht_signale.sort_values("Dip Score", ascending=False).head(KONTROLLGRUPPE_GROESSE)

        for _, row in nicht_signale.iterrows():
            neue_zeilen.append(zeile_bauen(row, "Kontrollgruppe", "kein_signal"))

        if neue_zeilen:
            sheet.append_rows(neue_zeilen)
        st.session_state["signal_log_letzter_lauf"] = (
            f"{len(neue_zeilen)} neue Zeile(n) geloggt"
        )
    except Exception as e:
        st.session_state["signal_log_fehler"] = f"{type(e).__name__}: {e!r}"
        st.session_state["signal_log_traceback"] = traceback.format_exc()
