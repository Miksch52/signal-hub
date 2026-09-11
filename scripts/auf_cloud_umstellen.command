#!/bin/bash
# Loest den lokalen Signal-Hub-Zeitplan (LaunchAgent com.maick.signalhub) ab,
# sobald der Cloudflare-Worker-Cron die Cloud-Laeufe puenktlich ausloest
# (seit 2026-09-11). Doppelklick oder: bash auf_cloud_umstellen.command
#
# Warum: der LaunchAgent rechnet dieselbe Pipeline lokal nach, pusht denselben
# Slot ein zweites Mal an denselben ntfy-Kanal (bestaetigt am 11.09.: lokal
# 07:34, Cloud nochmals gegen Mittag) und schreibt Forward-Logbuecher parallel
# zur Cloud nach R2 (_state/). Solange GitHubs Cron Stunden zu spaet kam, war
# er der einzige puenktliche Morgen-Push - mit dem Worker-Cron entfaellt das.
#
# Ersatz fuer seinen einzigen weiter noetigen Effekt (frische lokale Daten fuer
# die LAN-Versionen): der reine Lese-Job com.maick.cloudspiegel, alle 30 Min.
#
# Sicherung: bricht ab, solange der Worker-Cron noch keinen erfolgreichen
# Signal-Hub-Lauf ausgeloest hat - sonst ginge der puenktliche Morgen-Push
# verloren. Die Plist des alten Agents bleibt liegen (nur deaktiviert).
set -euo pipefail
HIER="$(cd "$(dirname "$0")" && pwd)"
SPIEGEL="$HIER/cloud_spiegel.sh"
UIDN="$(id -u)"
ALT="com.maick.signalhub"
NEU="com.maick.cloudspiegel"
PLIST="$HOME/Library/LaunchAgents/$NEU.plist"

echo "1/4 Pruefe, ob der Worker-Cron schon erfolgreich einen Signal-Hub-Lauf ausgeloest hat ..."
N="$(gh run list --repo Miksch52/signal-hub --workflow pipeline.yml --event workflow_dispatch --limit 40 \
      --json displayTitle,conclusion --jq '[.[] | select((.displayTitle | contains("Worker-Cron")) and .conclusion == "success")] | length' 2>/dev/null || echo 0)"
if [ "${N:-0}" -lt 1 ] 2>/dev/null; then
  echo "ABBRUCH: noch kein erfolgreicher Worker-Cron-Lauf. Erst GH_DISPATCH_TOKEN als Worker-Secret setzen"
  echo "und den naechsten Slot abwarten (Stand: https://mts-cors.miksch267.workers.dev/cron-status)."
  echo "Der LaunchAgent bleibt aktiv - nichts wurde geaendert."
  exit 1
fi
echo "    ok - $N erfolgreiche(r) Worker-Cron-Lauf/Laeufe."

echo "2/4 Alten Zeitplan $ALT stoppen und dauerhaft deaktivieren ..."
launchctl bootout "gui/$UIDN/$ALT" 2>/dev/null || true
launchctl disable "gui/$UIDN/$ALT"

echo "3/4 Lese-Job $NEU einrichten (alle 30 Minuten, osascript-Wrapper wegen TCC) ..."
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$NEU</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/osascript</string>
    <string>-e</string>
    <string>do shell script "'$SPIEGEL' &gt;&gt; /tmp/cloudspiegel.log 2&gt;&amp;1"</string>
  </array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/cloudspiegel-launchagent.log</string>
</dict>
</plist>
PLISTEOF
plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$UIDN/$NEU" 2>/dev/null || true
launchctl bootstrap "gui/$UIDN" "$PLIST"

echo "4/4 Einmal sofort spiegeln ..."
"$SPIEGEL"

echo
echo "Fertig. Rueckgaengig machen:"
echo "  launchctl bootout gui/$UIDN/$NEU; rm \"$PLIST\""
echo "  launchctl enable gui/$UIDN/$ALT; launchctl bootstrap gui/$UIDN \"$HOME/Library/LaunchAgents/$ALT.plist\""
