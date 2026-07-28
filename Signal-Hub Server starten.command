#!/bin/zsh
# Doppelklick: startet den Dashboard-Server. iPhone/iPad oeffnen die angezeigte WLAN-URL.
cd "/Users/maickschwillo/Library/Mobile Documents/com~apple~CloudDocs/Trading-System/Maick Trading System/Signal-Hub" || exit 1
open "http://localhost:8091/"
/usr/bin/python3 src/server.py
