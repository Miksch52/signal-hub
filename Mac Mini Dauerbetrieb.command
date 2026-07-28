#!/bin/zsh
# AUF DEM MAC MINI ausführen (Doppelklick).
# Macht den Mini zum immer erreichbaren Signal-Hub-Host fuers iPhone:
#  - System schläft nicht mehr (Display darf aus), per Netzwerk weckbar
#  - Push-Zeitplan + Dashboard-Server als Dauerdienste
#  - zeigt die Tailscale-/WLAN-Adresse fuers iPhone
HUB="/Users/maickschwillo/Library/Mobile Documents/com~apple~CloudDocs/Trading-System/Maick Trading System/Signal-Hub"
cd "$HUB" || exit 1

echo "=== 1) Energie: nie schlafen, per Netzwerk weckbar ==="
echo "(Passwort wird fuer die Energieeinstellungen abgefragt)"
sudo pmset -a sleep 0 disksleep 0 womp 1 powernap 1 displaysleep 15
echo "Aktuelle Einstellung:"; pmset -g | grep -E "sleep|womp|powernap" | sed 's/^/   /'

echo "\n=== 2) Hintergrunddienste (Push-Zeitplan + Dashboard-Server) ==="
LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
install_job () {
  cp "$HUB/$1.plist" "$LA/$1.plist"
  launchctl bootout "gui/$(id -u)" "$LA/$1.plist" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" "$LA/$1.plist" 2>/dev/null \
    || { launchctl unload "$LA/$1.plist" 2>/dev/null; launchctl load -w "$LA/$1.plist"; }
  echo "   ✅ $1"
}
install_job "com.maick.signalhub"
install_job "com.maick.signalhub.server"

echo "\n=== 3) Adresse fuers iPhone ==="
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo "   WLAN:       http://${IP:-<Mini-IP>}:8091/"
TS=""
for c in /usr/local/bin/tailscale /opt/homebrew/bin/tailscale "/Applications/Tailscale.app/Contents/MacOS/Tailscale"; do
  [ -x "$c" ] && TS="$c" && break
done
if [ -n "$TS" ]; then
  TSIP=$("$TS" ip -4 2>/dev/null | head -1)
  echo "   Unterwegs:  http://${TSIP}:8091/  (Tailscale)"
else
  echo "   Unterwegs:  Tailscale installieren (tailscale.com/download/mac) + iPhone-App, dann erreichbar."
fi
echo "\nFertig. Den Mac Mini ab jetzt NICHT herunterfahren – nur Bildschirm darf aus."
echo "(Fenster kann geschlossen werden)"
