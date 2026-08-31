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

def sync_logbuch_lokal(script):
    # Nur lokal (Mac mini/MacBook) - der Cloud-Lauf macht denselben
    # Pull/Push-Schritt per rclone direkt in .github/workflows/pipeline.yml
    # (kein wrangler-Login auf dem Runner). Fix 2026-08-16: logbuch.json/
    # pivot_logbuch.json/pivot_eval_state.json wuchsen bisher NUR auf der
    # Maschine, die sie zuletzt geschrieben hat (siehe pfade.py::LOKAL-
    # Docstring) - ein Cloud-Runner ist pro Lauf frisch und verwarf seine
    # taeglichen Eintraege wieder, siehe die Erklaerung weiter unten bei
    # score_backtest.py/score_faktoren_backtest.py. R2 (_state/) ist jetzt
    # die gemeinsame Quelle, die beide Seiten vor dem Lauf ziehen und danach
    # zurueckschreiben.
    if os.environ.get("GITHUB_ACTIONS"):
        return
    subprocess.run([os.path.join(PROJEKT, "scripts", script)])

def pipeline(c, push=False):
    sync_logbuch_lokal("sync_logbuch_pull.sh")
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
    # Plausibilitaetswaechter (seit 2026-08-23, Systempruefung): keins der fuenf
    # obigen Skripte wirft einen Fehler, wenn seine Quelle leer laeuft (IMAP-
    # Passwort abgelaufen, R2-Sync kaputt, Finviz-Screener liefert nichts) -
    # jedes schreibt einfach eine leere Liste und beendet sich mit Code 0. Der
    # Waechter vergleicht die heutige Trefferzahl je Quelle gegen deren eigene
    # Historie und pusht, wenn eine bisher verlaessliche Quelle ploetzlich auf
    # 0 faellt (siehe quellen_watchdog.py-Docstring). Laeuft VOR scorer.py,
    # unabhaengig von dessen Erfolg - absichtlich nicht in scorer_ok verrechnet,
    # der Push ist der eigentliche Meldeweg (wie beim Deploy-Token-Waechter).
    lauf("quellen_watchdog.py")
    scorer_ok = lauf("scorer.py")
    # Taeglicher OHLC-Snapshot (seit 2026-08-20, Top-Setups-Roadmap Punkt 4):
    # einzige tatsaechlich dauerhafte Kurshistorie im System, siehe
    # ohlc_history.py-Docstring. Nur bei frischem Scorer-Lauf sinnvoll (sonst
    # kein neues signals.json zum Ausziehen).
    if scorer_ok:
        lauf("ohlc_history.py")
        if not os.environ.get("GITHUB_ACTIONS"):
            subprocess.run([os.path.join(PROJEKT, "scripts", "upload_ohlc_to_r2.sh")])
    # Pivot-Armed-Linse setzt auf signals.json auf -> NACH dem Scorer, und nur
    # wenn der Scorer wirklich geschrieben hat (sonst nur ein zweiter, verwirrender
    # FileNotFoundError obendrauf - das eigentliche Problem steht schon oben).
    if c.get("pivot", {}).get("aktiv", True) and scorer_ok:
        # --notify nur, wenn dieser Lauf ohnehin schon einen Push ausloest
        # (faelliger Slot bzw. --notify/--push) - Anti-Spam-Zustand
        # (pivot_state.json, siehe pivot_screener.py::neuzugaenge) wird
        # unabhaengig davon IMMER aktualisiert, nur der tatsaechliche Versand
        # haengt an derselben Bedingung wie der normale Signal-Push.
        lauf("pivot_screener.py", *(["--notify"] if push else []))
        # Heutige ARMED/BREAKOUT ins Forward-Logbuch (unverzerrte Stichprobe,
        # reift ueber Kalenderzeit -> Basis fuer pivot_backtest.py --evaluate).
        lauf("pivot_backtest.py", "--log")
        # --evaluate laeuft seit 2026-08-23 bei JEDEM Lauf mit (vorher nur
        # woechentlich per com.maick.pivot-backtest.plist, So 08:15).
        #
        # Grund: die alte Regelung machte den Forward-Test faktisch
        # Mac-mini-abhaengig und damit im Cloud-Betrieb wirkungslos. Die
        # Cloud-Pipeline rief nur --log auf, also existierte
        # data/pivot_backtest.json auf dem Runner nie - top_setups.py fand
        # nichts, lieferte fuer JEDEN Eintrag backtest=None, und die
        # Kern-Setup-Auszeichnung auf der Startseite konnte in der Cloud nie
        # greifen. Sichtbar wurde das erst, als die Startseite die gemessene
        # Zahl statt nur eines Sterns anzeigt ("kein Beleg" ueberall).
        # Nebenwirkung derselben Ursache: die live ausgelieferte
        # pivot_backtest.json war am 2026-08-23 vom 09.08. - der woechentliche
        # Upload laeuft nur bei eingeschaltetem Mac mini. Verstoesst gegen
        # "Geraeteunabhaengigkeit hat Vorrang" (CLAUDE.md).
        #
        # Kosten: vernachlaessigbar. evaluate() zieht KEINE zusaetzlichen
        # Yahoo-Daten, sondern liest den Tages-Cache, den scorer.py im selben
        # Lauf ohnehin schon gefuellt hat (scorer.hole_chart_cached, siehe
        # Modul-Docstring in pivot_backtest.py).
        #
        # Kein Push-Spam trotz 4x/Tag: push_reife() hat eigenen Anti-Spam ueber
        # pivot_eval_state.json (nur beim ERSTEN Erreichen der Reife-Schwelle
        # bzw. wenn n um ~50% waechst), und dieser Zustand wird in der Pipeline
        # aus R2 wiederhergestellt und danach zurueckgesichert - der Runner
        # startet also nicht bei null.
        #
        # Der RETRO-Lauf (Default ohne Argument) bleibt bewusst woechentlich
        # und lokal: teuer (Walk-Forward ueber ~560 Charts) und ohnehin durch
        # die Universums-Vorauswahl verzerrt (siehe bias_hinweis). --evaluate
        # ueberschreibt dessen Block nicht, sondern ergaenzt nur die
        # forward_*-Felder in der bestehenden Datei.
        lauf("pivot_backtest.py", "--evaluate")
    if scorer_ok:
        # Trefferquoten des Momentum-Scores (Tier A/B, Forward-Test gegen das
        # Score-Logbuch). Teilt den Tages-Yahoo-Cache mit dem Scorer -> billig.
        lauf("score_backtest.py")
        # Pro-Faktor-Erfolgsanalyse (seit 2026-08-02): reift wie score_backtest.py
        # ueber Kalenderzeit, meldet bis dahin nur "noch nicht reif" und tut
        # sonst nichts - kein gesondertes Scheduling noetig.
        lauf("score_faktoren_backtest.py")
        # Markt-Regime-Forward-Test (seit 2026-08-21): pro Lauf nur zwei neue
        # Eintraege (ein Regime-Wert je Markt) -> billig genug, um wie
        # hebel_backtest.py bei jedem Lauf komplett durchzulaufen (--evaluate
        # ruft log_und_evaluate() auf, kein separates Scheduling noetig).
        lauf("regime_backtest.py", "--evaluate")
        # Minervini-Lexikon-Backfill (seit 2026-08-31): traegt bei bestehenden
        # Marktkommentar-Eintraegen fehlenden Marktkontext nach. Bewusst HIER
        # (nach sync_logbuch_lokal() oben, nach regime_backtest.py --evaluate)
        # - REGIME_LOGBUCH ist an dieser Stelle garantiert frisch aus R2
        # gezogen, egal ob der Lauf auf dem Mac mini, dem MacBook oder einem
        # GitHub-Actions-Runner passiert (sonst waere das Ergebnis vom Zufall
        # abhaengig, welche Maschine zuletzt lokal geschrieben hat - siehe
        # minervini_lexikon.py-Docstring, gleicher Bug wie beim
        # pivot_backtest-Mac-mini-Fix weiter oben in dieser Datei).
        lauf("minervini_lexikon.py", "--backfill")
        # Beide lesen LOGBUCH (pfade.LOKAL). Bis 2026-08-16 wuchs das nur auf
        # der Maschine, die zuletzt schrieb - der Cloud-Lauf startete jeden Job
        # mit leerem LOGBUCH (siehe deren main(): "if not lb: return", schrieb
        # also nie eine Datei). Seit dem Fix (sync_logbuch_lokal() oben, R2
        # _state/ per pipeline.yml im Cloud-Lauf) ist LOGBUCH auch dort
        # gefuellt -> score_backtest.json/score_faktoren_backtest.json
        # entstehen jetzt auch im Cloud-Lauf und laufen ganz normal ueber den
        # signal-hub-data-Artifact in den deploy-Job. Der lokale Direkt-Upload
        # hier bleibt trotzdem (schneller Stand ohne auf den naechsten
        # Cloud-Zyklus zu warten, Bugfix 2026-08-09).
        if not os.environ.get("GITHUB_ACTIONS"):
            # pivot_backtest.* seit 2026-08-23 mit dabei: --evaluate laeuft
            # jetzt bei jedem Lauf (siehe oben), also darf auch der lokale
            # Stand sofort nach R2 - sonst haette ein Mac-mini-Lauf frischere
            # Forward-Zahlen als die Live-Seite, bis der naechste Cloud-Zyklus
            # sie ueberschreibt. Der woechentliche
            # scripts/weekly_backtest_upload.sh laedt dieselben zwei Dateien
            # weiterhin hoch (dort nach dem teuren RETRO-Lauf) - doppelt
            # hochladen schadet nicht, upload_to_r2.sh ist idempotent.
            subprocess.run([os.path.join(PROJEKT, "scripts", "upload_to_r2.sh"),
                            "score_backtest.json", "score_faktoren_backtest.json",
                            "regime_backtest.json", "regime_backtest.js",
                            "pivot_backtest.json", "pivot_backtest.js"])
    sync_logbuch_lokal("sync_logbuch_push.sh")
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
        ok = pipeline(c, push=True)
        # Erster konfigurierter Slot des Tages (zeiten[0], typischerweise
        # 07:30) wird als Morning Brief gepusht (Marktampel-Status vorweg,
        # siehe notify.py::baue_nachricht) statt der normalen Top-Liste.
        morgens_slot = (c["benachrichtigung"].get("zeiten") or [None])[0]
        lauf("notify.py", "--morgens") if morgens_slot in slots else lauf("notify.py")
        markiere_gesendet(state, slots)
        # Fehlschlag sichtbar machen (z.B. CI-Job als "failed" statt gruen) -
        # sonst schluckt lauf() den Fehler und ein kompletter Ausfall des
        # Scorers sieht von aussen wie ein normaler, erfolgreicher Lauf aus.
        sys.exit(0 if ok else 1)

    push = "--notify" in args or "--push" in args
    ok = pipeline(c, push=push)
    if push:
        lauf("notify.py")
    print("\nFertig. signal-hub.html neu laden.")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
