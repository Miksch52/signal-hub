#!/bin/bash
# Holt die Forward-Logbuecher (logbuch.json, pivot_logbuch.json,
# pivot_eval_state.json) aus R2 (_state/) nach
# ~/Library/Application Support/SignalHub/ - Gegenstueck zu
# sync_logbuch_push.sh.
#
# Grund: Diese Dateien sind die Basis der UNVERZERRTEN Forward-Tests
# (score_backtest.py/score_faktoren_backtest.py/pivot_backtest.py) und
# wuchsen bisher NUR auf der Maschine, die sie zuletzt geschrieben hat
# (siehe src/pfade.py::LOKAL-Docstring) - ein GitHub-Actions-Runner ist pro
# Lauf frisch, hatte also nie den bisherigen Stand und verwarf seine
# taeglichen --log-Eintraege wieder (run.py::pipeline() dokumentiert das seit
# 2026-08-09 explizit als bekannte Einschraenkung). Der Cloud-Lauf macht
# denselben Pull/Push-Schritt per rclone direkt in
# .github/workflows/pipeline.yml (kein wrangler-Login auf dem Runner). Hier
# lokal per wrangler (bereits per "npx wrangler login" authentifiziert,
# gleiches Muster wie upload_to_r2.sh).
#
# Fehlende R2-Objekte (allererster Lauf) werden sauber uebersprungen - kein
# Fehler, die lokale Datei bleibt dann einfach wie sie ist.
set -u   # bewusst kein -e: ein fehlendes R2-Objekt darf den Aufrufer nicht abbrechen

HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
PROJ="$(cd "$HIER/../.." && pwd)"                      # .../Maick Trading System
ZIEL="$HOME/Library/Application Support/SignalHub"
mkdir -p "$ZIEL"

cd "$PROJ/cloudflare-worker"
for f in logbuch.json pivot_logbuch.json pivot_eval_state.json; do
  if /usr/local/bin/npx --yes wrangler r2 object get \
      "signalhub-magazine/_state/$f" --file="$ZIEL/$f" --remote >/dev/null 2>&1; then
    echo "Geladen: $f"
  else
    echo "Uebersprungen (noch kein R2-Stand): $f"
  fi
done
