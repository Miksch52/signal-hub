#!/bin/bash
# Laedt die in diesem Lauf geaenderten OHLC-Tagesdateien (Liste in
# Signal-Hub/data/ohlc-history/.geaendert, siehe src/ohlc_history.py) beim
# LOKALEN Mac-mini-Lauf nach R2 hoch - der Cloud-Lauf macht denselben Schritt
# per rclone direkt in .github/workflows/pipeline.yml. Beide Pfade muessen
# zusammen bedacht werden (siehe CLAUDE.md "Geraeteunabhaengigkeit").
#
# Seit 2026-09-11 (Befund B10): nur Tage, die es in R2 noch NICHT gibt. Vorher
# ueberschrieb dieser Upload die R2-Datei des Tages mit dem lokalen Stand. Die
# Historie wird seitdem nur noch angehaengt - der lokale Mac kennt aber den
# R2-Bestand nicht und wuerde mit seinem Teilstand vollstaendigere Cloud-Daten
# ersetzen. Fehlende Titel an einem bestehenden Tag ergaenzt die Cloud-Pipeline
# selbst (sie holt den R2-Stand vorher zurueck).
#
# Per "wrangler r2 object get/put" (lokal per "npx wrangler login"
# authentifiziert, siehe cloudflare-worker/) - keine zusaetzlichen
# R2-S3-Zugangsdaten auf diesem Mac noetig.
set -e

HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
PROJ="$(cd "$HIER/../.." && pwd)"                      # .../Maick Trading System
DIR="${OHLC_DIR:-$HIER/../data/ohlc-history}"
LISTE="$DIR/.geaendert"

if [ ! -s "$LISTE" ]; then
  echo "OHLC-R2-Upload: keine geaenderten Tage - uebersprungen."
  exit 0
fi

cd "$PROJ/cloudflare-worker"
while IFS= read -r NAME || [ -n "$NAME" ]; do
  [ -n "$NAME" ] || continue
  KEY="signalhub-magazine/ohlc-history/$NAME"
  if /usr/local/bin/npx --yes wrangler r2 object get "$KEY" --remote --pipe >/dev/null 2>&1; then
    echo "OHLC-R2-Upload: $NAME existiert in R2 bereits - bleibt unveraendert."
  else
    /usr/local/bin/npx --yes wrangler r2 object put "$KEY" --file="$DIR/$NAME" --remote -y
    echo "OHLC-R2-Upload: $NAME hochgeladen."
  fi
done < "$LISTE"
