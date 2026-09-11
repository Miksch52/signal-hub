#!/bin/bash
# Spiegelt die in der Cloud berechneten Ausgaben aus R2 (_deploy/) auf diesen
# Mac, damit die LAN-Versionen von Startseite, Signal-Hub, Price-Action-Hub und
# Rotation-Dashboard (8090/8091) aktuell bleiben (seit 2026-09-11).
#
# Ersetzt den einzigen weiter noetigen Effekt des frueheren LaunchAgents
# com.maick.signalhub: der rechnete dieselbe Pipeline lokal nach, pushte
# denselben Slot ein zweites Mal an denselben ntfy-Kanal und schrieb
# Forward-Logbuecher parallel zur Cloud nach R2. Dieser Job rechnet nichts,
# pusht nichts und schreibt nichts nach R2 - er liest nur.
#
# --update: eine lokal neuere Datei (z. B. nach "Aktualisieren" am 8091-Server)
# wird nie ueberschrieben. Nur .json/.js, nie ein Loeschen lokaler Dateien.
# SPIEGEL_TROCKEN=1 zeigt nur, was geholt wuerde.
set -u
HIER="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HIER/../.." && pwd)"
RCLONE="$(command -v rclone || true)"
[ -n "$RCLONE" ] || RCLONE="$HOME/bin/rclone"
if [ ! -x "$RCLONE" ]; then
  echo "$(date '+%F %T') Cloud-Spiegel: rclone nicht gefunden - uebersprungen."
  exit 0
fi
TROCKEN=""
[ "${SPIEGEL_TROCKEN:-}" = "1" ] && TROCKEN="--dry-run"
for paar in "signal-hub:Signal-Hub/data" "price-action-hub:Price-Action-Hub/data" "rotation-dashboard:Rotation-Dashboard/data"; do
  quelle="${paar%%:*}"; ziel="$PROJ/${paar#*:}"
  if "$RCLONE" copy "r2:signalhub-magazine/_deploy/$quelle/" "$ziel/" \
       --include "*.json" --include "*.js" --update $TROCKEN 2>&1 | grep -E "Skipped copy|ERROR" | sed "s|^|  $quelle: |"; then :; fi
done
echo "$(date '+%F %T') Cloud-Spiegel ${TROCKEN:+(Trockenlauf) }abgeschlossen."
