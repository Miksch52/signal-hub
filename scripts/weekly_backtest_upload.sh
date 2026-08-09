#!/bin/bash
# Woechentlicher Pivot-Backtest (com.maick.pivot-backtest.plist, So 08:15).
#
# 1) --evaluate + Retro-Lauf wie bisher - schreibt Signal-Hub/data/pivot_backtest.json
#    aus den gereiften Forward-Picks (PIVOT_LOGBUCH, nur lokal auf diesem Mac).
# 2) NEU (2026-08-09): laedt das frische Ergebnis zusaetzlich nach R2 hoch. Vorher
#    sah nur ein manueller "deploy.command"-Doppelklick die frischen Daten - der
#    automatisierte Cloud-Deploy (deploy.yml) zieht seine Daten ausschliesslich aus
#    R2 und zeigte deshalb dauerhaft einen eingefrorenen alten Stand (die Cloud-
#    Pipeline selbst ruft --evaluate nie auf, nur dieser woechentliche lokale Lauf
#    tut das). Seit den neuen deploy-trigger.yml-Workflows in Price-Action-Hub/
#    Rotation-Dashboard laeuft deploy.yml deutlich haeufiger - ohne diesen Upload
#    waere die Live-Seite fast immer veraltet gewesen.
#
# Upload laeuft ueber den gemeinsamen Helfer upload_to_r2.sh (wrangler r2 object
# put, bereits lokal per "npx wrangler login" authentifiziert - keine zusaetzlichen
# R2-S3-Zugangsdaten noetig). Derselbe Helfer wird auch von run.py fuer
# score_backtest.json/score_faktoren_backtest.json genutzt (dort direkt nach
# jedem lokalen Lauf statt nur woechentlich, siehe run.py::pipeline()).
#
# WICHTIG: laedt NUR das aggregierte Ergebnis hoch (pivot_backtest.json/.js) -
# NIEMALS die Rohhistorien (pivot_logbuch.json, pivot_eval_state.json, logbuch.json
# unter ~/Library/Application Support/SignalHub/). Die bleiben bewusst rein lokal.
set -e

HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
PROJ="$(cd "$HIER/../.." && pwd)"                      # .../Maick Trading System

cd "$PROJ/Signal-Hub"
/usr/bin/python3 src/pivot_backtest.py --evaluate
/usr/bin/python3 src/pivot_backtest.py

"$HIER/upload_to_r2.sh" pivot_backtest.json pivot_backtest.js
