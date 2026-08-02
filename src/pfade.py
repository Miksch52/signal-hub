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

import os

HIER = os.path.dirname(os.path.abspath(__file__))      # .../Signal-Hub/src
PROJEKT = os.path.dirname(HIER)                          # .../Signal-Hub
CONFIG = os.path.join(PROJEKT, "config.json")

DATA = os.path.join(PROJEKT, "data")                    # Outputs (iCloud, 1 Host)
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
SCORE_BACKTEST = os.path.join(DATA, "score_backtest.json")  # Trefferquoten Momentum-Score (Tier A/B)
SCORE_FAKTOREN_BACKTEST = os.path.join(DATA, "score_faktoren_backtest.json")  # Pro-Faktor-Erfolgsanalyse

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
STATE = os.path.join(LOKAL, "state.json")
LOGBUCH = os.path.join(LOKAL, "logbuch.json")
PIVOT_STATE = os.path.join(LOKAL, "pivot_state.json")   # Zustands-Uebergaenge fuer Push
PIVOT_LOGBUCH = os.path.join(LOKAL, "pivot_logbuch.json")  # Forward-Log der ARMED/BREAKOUT-Picks
PIVOT_EVAL_STATE = os.path.join(LOKAL, "pivot_eval_state.json")  # Reife-Meilensteine fuer Push
