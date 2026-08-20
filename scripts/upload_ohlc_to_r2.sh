#!/bin/bash
# Laedt den heutigen OHLC-Snapshot (Signal-Hub/data/ohlc-history/JJJJ-MM-TT.csv,
# siehe src/ohlc_history.py) beim LOKALEN Mac-mini-Lauf nach R2 hoch - der
# Cloud-Lauf macht denselben Schritt per rclone direkt in
# .github/workflows/pipeline.yml (kein wrangler-Login auf dem Runner noetig).
# Beide Pfade muessen zusammen bedacht werden (siehe CLAUDE.md
# "Geraeteunabhaengigkeit"): ohne diesen Schritt wuerde die Kurshistorie nur
# waechsen, wenn zufaellig der Cloud-Lauf zuerst dran war.
#
# Per "wrangler r2 object put" (lokal per "npx wrangler login" authentifiziert,
# siehe cloudflare-worker/) - keine zusaetzlichen R2-S3-Zugangsdaten auf
# diesem Mac noetig. Fehlt die heutige Datei (z.B. weil kein faelliger Slot
# lief), wird sauber uebersprungen statt mit Fehler abzubrechen.
set -e

HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
PROJ="$(cd "$HIER/../.." && pwd)"                      # .../Maick Trading System
HEUTE="$(date +%Y-%m-%d)"
DATEI="$HIER/../data/ohlc-history/$HEUTE.csv"

if [ ! -f "$DATEI" ]; then
  echo "OHLC-R2-Upload: $HEUTE.csv fehlt - uebersprungen."
  exit 0
fi

cd "$PROJ/cloudflare-worker"
/usr/local/bin/npx --yes wrangler r2 object put \
  "signalhub-magazine/ohlc-history/$HEUTE.csv" \
  --file="$DATEI" --remote -y
echo "OHLC-R2-Upload: $HEUTE.csv hochgeladen."
