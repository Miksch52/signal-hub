#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zentrale Pfade fuer den Daily Signal Hub.

Trennt bewusst:
  DATA  = Signal-Hub/data        -> Dashboard-Ausgaben (signals.json/js, config.js).
          Liegt in iCloud (Dashboard braucht es relativ); wird pro Lauf 1x geschrieben.
  LOKAL = ~/Library/Application Support/SignalHub
          -> Caches, State, Roh-Signale, Logbuch. NICHT in iCloud
          (sonst Sync-Konflikte/Eviction bei haeufigem Schreiben).
"""

import json
import os

HIER = os.path.dirname(os.path.abspath(__file__))      # .../Signal-Hub/src
PROJEKT = os.path.dirname(HIER)                          # .../Signal-Hub
CONFIG = os.path.join(PROJEKT, "config.json")

DATA = os.path.join(PROJEKT, "data")                    # Outputs (iCloud, 1 Host)
# Quartalsdaten aus SEC Form 13F. BEWUSST NICHT unter data/ (das ist komplett
# gitignored): die Tabelle wird nur viermal im Jahr gebaut, ist klein, enthaelt
# ausschliesslich oeffentliche SEC-Daten und muss in JEDEM Checkout vorliegen -
# auch im Cloud-Lauf, ohne Umweg ueber R2/rclone. Deshalb versioniert.
DATEN_13F = os.path.join(PROJEKT, "daten-13f")
INSTITUTIONAL_13F = os.path.join(DATEN_13F, "institutional_13f.json")
LOKAL = os.path.expanduser("~/Library/Application Support/SignalHub")  # Caches/State (lokal)

os.makedirs(DATA, exist_ok=True)
os.makedirs(LOKAL, exist_ok=True)

# Ausgaben (Dashboard liest diese)
SIGNALS_JSON = os.path.join(DATA, "signals.json")
SIGNALS_JS = os.path.join(DATA, "signals.js")
CONFIG_JS = os.path.join(DATA, "config.js")
PIVOT_JSON = os.path.join(DATA, "pivot.json")           # Pivot-Armed-Ausgabe
PIVOT_JS = os.path.join(DATA, "pivot.js")               # file://-Fallback
PIVOT_HISTORY = os.path.join(DATA, "pivot_history.json")  # Tages-Historie BREAKOUT/ARMED/CHEAT
PIVOT_HISTORY_JS = os.path.join(DATA, "pivot_history.js")  # file://-Fallback
PIVOT_BACKTEST = os.path.join(DATA, "pivot_backtest.json")  # Backtest-Auswertung
PIVOT_BACKTEST_JS = os.path.join(DATA, "pivot_backtest.js")  # file://-Fallback
REGIME_BACKTEST = os.path.join(DATA, "regime_backtest.json")  # Markt-Regime-Forward-Test
REGIME_BACKTEST_JS = os.path.join(DATA, "regime_backtest.js")  # file://-Fallback
# Taeglicher OHLC-Snapshot (seit 2026-08-20, siehe ohlc_history.py): einzige
# tatsaechlich dauerhafte Kurshistorie im System - der Yahoo-Tages-Cache
# (YAHOO_CACHE unten) wird auf dem Cloud-Runner nie gesichert und faengt bei
# jedem Lauf leer an. Lokal + Cloud schreiben hierher, die Pipeline laedt
# jede Tagesdatei zusaetzlich nach r2:signalhub-magazine/ohlc-history/ hoch.
OHLC_HISTORY_DIR = os.path.join(DATA, "ohlc-history")
SCORE_BACKTEST = os.path.join(DATA, "score_backtest.json")  # Trefferquoten Momentum-Score (Tier A/B)
SCORE_FAKTOREN_BACKTEST = os.path.join(DATA, "score_faktoren_backtest.json")  # Pro-Faktor-Erfolgsanalyse

# Minervini-Lexikon (Phase 0, seit 2026-08-31): manuell erfasste X-Posts von
# Mark Minervini, je Eintrag mit Marktkontext aus REGIME_LOGBUCH/signals.json
# verknuepft, siehe minervini_lexikon.py. EINGANG ist die Ablage fuer rohe
# Screenshot-Dateien (Nutzer zieht sie rein), BILDER die verarbeitete,
# umbenannte Ablage, die vom Lexikon-JSON referenziert wird.
MINERVINI_LEXIKON_DIR = os.path.join(DATA, "minervini-lexikon")
MINERVINI_LEXIKON_JSON = os.path.join(MINERVINI_LEXIKON_DIR, "minervini_lexikon.json")
MINERVINI_LEXIKON_JS = os.path.join(MINERVINI_LEXIKON_DIR, "minervini_lexikon.js")
MINERVINI_LEXIKON_BILDER = os.path.join(MINERVINI_LEXIKON_DIR, "bilder")
MINERVINI_LEXIKON_EINGANG = os.path.join(PROJEKT, "minervini-lexikon-eingang")

# Lokale Laufzeitdaten (kein iCloud)
RAW_PDF = os.path.join(LOKAL, "signals_raw.json")
RAW_MAIL = os.path.join(LOKAL, "signals_raw_mail.json")
RAW_FINVIZ = os.path.join(LOKAL, "signals_raw_finviz.json")
RAW_MARKETS360 = os.path.join(LOKAL, "signals_raw_markets360.json")
RAW_TRENDSCREENER = os.path.join(LOKAL, "signals_raw_trendscreener.json")

# Cloud-Datenkanal: von den Markets-360- und Trend-Screener-Workflows per
# rclone nach r2:signalhub-magazine/{markets360,trendscreener}/ hochgeladen,
# vom signal-hub.yml-Workflow zusammen mit dem PDF-Magazin nach _magazine/
# gezogen (gleicher rclone-copy-Schritt, neue Praefixe). Nur im Cloud-Lauf
# vorhanden (GitHub Actions) - beim lokalen Lauf auf dem Mac mini fehlt
# dieser Ordner, siehe LOKAL_MARKETS360/LOKAL_TRENDSCREENER unten.
EXTERN_MARKETS360 = os.path.join(PROJEKT, "_magazine", "markets360", "markets360_latest.csv")
EXTERN_TRENDSCREENER = os.path.join(PROJEKT, "_magazine", "trendscreener", "signals.json")
# Traderfox-PDFs liegen im selben rclone-Sync direkt im Root von _magazine/
# (manueller Upload nach r2:signalhub-magazine/, kein eigenes Praefix wie bei
# markets360/trendscreener). pdf_screener.py faellt hierauf zurueck, wenn der
# lokale iCloud-Ordner (config.json -> quellen.pdf.ordner) nicht existiert.
EXTERN_PDF_ORDNER = os.path.join(PROJEKT, "_magazine")

# Lokaler Lauf auf dem Mac mini: Markets 360 und der Trend-Screener schreiben
# ihre Nachtscan-Ergebnisse dort bereits direkt hin (eigene LaunchAgents,
# dieselbe Maschine) - kein rclone/_magazine noetig. markets360_screener.py/
# trendscreener_screener.py probieren diese Pfade zuerst, bevor sie auf
# EXTERN_* (Cloud-Lauf) zurueckfallen.
_ICLOUD = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
LOKAL_MARKETS360 = os.path.join(
    _ICLOUD, "Trading-System", "Mein Minervini Trading-Journal-System",
    "MinerviniMarkets360", "exports", "markets360_latest.csv")
LOKAL_TRENDSCREENER = os.path.expanduser(
    "~/Library/Application Support/LokalerTrendScreener/output/signals.json")
YAHOO_CACHE = os.path.join(LOKAL, "yahoo_cache.json")
SYMBOL_CACHE = os.path.join(LOKAL, "symbol_cache.json")
PROFIL_CACHE = os.path.join(LOKAL, "profil_cache.json")
FUNDAMENTAL_CACHE = os.path.join(LOKAL, "fundamental_cache.json")  # EPS/Umsatz-Wachstum (7-Tage-TTL)
CODE33_CACHE = os.path.join(LOKAL, "code33_cache.json")  # Code-33-Kriterien (7-Tage-TTL)
INSTITUTIONAL_CACHE = os.path.join(LOKAL, "institutional_cache.json")  # institutioneller Besitzanteil (7-Tage-TTL)
STATE = os.path.join(LOKAL, "state.json")
LOGBUCH = os.path.join(LOKAL, "logbuch.json")
PIVOT_STATE = os.path.join(LOKAL, "pivot_state.json")   # Zustands-Uebergaenge fuer Push
PIVOT_LOGBUCH = os.path.join(LOKAL, "pivot_logbuch.json")  # Forward-Log der ARMED/BREAKOUT-Picks
PIVOT_EVAL_STATE = os.path.join(LOKAL, "pivot_eval_state.json")  # Reife-Meilensteine fuer Push
REGIME_LOGBUCH = os.path.join(LOKAL, "regime_logbuch.json")  # Forward-Log des Markt-Regimes je Markt
# Plausibilitaetswaechter (seit 2026-08-23, siehe quellen_watchdog.py): Tages-
# Historie der Trefferzahlen je Rohsignal-Quelle + Anti-Spam-Zustand fuer den
# "0 Treffer trotz verlaesslicher Historie"-Push. Wie die uebrigen State-
# Dateien lokal (nicht iCloud) und per pipeline.yml aus/nach R2 gesichert -
# ein frischer Cloud-Runner haette sonst nie eine Historie, gegen die sich
# "heute ploetzlich 0" ueberhaupt erkennen liesse.
QUELLEN_HISTORIE = os.path.join(LOKAL, "quellen_historie.json")
QUELLEN_WATCHDOG_STATE = os.path.join(LOKAL, "quellen_watchdog_state.json")


# --- Atomares Schreiben -----------------------------------------------------
# Grund (2026-08-28): Ein direktes open(pfad, "w") kuerzt die Datei sofort auf
# 0 Bytes und fuellt sie erst ueber die naechsten Millisekunden bis Sekunden
# wieder auf. In diesem Fenster sieht JEDER andere Leser eine unvollstaendige
# Datei - bei signals.json (~4 MB) hat genau das den 8090-Server dazu gebracht,
# ein 200 mit zu wenig Bytes auszuliefern (net::ERR_CONTENT_LENGTH_MISMATCH im
# Browser, siehe server_mts.py::_serve_json_snapshot im Hauptprojekt).
#
# Stattdessen: in eine Nebendatei schreiben und erst danach per os.replace()
# an ihren Platz schieben. Das Umbenennen ist auf demselben Dateisystem eine
# atomare Operation - ein Leser sieht entweder komplett den alten oder komplett
# den neuen Stand, nie etwas dazwischen. Hat er die Datei bereits geoeffnet,
# liest er den alten Inhalt ungestoert zu Ende.
#
# Gilt fuer beide Sorten Ausgabe, aus je eigenem Grund:
#   DATA  - wird waehrend des Laufs von aussen gelesen (Server/Dashboard).
#   LOKAL - Caches/State: schuetzt vor einer halb geschriebenen Datei, wenn
#           der Lauf mittendrin abbricht (Cloud-Runner-Timeout, Ctrl+C). Die
#           war sonst beim naechsten Lauf unlesbar und der Cache still leer.
#
# Gleiches Muster wie server.py beim Speichern der config.json.

def schreibe_atomar(pfad, schreiber):
    """Ruft schreiber(fp) auf eine geoeffnete Nebendatei auf und schiebt sie
    danach per os.replace() an ihren Platz. Bricht das Schreiben ab, bleibt die
    bisherige Datei unveraendert und die Nebendatei wird aufgeraeumt."""
    tmp = pfad + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fp:
            schreiber(fp)
        os.replace(tmp, pfad)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def schreibe_json_atomar(pfad, obj, **dump_args):
    """schreibe_atomar() fuer den Normalfall 'eine JSON-Datei'.
    dump_args werden an json.dump durchgereicht (ensure_ascii, indent, ...)."""
    schreibe_atomar(pfad, lambda fp: json.dump(obj, fp, **dump_args))
