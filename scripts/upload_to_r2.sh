#!/bin/bash
# Laedt einzelne Dateien aus Signal-Hub/data/ nach R2 (_deploy/signal-hub/) hoch,
# per "wrangler r2 object put" (lokal per "npx wrangler login" authentifiziert,
# siehe cloudflare-worker/ - keine zusaetzlichen R2-S3-Zugangsdaten auf diesem
# Mac noetig). NUR fuer abgeleitete/aggregierte Ausgabedateien gedacht (z.B.
# pivot_backtest.json, score_backtest.json) - NIEMALS fuer die rohen Historien
# unter ~/Library/Application Support/SignalHub/ (die bleiben bewusst lokal).
#
# Aufruf: upload_to_r2.sh datei1.json [datei2.json ...]
# Pfade relativ zu Signal-Hub/data/. Fehlende Dateien werden sauber uebersprungen
# (kein Fehler - reift-ueber-Zeit-Dateien existieren anfangs oft noch nicht).
set -e

HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
PROJ="$(cd "$HIER/../.." && pwd)"                      # .../Maick Trading System

cd "$PROJ/cloudflare-worker"
for f in "$@"; do
  src="../Signal-Hub/data/$f"
  if [ -f "$src" ]; then
    /usr/local/bin/npx --yes wrangler r2 object put \
      "signalhub-magazine/_deploy/signal-hub/$f" \
      --file="$src" --remote -y
    echo "Hochgeladen: $f"
  else
    echo "Uebersprungen (fehlt): $f"
  fi
done
