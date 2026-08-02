#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator fuer den Daily Signal Hub.

Ein Befehl liest alle aktiven Quellen ein (PDF, E-Mail, Finviz), bewertet und
schreibt das Dashboard-Datenfile. Optional Push aufs Handy.

  python3 src/run.py              # alle Quellen einlesen + bewerten (kein Push)
  python3 src/run.py --notify     # zusaetzlich Push senden
  python3 src/run.py --scheduled  # fuer launchd: pusht nur zu den config-Zeiten (morgens/abends)

Der --scheduled-Modus wird alle 15 Min von launchd aufgerufen, prueft die in
config.benachrichtigung.zeiten hinterlegten Uhrzeiten und loest pro Tag einmal je
Zeitfenster Lauf+Push aus (Uhrzeiten also editierbar ohne Systemeingriff).
"""

import json
import os
import subprocess
import sys
from datetime import datetime

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade
PROJEKT = pfade.PROJEKT
DATA = pfade.DATA
CONFIG = pfade.CONFIG
STATE = pfade.STATE
PY = sys.executable

def cfg():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)

def lauf(script, *args):
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    r = subprocess.run([PY, os.path.join(HIER, script), *args])
    if r.returncode != 0:
        print(f"  ! {script} endete mit Code {r.returncode}")
    return r.returncode == 0

def pipeline(c):
    q = c["quellen"]
    if q.get("pdf", {}).get("aktiv"):
        lauf("pdf_screener.py")
    if q.get("email", {}).get("aktiv"):
        lauf("mail_screener.py")
    fv = q.get("finviz", {})
    if fv.get("aktiv") and (fv.get("screener") or fv.get("screener_urls")):
        lauf("finviz_screener.py")
    # Markets 360 / Trend-Screener: eigene Cloud-Pipelines liefern die Rohdaten
    # nach _magazine/ (rclone, siehe signal-hub.yml); die Skripte selbst
    # ueberspringen sauber, wenn die Datei (noch) fehlt.
    lauf("markets360_screener.py")
    lauf("trendscreener_screener.py")
    scorer_ok = lauf("scorer.py")
    # Pivot-Armed-Linse setzt auf signals.json auf -> NACH dem Scorer, und nur
    # wenn der Scorer wirklich geschrieben hat (sonst nur ein zweiter, verwirrender
    # FileNotFoundError obendrauf - das eigentliche Problem steht schon oben).
    if c.get("pivot", {}).get("aktiv", True) and scorer_ok:
        lauf("pivot_screener.py")
        # Heutige ARMED/BREAKOUT ins Forward-Logbuch (unverzerrte Stichprobe,
        # reift ueber Kalenderzeit -> Basis fuer pivot_backtest.py --evaluate).
        lauf("pivot_backtest.py", "--log")
        # --evaluate laeuft NICHT hier (4x/Tag waere unnoetig haeufig), sondern
        # wöchentlich per com.maick.pivot-backtest.plist (So 08:15) - pusht per
        # ntfy automatisch, sobald eine Kohorte die Reife-Schwelle erreicht.
    if scorer_ok:
        # Trefferquoten des Momentum-Scores (Tier A/B, Forward-Test gegen das
        # Score-Logbuch). Teilt den Tages-Yahoo-Cache mit dem Scorer -> billig.
        lauf("score_backtest.py")
        # Pro-Faktor-Erfolgsanalyse (seit 2026-08-02): reift wie score_backtest.py
        # ueber Kalenderzeit, meldet bis dahin nur "noch nicht reif" und tut
        # sonst nichts - kein gesondertes Scheduling noetig.
        lauf("score_faktoren_backtest.py")
    return scorer_ok

# --- Zeitplan-Status ------------------------------------------------------
def state_load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def state_save(s):
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def faellige_slots(c, state):
    """Zeit-Slots, deren Uhrzeit heute erreicht und noch nicht gesendet wurde."""
    heute = datetime.now().strftime("%Y-%m-%d")
    erledigt = set(state.get("gesendet", {}).get(heute, []))
    jetzt = datetime.now()
    faellig = []
    for slot in c["benachrichtigung"].get("zeiten", []):
        try:
            hh, mm = map(int, slot.split(":"))
        except ValueError:
            continue
        slot_dt = jetzt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if jetzt >= slot_dt and slot not in erledigt:
            faellig.append(slot)
    return faellig

def markiere_gesendet(state, slots):
    heute = datetime.now().strftime("%Y-%m-%d")
    g = state.setdefault("gesendet", {})
    g.setdefault(heute, [])
    for s in slots:
        if s not in g[heute]:
            g[heute].append(s)
    for tag in sorted(g)[:-7]:   # nur letzte 7 Tage behalten
        del g[tag]
    state_save(state)

# --- main -----------------------------------------------------------------
def main():
    c = cfg()
    args = sys.argv[1:]

    if "--scheduled" in args:
        if not c["benachrichtigung"].get("aktiv"):
            return
        state = state_load()
        slots = faellige_slots(c, state)
        if not slots:
            return  # ausserhalb der Sendezeiten -> still beenden
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Faellige Slots {slots} -> Lauf + Push")
        ok = pipeline(c)
        lauf("notify.py")
        markiere_gesendet(state, slots)
        # Fehlschlag sichtbar machen (z.B. CI-Job als "failed" statt gruen) -
        # sonst schluckt lauf() den Fehler und ein kompletter Ausfall des
        # Scorers sieht von aussen wie ein normaler, erfolgreicher Lauf aus.
        sys.exit(0 if ok else 1)

    ok = pipeline(c)
    if "--notify" in args or "--push" in args:
        lauf("notify.py")
    print("\nFertig. signal-hub.html neu laden.")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
