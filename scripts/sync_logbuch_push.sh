#!/bin/bash
# Gegenstueck zu sync_logbuch_pull.sh: sichert die (evtl. um heutige Picks
# erweiterten) Forward-Logbuecher zurueck nach R2 (_state/), DAMIT der
# naechste Lauf - egal ob Mac mini, MacBook oder Cloud-Runner - mit dem
# vollstaendigen Stand weitermacht statt wieder bei null anzufangen.
#
# NICHT zu verwechseln mit upload_to_r2.sh: das laedt nur die AGGREGIERTEN
# Backtest-Ausgaben aus Signal-Hub/data/ nach _deploy/ hoch (fuers Dashboard).
# Dieses Skript sichert die ROHEN Logbuecher aus
# ~/Library/Application Support/SignalHub/ nach _state/ (Grundlage fuer die
# naechste Auswertung, nicht selbst zur Anzeige gedacht).
set -u

HIER="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HIER/../.." && pwd)"
QUELLE="$HOME/Library/Application Support/SignalHub"

cd "$PROJ/cloudflare-worker"
for f in logbuch.json pivot_logbuch.json pivot_eval_state.json regime_logbuch.json; do
  if [ -f "$QUELLE/$f" ]; then
    if /usr/local/bin/npx --yes wrangler r2 object put \
        "signalhub-magazine/_state/$f" --file="$QUELLE/$f" --remote -y >/dev/null 2>&1; then
      echo "Gesichert: $f"
    else
      echo "  ! Sichern fehlgeschlagen: $f"
    fi
  fi
done
