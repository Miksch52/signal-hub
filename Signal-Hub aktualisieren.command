#!/bin/zsh
# Doppelklick: liest alle Quellen ein, bewertet, sendet Push, oeffnet Dashboard.
cd "/Users/maickschwillo/Library/Mobile Documents/com~apple~CloudDocs/Trading-System/Maick Trading System/Signal-Hub" || exit 1
/usr/local/bin/python3 src/run.py --notify
open signal-hub.html
echo ""
echo "Fertig. (Fenster kann geschlossen werden)"
