#!/bin/bash
# Haelt die Minervini-Zitat-Karte (setup-detail.html, Abschnitt "Minervini-
# Analyse") lokal mit Marktkontext angereichert, auch ohne den abgeloesten
# com.maick.signalhub-Zeitplan.
#
# SICHERHEITSFIX 2026-09-17: laed die Datei NICHT MEHR nach R2 hoch. Der
# vorherige dritte Schritt lud Minervinis Original-Posts (Volltext) nach
# signalhub-magazine/_deploy/signal-hub/ - demselben Pfad, den deploy.yml
# fuer die OEFFENTLICHE mts-hub.pages.dev-Seite abholt. Kombiniert mit der
# damaligen Whitelist-Zeile in cloudflare-pages/deploy.command (ebenfalls
# entfernt) waere das Lexikon dadurch alle 30 Minuten neu oeffentlich
# veroeffentlicht worden - bewusst NIE gewollt (siehe
# Konzept-Minervini-Lexikon.md: nur privater Gist-Sync im Coach).
# setup-detail.html liest die Zitat-Karte inzwischen client-seitig aus
# genau diesem privaten Gist (ladeLexikonAusGist()) statt aus einer Datei -
# ein Upload hierher ist fuer die Karte nicht mehr noetig. Dieses Skript
# pflegt nur noch den lokalen Marktkontext-Backfill (siehe --backfill), fuer
# den Fall, dass niemand mehr run.py::pipeline() lokal laufen laesst.
#
# Ablauf: (1) regime_logbuch.json frisch aus R2 ziehen (Voraussetzung fuer
# einen korrekten Marktkontext, siehe minervini_lexikon.py::_markt_kontext_fuer),
# (2) --backfill nachtragen. Existiert die Lexikon-Datei auf diesem Host
# (noch) nicht, ueberspringt der Schritt sauber - kein Fehler, kein
# Fortschritt auf diesem Host (siehe Modul-Docstring).
set -u
HIER="$(cd "$(dirname "$0")" && pwd)"                 # .../Signal-Hub/scripts
SIGNALHUB="$(cd "$HIER/.." && pwd)"                    # .../Signal-Hub

# launchd/osascript liefern eine minimale Umgebung ohne /usr/local/bin bzw.
# /opt/homebrew/bin - npx selbst wird zwar per absolutem Pfad in den
# aufgerufenen Skripten gefunden, aber dessen Shebang "#!/usr/bin/env node"
# scheitert dann mit "env: node: No such file or directory" (derselbe Fix
# wie in upload_magazine_to_r2.py::_mit_node_path, dort 2026-08-21 erstmals
# beobachtet und behoben).
export PATH="/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

"$HIER/sync_logbuch_pull.sh"
/usr/bin/python3 "$SIGNALHUB/src/minervini_lexikon.py" --backfill
