#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aufnahme von YouTube-Transkripten fuer den Coach-Reiter "Lektionen".

Ablauf: .txt-Datei (YouTube "Transkript anzeigen" -> kopieren/speichern) in
minervini-lexikon-eingang/transkripte/ ablegen, dann
  python3 src/minervini_transkripte.py [--url URL] [--datum JJJJ-MM-TT]
Neue Transkripte wandern nach data/minervini-lexikon/transkripte/ (nur
lokal in iCloud, nie Gist/Deploy - Urheberrecht, siehe CLAUDE.md), Dubletten
nach eingang/transkripte/_duplikate/ (nie geloescht).

Dublettenpruefung, ein Treffer genuegt:
  1. gleiche YouTube-Video-ID (aus --url)
  2. gleicher Text-Hash (Zeitmarken/Gross-Klein/Satzzeichen ignoriert)
  3. gleicher Textanfang (erste 400 normalisierte Zeichen) - faengt eine
     leicht abweichende Neu-Exportierung desselben Videos
Gleicher Titel bei anderem Text ist nur eine Warnung (Serien haben oft
aehnliche Titel), kein Abbruch.

Bei einer Dublette mit neuer --url wird die URL im Index und im Lexikon-
Eintrag nachgetragen (Zeitmarken-Links funktionieren erst mit URL).

JSON-Import (Apify "YouTube Scraper"): --json datei.json registriert jedes
Video als "bekannt" (Dublettenpruefung, Status ohne Transkript) und uebernimmt
vorhandene Untertitel (SRT) als Transkript. Videos ohne Untertitel bleiben
registriert; ein spaeter abgelegtes Transkript mit passender --url ergaenzt
sie, statt als Dublette abgewiesen zu werden.

Die Kernaussagen erstellt danach Claude im Chat und legt sie per
minervini_lexikon.py --eintraege als Eintrag typ "video_lektion" ab.

Warteschlange aus dem Coach (seit 2026-09-23): "＋ Video vormerken" im Reiter
Lektionen legt Link + optional eingefuegtes Transkript in die Datei
minervini-lexikon-eingang.json im privaten Coach-Gist. --eingang holt sie ab:
Transkripte laufen durch dieselbe Dublettenpruefung wie oben, danach wird der
Transkripttext aus dem Gist entfernt (Volltext bleibt nur lokal, Urheberrecht),
Videos ohne Transkript werden im Index registriert. Die Vormerkung selbst
verschwindet, sobald es Lektionen zu dem Video gibt (minervini_lexikon.py
--gist raeumt auf). Im Coach geloeschte Videos werden nicht wieder aufgenommen.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime

import pfade

EINGANG = os.path.join(pfade.MINERVINI_LEXIKON_EINGANG, "transkripte")
DUPLIKATE = os.path.join(EINGANG, "_duplikate")
INDEX = os.path.join(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, "index.json")
ZEIT = re.compile(r"^\[(\d+(?::\d{2}){1,2})\]\s*(.*)$")


def _norm(text):
    return re.sub(r"[^a-z0-9äöüß]", "", text.lower())


def _video_id(url):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _lade_index():
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            return json.load(f)
    return {"videos": {}}


def _speichere_index(idx):
    os.makedirs(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, exist_ok=True)
    pfade.schreibe_json_atomar(INDEX, idx, ensure_ascii=False, indent=1)


def parse(pfad):
    with open(pfad, encoding="utf-8") as f:
        zeilen = f.read().splitlines()
    titel = re.sub(r"\s*-\s*YouTube\s*$", "", re.sub(r"^\(\d+\)\s*", "", zeilen[0].strip())) if zeilen else ""
    segmente = []
    for z in zeilen[1:]:
        m = ZEIT.match(z.strip())
        if m:
            segmente.append([m.group(1), m.group(2)])
        elif z.strip() and segmente:
            segmente[-1][1] += " " + z.strip()
    text = " ".join(t for _, t in segmente)
    return titel, segmente, text


def _lexikon_eintrag_finden(daten, video_id, hash_):
    for e in daten.get("eintraege", []):
        if e.get("typ") != "video_lektion":
            continue
        if (video_id and e.get("video_id") == video_id) or e.get("transkript_hash") == hash_:
            return e
    return None


def verarbeite(pfad, url=None, datum=None):
    name = os.path.basename(pfad)
    titel, segmente, text = parse(pfad)
    if len(segmente) < 20:
        print(f"! {name}: nur {len(segmente)} Zeitabschnitte erkannt - kein gueltiges Transkript, uebersprungen.")
        return None
    norm = _norm(text)
    h = hashlib.sha256(norm.encode()).hexdigest()[:16]
    kopf = hashlib.sha256(norm[:400].encode()).hexdigest()[:16]
    vid = _video_id(url)
    idx = _lade_index()

    treffer = None
    for k, v in idx["videos"].items():
        if (vid and v.get("video_id") == vid) or v.get("hash") == h or v.get("kopf") == kopf:
            treffer = (k, v)
            break
    if not treffer:
        for k, v in idx["videos"].items():
            if _norm(v.get("titel", "")) == _norm(titel) and titel:
                print(f"  Hinweis: gleicher Titel wie {k}, aber anderer Text - wird als neues Video behandelt.")

    if treffer and not treffer[1].get("datei"):
        k, v = treffer
        ziel = os.path.join(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, f"{k}.txt")
        os.makedirs(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, exist_ok=True)
        shutil.move(pfad, ziel)
        v.update({"hash": h, "kopf": kopf, "datei": os.path.basename(ziel), "dauer_bis": segmente[-1][0],
                  "abschnitte": len(segmente), "aufgenommen": datetime.now().astimezone().isoformat(timespec="seconds")})
        _speichere_index(idx)
        print(f"+ Transkript zu bekanntem Video ergaenzt: {k} | {v.get('titel', '')[:60]} | {len(segmente)} Abschnitte")
        return k

    if treffer:
        k, v = treffer
        print(f"= Dublette: {name} ist bereits erfasst als {k} ({v.get('titel', '')[:60]}) - nicht erneut aufgenommen.")
        if url and vid and not v.get("video_id"):
            v["video_id"], v["url"] = vid, url
            _speichere_index(idx)
            import minervini_lexikon as ml
            daten = ml._lade_lexikon()
            e = _lexikon_eintrag_finden(daten, None, v["hash"])
            if e:
                e["video_id"], e["video_url"] = vid, url
                ml._speichere_lexikon(daten)
            print(f"  URL nachgetragen: {url}")
        os.makedirs(DUPLIKATE, exist_ok=True)
        shutil.move(pfad, os.path.join(DUPLIKATE, f"{datetime.now():%Y%m%d-%H%M%S}_{name}"))
        return None

    schluessel = f"yt-{vid}" if vid else f"yt-t-{h[:10]}"
    os.makedirs(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, exist_ok=True)
    ziel = os.path.join(pfade.MINERVINI_LEXIKON_TRANSKRIPTE, f"{schluessel}.txt")
    shutil.move(pfad, ziel)
    idx["videos"][schluessel] = {
        "titel": titel, "hash": h, "kopf": kopf, "video_id": vid, "url": url,
        "datum": datum, "datei": os.path.basename(ziel), "dauer_bis": segmente[-1][0],
        "abschnitte": len(segmente), "aufgenommen": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _speichere_index(idx)
    print(f"+ Neu: {schluessel} | {titel[:70]} | {len(segmente)} Abschnitte, bis {segmente[-1][0]} -> {ziel}")
    return schluessel


ROH_ZEIT = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(.*)$")
# Barrierefreiheits-Beschriftungen, die YouTube beim Kopieren mitliefert
ROH_LABEL = re.compile(r"^\d+\s*(sekunden?|seconds?|minuten?|minutes?|stunden?|hours?)"
                       r"(,?\s*\d+\s*(sekunden?|seconds?|minuten?|minutes?))*$", re.I)


def _roh_zu_txt(roh, titel):
    """Eingefuegtes YouTube-Transkript ("0:00" auf eigener Zeile oder davor)
    in das Format "[mm:ss] Text" bringen, das parse() erwartet."""
    abschnitte = []
    for z in (roh or "").splitlines():
        z = z.strip()
        if not z or ROH_LABEL.match(z):
            continue
        m = ROH_ZEIT.match(z)
        if m:
            abschnitte.append([m.group(1), m.group(2).strip()])
        elif abschnitte:
            abschnitte[-1][1] = (abschnitte[-1][1] + " " + z).strip()
    return titel + "\n" + "\n".join(f"[{t}] {x}" for t, x in abschnitte if x) + "\n"


def eingang_abholen():
    import minervini_lexikon as ml
    gist_id = ml.gist_finden()
    roh = ml.gist_datei_lesen(gist_id, ml.GIST_EINGANG) if gist_id else None
    if not roh:
        print("Warteschlange: leer (keine Vormerkungen im Gist).")
        return
    eingang = json.loads(roh)
    videos = eingang.get("videos") or []
    daten = ml._lade_lexikon()
    fertig = {e.get("video_id") for e in daten["eintraege"] if e.get("typ") == "video_lektion"}
    gesperrt = ml.gesperrte_video_ids(daten)
    geaendert = False
    os.makedirs(EINGANG, exist_ok=True)
    for it in videos:
        vid, url = it.get("video_id"), it.get("url")
        if not vid or vid in fertig or vid in gesperrt:
            continue
        titel = it.get("titel") or f"YouTube-Video {vid}"
        if it.get("transkript") and it.get("status") != "uebernommen":
            tmp = os.path.join(EINGANG, f"{vid}.txt")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(_roh_zu_txt(it["transkript"], titel))
            verarbeite(tmp, url, it.get("datum"))
            if os.path.exists(tmp):
                os.remove(tmp)
            eintrag = next((v for v in _lade_index()["videos"].values() if v.get("video_id") == vid), None)
            if eintrag and eintrag.get("datei"):
                it.update({"status": "uebernommen", "transkript": None,
                           "uebernommen_am": datetime.now().astimezone().isoformat(timespec="seconds")})
                geaendert = True
            else:
                it["status"] = "fehler"
                it["fehler"] = "Transkript nicht lesbar (zu wenige Zeitmarken) - bleibt vorgemerkt"
                geaendert = True
        elif not it.get("transkript"):
            idx = _lade_index()
            if not any(v.get("video_id") == vid for v in idx["videos"].values()):
                idx["videos"][f"yt-{vid}"] = {
                    "titel": titel, "video_id": vid, "url": url, "datum": it.get("datum"),
                    "hash": None, "kopf": None, "datei": None, "quelle": "coach_warteschlange",
                    "aufgenommen": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                _speichere_index(idx)
                print(f"+ Registriert (ohne Transkript): yt-{vid} | {titel[:60]}")
    if geaendert:
        ml.gist_datei_schreiben(gist_id, ml.GIST_EINGANG, json.dumps(eingang, ensure_ascii=False, indent=1))
    idx = _lade_index()["videos"]
    print("\nWarteschlange:")
    for it in videos:
        vid = it.get("video_id")
        if vid in fertig or vid in gesperrt:
            zustand = "erledigt (wird beim naechsten --gist entfernt)"
        elif any(v.get("video_id") == vid and v.get("datei") for v in idx.values()):
            zustand = "Transkript liegt lokal - bereit fuer Kernaussagen"
        elif it.get("status") == "fehler":
            zustand = "FEHLER: " + it.get("fehler", "")
        else:
            zustand = "ohne Transkript - Transkript besorgen (Apify-JSON oder .txt)"
        print(f"  {vid} | {(it.get('titel') or it.get('url') or '')[:55]} | {zustand}")


def _srt_zu_txt(srt, titel):
    zeilen = [titel]
    letzter = None
    for block in re.split(r"\n\s*\n", srt.strip()):
        teile = block.strip().splitlines()
        if len(teile) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.]\d+\s*-->", teile[1])
        text = " ".join(t.strip() for t in teile[2:]).strip()
        if not m or not text or text == letzter:
            continue
        letzter = text
        sek = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        h_, r = divmod(sek, 3600)
        zeit = f"{h_}:{r // 60:02d}:{r % 60:02d}" if h_ else f"{r // 60:02d}:{r % 60:02d}"
        zeilen.append(f"[{zeit}] {text}")
    return "\n".join(zeilen) + "\n"


def importiere_json(pfad):
    with open(pfad, encoding="utf-8") as f:
        videos = json.load(f)
    idx = _lade_index()
    bekannt = {v.get("video_id") for v in idx["videos"].values() if v.get("video_id")}
    neu_registriert = mit_transkript = schon_da = 0
    import minervini_lexikon as ml
    gesperrt = ml.gesperrte_video_ids(ml._lade_lexikon())
    os.makedirs(EINGANG, exist_ok=True)
    for it in videos:
        vid, url, titel = it.get("id"), it.get("url"), it.get("title") or ""
        if not vid or not url:
            continue
        if vid in gesperrt:
            print(f"  - {vid} wurde im Coach geloescht - uebersprungen.")
            continue
        datum = (it.get("date") or "")[:10] or None
        srt = next((s.get("srt") for s in (it.get("subtitles") or []) if s.get("srt")), None)
        vorhanden = next((v for v in idx["videos"].values() if v.get("video_id") == vid), None)
        if srt and not (vorhanden and vorhanden.get("datei")):
            tmp = os.path.join(EINGANG, f"{vid}.txt")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(_srt_zu_txt(srt, titel))
            _speichere_index(idx)
            if verarbeite(tmp, url, datum):
                mit_transkript += 1
            if os.path.exists(tmp):
                os.remove(tmp)
            idx = _lade_index()
            bekannt = {v.get("video_id") for v in idx["videos"].values() if v.get("video_id")}
            continue
        if vid in bekannt:
            schon_da += 1
            continue
        idx["videos"][f"yt-{vid}"] = {
            "titel": titel, "video_id": vid, "url": url, "datum": datum, "dauer": it.get("duration"),
            "kanal": it.get("channelName"), "hash": None, "kopf": None, "datei": None,
            "aufgenommen": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        bekannt.add(vid)
        neu_registriert += 1
    _speichere_index(idx)
    print(f"JSON-Import: {len(videos)} Videos gelesen, {neu_registriert} neu registriert (ohne Transkript), "
          f"{mit_transkript} mit Transkript uebernommen, {schon_da} bereits bekannt.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="Apify-YouTube-Scraper-JSON importieren (siehe Modul-Docstring)")
    ap.add_argument("--eingang", action="store_true", help="im Coach vorgemerkte Videos aus dem Gist abholen")
    ap.add_argument("--datei", help="einzelne Datei statt des ganzen Eingang-Ordners")
    ap.add_argument("--url", help="YouTube-URL (nur mit einer einzelnen Datei)")
    ap.add_argument("--datum", help="Veroeffentlichungsdatum JJJJ-MM-TT (nur mit einer einzelnen Datei)")
    a = ap.parse_args()
    if a.eingang:
        eingang_abholen()
        raise SystemExit(0)
    if a.json:
        importiere_json(a.json)
        raise SystemExit(0)
    os.makedirs(EINGANG, exist_ok=True)
    dateien = [a.datei] if a.datei else sorted(
        os.path.join(EINGANG, f) for f in os.listdir(EINGANG) if f.lower().endswith(".txt"))
    if len(dateien) > 1 and (a.url or a.datum):
        raise SystemExit("--url/--datum nur mit genau einer Datei (sonst wuerden alle dieselbe URL bekommen).")
    if not dateien:
        print("Keine .txt-Dateien in minervini-lexikon-eingang/transkripte/.")
    neu = [x for x in (verarbeite(d, a.url, a.datum) for d in dateien) if x]
    print(f"\n{len(neu)} neu, {len(dateien) - len(neu)} Dublette(n)/uebersprungen.")
