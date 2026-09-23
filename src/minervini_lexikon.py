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
manuell auf einem der beiden Macs (siehe Modul-Docstring oben), und der Mac
mini backfillt bei jedem seiner taeglichen Laeufe zuverlaessig nach. Die
beiden Wege AUS dieser Datei heraus existieren inzwischen beide (Gist fuer
den Coach, R2 fuer den Pages-Deploy - siehe naechster Absatz); offen bleibt
nur der Weg HINEIN auf einem Cloud-Runner, den es mangels Datei dort nicht
gibt und mangels automatischer Erfassung auch nicht braucht.

ANZEIGE-Pfad in die Cloud (seit 2026-09-07): setup-detail.html zeigt den
neuesten Marktkommentar unter der Minervini-Analyse an und laedt dafuer
data/minervini-lexikon/minervini_lexikon.json. Damit das auch auf
mts-hub.pages.dev funktioniert, steht die Datei jetzt in der Deploy-Whitelist
(cloudflare-pages/deploy.command) und wird AUTOMATISCH nach R2 geschoben:
run.py haengt sie an den lokalen upload_to_r2.sh-Aufruf direkt nach dem
--backfill oben an. Der Pipeline-Upload im Cloud-Lauf kann das nicht (der
Runner hat die Datei gar nicht), der Mac-mini-Lauf dagegen hat sie immer -
also passiert es dort, ohne einen Extra-Schritt nach jeder Pflege, den man
verlaesslich vergessen wuerde. Bei Bedarf von Hand:

  Signal-Hub/scripts/upload_to_r2.sh minervini-lexikon/minervini_lexikon.json

Laeuft der Mac mini laengere Zeit nicht, friert nur die Zitat-Karte in der
Cloud auf dem letzten hochgeladenen Stand ein (kein Fehler, keine Auswirkung
auf den Regelabgleich darueber). Der --backfill-Pfad oben ist unberuehrt.

NICHT ZU VERWECHSELN mit dem Coach-Knopf "Lokalen Stand hochladen"
(minervini-coach-2.html::lexikonUploadLocal): der liest dieselbe Datei, legt
sie aber in den privaten GIST-Sync, damit der Coach das Lexikon auf jedem
Geraet zeigen kann. Zwei Ziele, zwei Zwecke - Gist fuer den Coach, R2 fuer
den Pages-Deploy der Setup-Analyse. Keiner ersetzt den anderen; der Knopf
bleibt weiterhin der Weg, den Coach-Stand sofort zu aktualisieren, ohne auf
den naechsten Mac-mini-Lauf zu warten.

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
  python3 src/minervini_lexikon.py --gist [--eintraege datei.json]
    Abgleich mit dem privaten Coach-Gist in BEIDE Richtungen (seit 2026-09-23,
    siehe "Gist als massgebliche Quelle" unten). Mit --eintraege: erst
    abgleichen (Loeschungen/Neuaufnahmen von der Seite holen), dann
    hinzufuegen, dann wieder hochladen. Braucht `gh auth login` mit gist-Scope.

GIST ALS MASSGEBLICHE QUELLE (seit 2026-09-23): Im Coach lassen sich Posts
direkt hinzufuegen und Posts/Videos loeschen. Diese Datei hier ist deshalb
nicht mehr "der" Stand, sondern eine von drei Kopien (Datei, Gist, Browser),
die per merge_lexikon() ZUSAMMENGEFUEHRT werden - nie ueberschrieben.
Loeschungen stehen als Grabsteine in "geloescht" und werden nie entfernt,
damit ein spaeterer Import einen geloeschten Post nicht zurueckholt. Die
gleichen Regeln stehen in minervini-coach-2.html::_lexikonMerge - bei
Aenderungen beide anfassen. Anlass: am 2026-09-22 hing der Coach fuenf Tage
auf einem alten Gist-Stand, weil der Upload nur ueber einen lokal geoeffneten
Coach lief.
"""

import argparse
import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone

import pfade

AMPELN = ("gruen", "gelb", "rot")
GIST_DATEI = "minervini-fortschritt.json"
GIST_EINGANG = "minervini-lexikon-eingang.json"


def jetzt_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _zeit(t):
    try:
        return datetime.fromisoformat((t or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def merge_lexikon(a, b):
    """Vereinigt zwei Lexikon-Staende (Regeln identisch zu
    minervini-coach-2.html::_lexikonMerge): je post_id gewinnt das spaetere
    "geaendert", bei Gleichstand b; Grabsteine ("geloescht") werden vereinigt
    und entfernen den Eintrag aus beiden."""
    a, b = a or {"eintraege": []}, b or {"eintraege": []}
    grab = {}
    for g in (a.get("geloescht") or []) + (b.get("geloescht") or []):
        if g and g.get("post_id") and g["post_id"] not in grab:
            grab[g["post_id"]] = g
    eintraege = {}
    for e in a.get("eintraege") or []:
        if e and e.get("post_id"):
            eintraege[e["post_id"]] = e
    for e in b.get("eintraege") or []:
        if not e or not e.get("post_id"):
            continue
        alt = eintraege.get(e["post_id"])
        if alt is None or _zeit(e.get("geaendert")) >= _zeit(alt.get("geaendert")):
            eintraege[e["post_id"]] = e
    out = {**a, **b}
    out["eintraege"] = [e for pid, e in eintraege.items() if pid not in grab]
    out["geloescht"] = list(grab.values())
    akt = max((x for x in (a.get("aktualisiert"), b.get("aktualisiert")) if x), key=_zeit, default=None)
    if akt:
        out["aktualisiert"] = akt
    return out


def _ohne_komma_null(o):
    """9.0 -> 9: JavaScript (Coach) schreibt ganzzahlige Kommazahlen ohne ".0",
    Python mit - ohne diese Angleichung saehe jeder Abgleich eine Aenderung."""
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {k: _ohne_komma_null(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_ohne_komma_null(v) for v in o]
    return o


def _fingerprint(daten):
    daten = _ohne_komma_null(daten)
    return json.dumps([sorted(daten.get("eintraege") or [], key=lambda e: e.get("post_id", "")),
                       sorted(g.get("post_id", "") for g in daten.get("geloescht") or [])],
                      sort_keys=True, ensure_ascii=False)


def gesperrte_video_ids(daten):
    """Video-IDs, die per Grabstein geloescht sind (auch bei yt-t-<hash>-Kennungen)."""
    ids = set()
    for g in daten.get("geloescht") or []:
        if g.get("video_id"):
            ids.add(g["video_id"])
        pid = g.get("post_id") or ""
        if pid.startswith("yt-") and not pid.startswith("yt-t-"):
            ids.add(pid[3:])
    return ids


# ---- Gist-Zugriff ueber die GitHub-CLI (Token bleibt im Schluesselbund) ----
def _gh_json(*args, eingabe=None):
    r = subprocess.run(["gh", "api", *args], input=eingabe, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} fehlgeschlagen: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def gist_finden():
    """ID des Coach-Gists. Mehrere Treffer sind ein Fehler (Coach und Setup-
    Analyse nehmen den ersten - bei zwei Kandidaten koennte das der falsche
    sein, siehe Aufraeumen 2026-09-22)."""
    treffer = [g["id"] for g in _gh_json("/gists?per_page=100") or [] if GIST_DATEI in (g.get("files") or {})]
    if len(treffer) > 1:
        raise RuntimeError(f"{len(treffer)} Gists mit {GIST_DATEI} gefunden ({', '.join(treffer)}) - bitte erst aufraeumen.")
    return treffer[0] if treffer else None


def gist_datei_lesen(gist_id, name):
    f = ((_gh_json(f"/gists/{gist_id}") or {}).get("files") or {}).get(name)
    if not f:
        return None
    if f.get("truncated"):
        with urllib.request.urlopen(f["raw_url"], timeout=60) as r:
            return r.read().decode("utf-8")
    return f.get("content")


def gist_datei_schreiben(gist_id, name, inhalt):
    _gh_json("--method", "PATCH", f"/gists/{gist_id}", "--input", "-",
             eingabe=json.dumps({"files": {name: {"content": inhalt}}}, ensure_ascii=False))


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
    grab = {g.get("post_id") for g in daten.get("geloescht") or []}
    gesperrt = gesperrte_video_ids(daten)
    for eintrag in neue_eintraege:
        if not eintrag.get("post_id") or not eintrag.get("datum"):
            print(f"  ! Eintrag ohne post_id/datum uebersprungen: {eintrag.get('text_de', '')[:40]!r}")
            continue
        if eintrag["post_id"] in grab or (eintrag.get("video_id") and eintrag["video_id"] in gesperrt):
            print(f"  ! {eintrag['post_id']} wurde im Coach geloescht - nicht wieder aufgenommen.")
            continue
        eintrag["geaendert"] = jetzt_utc()
        if eintrag.get("typ") == "video_lektion" and eintrag.get("transkript_hash"):
            dublette = next((e for e in daten["eintraege"]
                             if e.get("transkript_hash") == eintrag["transkript_hash"]
                             and e.get("post_id") != eintrag["post_id"]), None)
            if dublette:
                print(f"  ! Video-Dublette uebersprungen: {eintrag['post_id']} hat denselben "
                      f"Transkript-Hash wie {dublette['post_id']}")
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
    aktualisiert = _backfill_daten(daten)
    if aktualisiert:
        _speichere_lexikon(daten)
    print(f"Minervini-Lexikon Backfill: {aktualisiert} Eintraege ergaenzt "
          f"(von {len(daten['eintraege'])} gesamt).")


def _backfill_daten(daten):
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
            eintrag["geaendert"] = jetzt_utc()
            aktualisiert += 1
            print(f"  Marktkontext nachgetragen: {eintrag['post_id']}")
    return aktualisiert


def gist_abgleich():
    """Datei <-> Gist in beide Richtungen zusammenfuehren (siehe Docstring).
    Holt Neuaufnahmen/Loeschungen von der Seite, traegt fuer neue
    Marktkommentare den Marktkontext nach und laedt das Ergebnis hoch.
    Raeumt ausserdem vorgemerkte Videos aus der Warteschlange, zu denen es
    inzwischen Lektionen gibt (oder die geloescht wurden)."""
    gist_id = gist_finden()
    if not gist_id:
        print(f"Gist-Abgleich: kein Gist mit {GIST_DATEI} gefunden - uebersprungen.")
        return False
    gesamt = json.loads(gist_datei_lesen(gist_id, GIST_DATEI) or "{}")
    remote = gesamt.get("lexikon") or {"eintraege": []}
    lokal = _lade_lexikon()
    # echte Kopie: merge_lexikon teilt die Eintrags-Objekte mit remote/lokal,
    # der Backfill darunter wuerde sonst auch die Vergleichsbasis veraendern
    daten = json.loads(json.dumps(merge_lexikon(remote, lokal), ensure_ascii=False))
    nachgetragen = _backfill_daten(daten)
    if _fingerprint(daten) != _fingerprint(lokal):
        _speichere_lexikon(daten)
        print(f"  Datei aktualisiert ({len(lokal.get('eintraege') or [])} -> {len(daten['eintraege'])} Eintraege).")
    if _fingerprint(daten) != _fingerprint(remote):
        gesamt["lexikon"] = daten
        gesamt["savedAt"] = jetzt_utc()
        gist_datei_schreiben(gist_id, GIST_DATEI, json.dumps(gesamt, ensure_ascii=False, indent=2))
        print(f"  Gist aktualisiert ({len(remote.get('eintraege') or [])} -> {len(daten['eintraege'])} Eintraege).")

    roh = gist_datei_lesen(gist_id, GIST_EINGANG)
    if roh:
        eingang = json.loads(roh)
        fertig = {e.get("video_id") for e in daten["eintraege"] if e.get("typ") == "video_lektion"}
        weg = fertig | gesperrte_video_ids(daten)
        vorher = len(eingang.get("videos") or [])
        eingang["videos"] = [v for v in eingang.get("videos") or [] if v.get("video_id") not in weg]
        if len(eingang["videos"]) != vorher:
            gist_datei_schreiben(gist_id, GIST_EINGANG, json.dumps(eingang, ensure_ascii=False, indent=1))
            print(f"  Warteschlange: {vorher - len(eingang['videos'])} erledigte Vormerkung(en) entfernt.")
    print(f"Gist-Abgleich fertig: {len(daten['eintraege'])} Eintraege, "
          f"{len(daten.get('geloescht') or [])} geloescht, {nachgetragen} Marktkontext(e) nachgetragen.")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    gruppe = ap.add_mutually_exclusive_group()
    gruppe.add_argument("--eintraege",
                         help="Pfad zu einer JSON-Datei mit einer Liste neuer/aktualisierter Eintraege")
    gruppe.add_argument("--backfill", action="store_true",
                         help="Marktkontext bei bestehenden Eintraegen nachtragen (siehe run.py::pipeline)")
    ap.add_argument("--gist", action="store_true",
                    help="mit dem Coach-Gist abgleichen (vor und nach --eintraege)")
    args = ap.parse_args()
    if not (args.eintraege or args.backfill or args.gist):
        ap.error("--eintraege, --backfill oder --gist angeben")
    if args.gist:
        gist_abgleich()
    if args.backfill:
        backfill()
    elif args.eintraege:
        with open(args.eintraege, encoding="utf-8") as f:
            neue = json.load(f)
        fuege_eintraege_hinzu(neue)
        if args.gist:
            gist_abgleich()
