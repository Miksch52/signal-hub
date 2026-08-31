#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minervini-Lexikon: manuell erfasste X-Posts von Mark Minervini, verknuepft
mit dem Marktkontext des Tages (Ampel/Index-Stand aus dem Regime-Logbuch).

Phase 0 (seit 2026-08-31, siehe Konzept-Minervini-Lexikon.md): Erfassung ist
bewusst manuell (Copy/Paste im Claude-Code-Chat + Screenshot-Ablage in
minervini-lexikon-eingang/) statt automatisiertem X-Scraping - automatisiertes
Abrufen ohne die offizielle, kostenpflichtige X-API verstoesst gegen X's
Nutzungsbedingungen. Dieses Modul ist der Verarbeitungsschritt danach: nimmt
neu erfasste Eintraege entgegen, ordnet Bilddateien aus dem Eingang-Ordner
zu, haengt bei Marktkommentaren automatisch den Marktkontext des Tages an
(Ampel, Index-Stand) und schreibt die Sammel-Datei.

Marktkontext wird NUR bei exakter Datumsuebereinstimmung gesetzt (siehe
_markt_kontext_fuer) - kein Naeherungswert ueber mehrere Tage hinweg, analog
der Datendisziplin in pivot.py/regime_backtest.py. Fehlt der Tag im Logbuch,
bleibt der Eintrag ehrlich ohne Marktkontext statt eines erfundenen.

Geraeteunabhaengigkeit (seit 2026-08-31, Bugfix): --backfill haengt in
run.py::pipeline() NACH sync_logbuch_pull() und regime_backtest.py --evaluate
ein (siehe run.py) - genau wie beim frueheren pivot_backtest-Mac-mini-Bug
(siehe dortiger Kommentar) waere ein direkter, isolierter Aufruf dieses
Skripts ausserhalb der Pipeline auf JEDER Maschine "zufaellig richtig oder
falsch" util je nachdem, ob REGIME_LOGBUCH/signals.json gerade frisch sind -
das ist keine Eigenschaft des Geraets, sondern des Aufrufwegs. Innerhalb der
Pipeline ist REGIME_LOGBUCH immer frisch aus R2 gezogen (lokal wie Cloud,
sync_logbuch_pull.sh/pipeline.yml), backfill() funktioniert also gleich
zuverlaessig auf Mac mini, MacBook und GitHub-Actions-Runner.

Bekannte Restluecke: die Lexikon-JSON selbst (data/minervini-lexikon/) liegt
in iCloud (DATA), nicht in R2 - ein Cloud-Runner hat sie nicht im frischen
Checkout und ueberspringt --backfill dann sauber (kein Fehler, aber auch
kein Fortschritt auf diesem Host). Neue Eintraege entstehen ohnehin nur
manuell auf einem der beiden Macs (siehe Modul-Docstring oben) - die
Cloud-Seite wird erst relevant, wenn die Lexikon-Daten wie geplant zusaetzlich
per Gist synchronisiert werden (Coach-Anbindung, naechste Phase). Bis dahin
backfillt der Mac mini bei jedem seiner taeglichen Laeufe zuverlaessig nach.

Kein Netzabruf (wie ohlc_history.py) - reine Verarbeitung vorhandener Dateien.

Aufruf:
  python3 src/minervini_lexikon.py --eintraege pfad/zu/neue_eintraege.json
    Erwartet eine JSON-Liste neuer/aktualisierter Eintraege (Schema siehe
    Konzept-Dokument, Pflichtfelder: post_id, datum). Optionales Feld je
    Eintrag: "bild_aus_eingang": ["dateiname-im-eingang-ordner.jpeg", ...] -
    wird nach data/minervini-lexikon/bilder/ verschoben und ersetzt "grafik".
  python3 src/minervini_lexikon.py --backfill
    Holt bei bestehenden Eintraegen ohne (oder mit als nicht verfuegbar
    markiertem) Marktkontext erneut Marktkontext nach - fuer run.py::pipeline.
"""

import argparse
import json
import os
import shutil
from datetime import datetime

import pfade

AMPELN = ("gruen", "gelb", "rot")


def _lade_lexikon():
    if os.path.exists(pfade.MINERVINI_LEXIKON_JSON):
        try:
            with open(pfade.MINERVINI_LEXIKON_JSON, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Minervini-Lexikon: bestehende Datei nicht lesbar ({e}) - starte neu.")
    return {"eintraege": []}


def _speichere_lexikon(daten):
    daten["eintraege"].sort(
        key=lambda e: (e.get("datum") or "", e.get("zeit") or ""), reverse=True)
    daten["aktualisiert"] = datetime.now().astimezone().isoformat(timespec="seconds")
    os.makedirs(pfade.MINERVINI_LEXIKON_DIR, exist_ok=True)
    pfade.schreibe_json_atomar(pfade.MINERVINI_LEXIKON_JSON, daten, ensure_ascii=False, indent=1)

    def _js(fp):
        fp.write("window.MINERVINI_LEXIKON_DATA = ")
        json.dump(daten, fp, ensure_ascii=False)
        fp.write(";")

    pfade.schreibe_atomar(pfade.MINERVINI_LEXIKON_JS, _js)


def _markt_kontext_fuer(datum, markt="USA"):
    """Best-effort Marktkontext fuer ein Datum, nur bei exaktem Tagestreffer:
    1) REGIME_LOGBUCH (lokal, reicht am weitesten zurueck, aber nur auf der
       Maschine vorhanden, die regime_backtest.py --log laufen laesst).
    2) signals.json::marktregime, nur wenn deren 'erstellt'-Datum exakt auf
       diesen Tag faellt (kein historischer Speicher, nur der letzte Lauf).
    Sonst None."""
    if os.path.exists(pfade.REGIME_LOGBUCH):
        try:
            lb = json.load(open(pfade.REGIME_LOGBUCH, encoding="utf-8"))
            treffer = [e for e in lb if e.get("datum") == datum and e.get("markt") == markt]
            if treffer:
                e = treffer[-1]
                return {
                    "quelle": "regime_logbuch", "markt": markt,
                    "ampel": e.get("ampel"), "index_symbol": e.get("index_symbol"),
                    "index_kurs": e.get("index_kurs"),
                }
        except Exception:
            pass
    if os.path.exists(pfade.SIGNALS_JSON):
        try:
            signals = json.load(open(pfade.SIGNALS_JSON, encoding="utf-8"))
            erstellt = (signals.get("erstellt") or "")[:10]
            if erstellt == datum:
                r = (signals.get("marktregime") or {}).get(markt) or {}
                if r.get("ampel") in AMPELN:
                    ft = r.get("follow_through") or {}
                    return {
                        "quelle": "signals_json_aktueller_lauf", "markt": markt,
                        "ampel": r.get("ampel"),
                        "distribution_days": r.get("distribution_days"),
                        "pullback_vom_zwischenhoch_pct": ft.get("pullback_pct"),
                        "follow_through_day_bestaetigt": ft.get("state") == 2,
                    }
        except Exception:
            pass
    return None


def _verschiebe_bilder(eintrag):
    dateien = eintrag.pop("bild_aus_eingang", None)
    if not dateien:
        return
    if isinstance(dateien, str):
        dateien = [dateien]
    os.makedirs(pfade.MINERVINI_LEXIKON_BILDER, exist_ok=True)
    neue_namen = []
    for name in dateien:
        quelle = os.path.join(pfade.MINERVINI_LEXIKON_EINGANG, name)
        if not os.path.exists(quelle):
            print(f"  ! Bild nicht im Eingang gefunden, uebersprungen: {name}")
            continue
        ziel_name = f"{eintrag['datum']}_{eintrag['post_id']}_{os.path.basename(name)}"
        ziel = os.path.join(pfade.MINERVINI_LEXIKON_BILDER, ziel_name)
        shutil.move(quelle, ziel)
        neue_namen.append(ziel_name)
        print(f"  Bild verschoben: {name} -> minervini-lexikon/bilder/{ziel_name}")
    if neue_namen:
        eintrag["grafik"] = neue_namen if len(neue_namen) > 1 else neue_namen[0]


def fuege_eintraege_hinzu(neue_eintraege):
    daten = _lade_lexikon()
    bestehend = {e["post_id"]: i for i, e in enumerate(daten["eintraege"]) if e.get("post_id")}
    for eintrag in neue_eintraege:
        if not eintrag.get("post_id") or not eintrag.get("datum"):
            print(f"  ! Eintrag ohne post_id/datum uebersprungen: {eintrag.get('text_de', '')[:40]!r}")
            continue
        _verschiebe_bilder(eintrag)
        if eintrag.get("typ") == "marktkommentar" and not eintrag.get("markt_kontext"):
            kontext = _markt_kontext_fuer(eintrag["datum"])
            eintrag["markt_kontext"] = kontext or {
                "hinweis": "kein Marktkontext fuer dieses Datum verfuegbar "
                           "(Regime-Logbuch/aktueller Lauf deckt den Tag nicht ab)"
            }
        if eintrag["post_id"] in bestehend:
            daten["eintraege"][bestehend[eintrag["post_id"]]] = eintrag
            print(f"  Aktualisiert: {eintrag['post_id']}")
        else:
            daten["eintraege"].append(eintrag)
            bestehend[eintrag["post_id"]] = len(daten["eintraege"]) - 1
            print(f"  Neu: {eintrag['post_id']}")
    _speichere_lexikon(daten)
    print(f"Minervini-Lexikon: {len(daten['eintraege'])} Eintraege gesamt -> {pfade.MINERVINI_LEXIKON_JSON}")


def backfill():
    """Fuer run.py::pipeline (siehe Modul-Docstring): holt bei bestehenden
    Marktkommentar-Eintragen ohne Marktkontext erneut nach. Ueberspringt sauber,
    wenn es die Lexikon-Datei auf diesem Host (noch) nicht gibt."""
    if not os.path.exists(pfade.MINERVINI_LEXIKON_JSON):
        print("Minervini-Lexikon: keine Datei auf diesem Host - Backfill uebersprungen.")
        return
    daten = _lade_lexikon()
    aktualisiert = 0
    for eintrag in daten["eintraege"]:
        if eintrag.get("typ") != "marktkommentar":
            continue
        kontext = eintrag.get("markt_kontext")
        hat_echten_kontext = kontext and "hinweis" not in kontext
        if hat_echten_kontext:
            continue
        neuer_kontext = _markt_kontext_fuer(eintrag["datum"])
        if neuer_kontext:
            eintrag["markt_kontext"] = neuer_kontext
            aktualisiert += 1
            print(f"  Marktkontext nachgetragen: {eintrag['post_id']}")
    if aktualisiert:
        _speichere_lexikon(daten)
    print(f"Minervini-Lexikon Backfill: {aktualisiert} Eintraege ergaenzt "
          f"(von {len(daten['eintraege'])} gesamt).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    gruppe = ap.add_mutually_exclusive_group(required=True)
    gruppe.add_argument("--eintraege",
                         help="Pfad zu einer JSON-Datei mit einer Liste neuer/aktualisierter Eintraege")
    gruppe.add_argument("--backfill", action="store_true",
                         help="Marktkontext bei bestehenden Eintraegen nachtragen (siehe run.py::pipeline)")
    args = ap.parse_args()
    if args.backfill:
        backfill()
    else:
        with open(args.eintraege, encoding="utf-8") as f:
            neue = json.load(f)
        fuege_eintraege_hinzu(neue)
