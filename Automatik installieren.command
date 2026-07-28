#!/bin/zsh
# Doppelklick: richtet die Hintergrunddienste ein:
#  1) Push-Zeitplan (alle 15 Min, pusht zu den config-Zeiten morgens/abends)
#  2) Dashboard-Server (Dauerbetrieb, fuer iPhone/iPad-Zugriff)
#  3) Pivot-Evaluations-Loop (So 08:15: bewertet gereifte Forward-Picks gegen
#     aktuelle Kurse, pusht sobald der Forward-Test reif ist ~6-8 Wochen)
# Am besten auf dem Mac Mini ausfuehren (Dauerlaeufer).
HUB="/Users/maickschwillo/Library/Mobile Documents/com~apple~CloudDocs/Trading-System/Maick Trading System/Signal-Hub"
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA"

install_job () {
    local name="$1"
    cp "$HUB/$name.plist" "$LA/$name.plist"
    launchctl bootout "gui/$(id -u)" "$LA/$name.plist" 2>/dev/null
    if launchctl bootstrap "gui/$(id -u)" "$LA/$name.plist" 2>/dev/null; then
        echo "  ✅ $name aktiv (bootstrap)"
    else
        launchctl unload "$LA/$name.plist" 2>/dev/null
        launchctl load -w "$LA/$name.plist" && echo "  ✅ $name aktiv (load)"
    fi
}

echo "Richte Hintergrunddienste ein ..."
install_job "com.maick.signalhub"           # Push-Zeitplan
install_job "com.maick.signalhub.server"    # Dashboard-Server
install_job "com.maick.pivot-backtest"      # Pivot-Evaluations-Loop (woechentlich)

echo ""
echo "Status:"
launchctl list | grep -E "signalhub|pivot" || echo "  (noch nicht gelistet)"

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo ""
echo "📱 Dashboard im WLAN:  http://${IP:-<diese-Mac-IP>}:8091/"
echo "🌍 Von unterwegs:      Tailscale installieren -> http://<mac-name>:8091/"
echo ""
echo "Deaktivieren: launchctl bootout gui/\$(id -u) \"$LA/com.maick.signalhub.server.plist\""
echo "Fertig. (Fenster kann geschlossen werden)"
