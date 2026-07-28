#!/bin/zsh
# Zeigt die Adresse, unter der das Dashboard von unterwegs (iPhone) erreichbar ist.
TS=""
for c in /usr/local/bin/tailscale /opt/homebrew/bin/tailscale "/Applications/Tailscale.app/Contents/MacOS/Tailscale"; do
  [ -x "$c" ] && TS="$c" && break
done

if [ -z "$TS" ]; then
  echo "Tailscale ist noch nicht installiert."
  echo ""
  echo "So richtest du Zugriff von unterwegs ein (kostenlos):"
  echo "  1) Auf dem Mac Mini: https://tailscale.com/download/mac laden, anmelden."
  echo "  2) Auf dem iPhone:   App 'Tailscale' aus dem App Store, GLEICHES Konto."
  echo "  3) Dieses Skript erneut starten -> zeigt die iPhone-URL."
  echo ""
  read "?Enter zum Schließen"
  exit 0
fi

IP=$("$TS" ip -4 2>/dev/null | head -1)
NAME=$("$TS" status --json 2>/dev/null | /usr/local/bin/python3 -c "import sys,json;print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null)
echo "✅ Tailscale aktiv."
[ -n "$IP" ]   && echo "📱 Dashboard von überall:  http://$IP:8091/"
[ -n "$NAME" ] && echo "   oder per Name:          http://$NAME:8091/"
echo ""
echo "iPhone: Tailscale-App einschalten, dann obige URL in Safari öffnen"
echo "(Safari -> Teilen -> 'Zum Home-Bildschirm' für ein App-Icon)."
read "?Enter zum Schließen"
