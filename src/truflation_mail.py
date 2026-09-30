#!/usr/bin/env python3
"""Truflation „Truman Macro Daily“ -> data/truflation.json (Makro-Tab Markets 360).

Liest die Newsletter-Mails von truman@truflation.com aus demselben IMAP-Postfach
wie mail_screener.py (freenet, Passwort aus Schluesselbund bzw.
SIGNALHUB_IMAP_PASSWORD) und zieht daraus je Story nur FAKTEN: Land, Thema,
Titel, Status (Verified/Cited/Lookahead), Key Metrics (Name, Zeitraum, Wert)
und die Quellen als reinen Text.

Bewusst NICHT uebernommen (Entscheidung 2026-09-30): die Einordnungstexte
(„What's Truflation saying“, „Market relevance“, Fliesstext) - fremder
Newsletter-Inhalt, truflation.json liegt oeffentlich auf mts-hub.pages.dev.
Ebenso NIE Links aus der Mail: alle sind personalisierte Tracking-Links
(mlsend.com, darunter der Abmeldelink).

Die Truflation-API selbst ist kostenpflichtig (ab Professional-Plan) - daher
dieser Weg ueber den Newsletter.

Historie: je Ausgabe eine unveraenderliche Datei
<LOKAL>/truflation-history/JJJJ-MM-TT.json (Datum aus dem Mailkopf, nicht
Schreibdatum), in der Cloud nach R2 truflation/history/ (--ignore-existing).
truflation.json = die juengsten MAX_AUSGABEN Ausgaben aus dieser Historie -
eine leere Mailbox laesst den letzten Stand also stehen.

    python3 src/truflation_mail.py            # Mails holen, JSON schreiben
    python3 src/truflation_mail.py --eml a.eml [b.eml ...]   # offline testen
"""
import argparse
import email
import glob
import html as htmlmod
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pfade  # noqa: E402

ABSENDER = "truman@truflation.com"
MAX_ALTER_TAGE = 10
MAX_AUSGABEN = 10
HISTORIE = os.path.join(pfade.LOKAL, "truflation-history")
AUSGABE = os.path.join(pfade.DATA, "truflation.json")


def _text(fragment):
    """HTML-Fragment -> ein Zeilen-Klartext (Tags weg, Entities aufgeloest)."""
    t = re.sub(r"<[^>]+>", " ", fragment or "")
    t = htmlmod.unescape(t).replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


_MONATE = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


def parse_ausgabe(h):
    """Newsletter-HTML -> dict oder None, wenn es keine Truman-Ausgabe ist."""
    h = re.sub(r"<(style|head)\b.*?</\1>", " ", h, flags=re.S | re.I)
    kopf = re.search(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
                     r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})(?:\s*·\s*Research cut ([^<·]+))?", h)
    if not kopf or kopf.group(2) not in _MONATE:
        return None
    datum = f"{kopf.group(4)}-{_MONATE[kopf.group(2)]:02d}-{int(kopf.group(3)):02d}"
    ausgabe = {"datum": datum,
               "research_cut": (kopf.group(5) or "").strip() or None,
               "stories": []}

    # Jede Story beginnt mit <a id="story-…">; Ende = naechster Anker/Abschnitt
    starts = [m.start() for m in re.finditer(r'<a[^>]*\bid="story-[^"]+"', h)]
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(h)
        pm = h.find('id="prediction-market-watch"', s + 1)
        if 0 < pm < e:
            e = pm
        block = h[s:e]
        titel_m = re.search(r"<h2\b[^>]*>(.*?)</h2>", block, re.S)
        if not titel_m:
            continue
        zeile = _text(block[:titel_m.start()])       # "1. 🇺🇸 📈 United States · Thema"
        zm = re.match(r"(\d+)\.\s*(.*)$", zeile)
        nr, rest = (int(zm.group(1)), zm.group(2)) if zm else (i + 1, zeile)
        teile = [t.strip() for t in rest.split("·")]
        erster = teile[0]
        emo = re.match(r"^([^\w]*)\s*(.*)$", erster)   # Emojis vor dem Landesnamen
        story = {
            "nr": nr,
            "emoji": emo.group(1).strip() if emo else "",
            "land": (emo.group(2) if emo else erster).strip(),
            "thema": " · ".join(teile[1:]).strip(),
            "titel": _text(titel_m.group(1)),
            "status": None,
            "kennzahlen": [],
            "quellen": [],
        }
        nach = block[titel_m.end():]
        st = re.search(r"<p\b[^>]*>(.*?)</p>", nach, re.S)
        if st:
            s_txt = _text(st.group(1))
            if len(s_txt) <= 40:                     # "Verified", "Cited", "Lookahead" …
                story["status"] = s_txt

        km = re.search(r">\s*Key Metrics\s*</p>(.*?)</table>", block, re.S)
        if km:
            for zeile_km in re.findall(r"<tr\b[^>]*>(.*?)</tr>", km.group(1), re.S):
                zellen = re.findall(r"<td\b[^>]*>(.*?)</td>", zeile_km, re.S)
                if len(zellen) < 2:
                    continue
                per = re.search(r"<span\b[^>]*>(.*?)</span>", zellen[0], re.S)
                periode = _text(per.group(1)).lstrip("· ").strip() if per else None
                name = _text(re.sub(r"<span\b[^>]*>.*?</span>", "", zellen[0], flags=re.S))
                wert = _text(zellen[1])
                if name and wert:
                    story["kennzahlen"].append({"name": name, "periode": periode, "wert": wert})

        qm = re.search(r"<strong\b[^>]*>\s*Sources:\s*</strong>(.*?)</p>", block, re.S)
        if qm:
            # nur der sichtbare Linktext - nie die (Tracking-)URL
            story["quellen"] = [q for q in (_text(a) for a in
                                re.findall(r"<a\b[^>]*>(.*?)</a>", qm.group(1), re.S)) if q]
        ausgabe["stories"].append(story)
    return ausgabe if ausgabe["stories"] else None


def _html_teil(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            raw = part.get_payload(decode=True) or b""
            return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def ausgabe_aus_mail(roh):
    msg = email.message_from_bytes(roh)
    a = parse_ausgabe(_html_teil(msg))
    if a:
        import mail_screener as MS
        a["betreff"] = re.sub(r"\s*\|\s*Truman\s*$", "", MS.dekodiere(msg.get("Subject")) or "").strip()
    return a


def hole_mails():
    """Rohbytes aller Truman-Mails der letzten MAX_ALTER_TAGE (nur lesend)."""
    import mail_screener as MS
    import pdf_screener as PDF
    ecfg = PDF.lade_config()["quellen"]["email"]
    pw = MS.keychain_passwort(ecfg.get("keychain_dienst", "signal-hub-imap"))
    if not pw:
        print("Truflation: kein IMAP-Passwort - uebersprungen.")
        return []
    M = imaplib.IMAP4_SSL(ecfg["imap_host"], ecfg.get("imap_port", 993), ssl_context=MS.SSL_CTX)
    M.login(ecfg["benutzer"], pw)
    since = (datetime.now() - timedelta(days=MAX_ALTER_TAGE)).strftime("%d-%b-%Y")
    roh = []
    try:
        M.select("INBOX", readonly=True)
        _, d = M.uid("search", None, f'(SINCE "{since}" FROM "{ABSENDER}")')
        for uid in (d[0].split() if d and d[0] else []):
            _, fb = M.uid("fetch", uid, "(BODY.PEEK[])")
            if fb and fb[0] and isinstance(fb[0], tuple):
                roh.append(fb[0][1])
    finally:
        try:
            M.logout()
        except (imaplib.IMAP4.abort, OSError):
            pass
    return roh


def _pruefe_oeffentlich(obj):
    """Notbremse: nichts aus der Mail, das auf eine URL/Adresse hinauslaeuft."""
    s = json.dumps(obj, ensure_ascii=False)
    if re.search(r"https?://|mlsend|@[\w.-]+\.\w{2,}", s):
        raise ValueError("truflation.json enthielte eine URL oder Mailadresse - abgebrochen")


def speichere(ausgaben):
    os.makedirs(HISTORIE, exist_ok=True)
    neu = 0
    for a in ausgaben:
        _pruefe_oeffentlich(a)
        pfad = os.path.join(HISTORIE, a["datum"] + ".json")
        if os.path.exists(pfad):          # unveraenderlich: nie ueberschreiben
            continue
        with open(pfad, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=1)
        neu += 1

    dateien = sorted(glob.glob(os.path.join(HISTORIE, "????-??-??.json")), reverse=True)
    juengste = []
    for p in dateien[:MAX_AUSGABEN]:
        with open(p, encoding="utf-8") as f:
            juengste.append(json.load(f))
    if not juengste:
        print("Truflation: keine Ausgabe vorhanden - nichts geschrieben.")
        return
    out = {"quelle": "Truflation · Truman Macro Daily (Newsletter)",
           "erzeugt": datetime.now().isoformat(timespec="seconds"),
           "stand": juengste[0]["datum"],
           "ausgaben": juengste}
    _pruefe_oeffentlich(out)
    os.makedirs(os.path.dirname(AUSGABE), exist_ok=True)
    tmp = AUSGABE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, AUSGABE)
    print(f"Truflation: {neu} neue Ausgabe(n), Stand {out['stand']}, "
          f"{len(juengste)} in truflation.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eml", nargs="+", help="lokale .eml-Dateien statt IMAP (Test)")
    args = ap.parse_args()
    roh = [open(p, "rb").read() for p in args.eml] if args.eml else hole_mails()
    ausgaben = [a for a in (ausgabe_aus_mail(r) for r in roh) if a]
    print(f"Truflation: {len(roh)} Mail(s), {len(ausgaben)} lesbare Ausgabe(n)")
    speichere(ausgaben)


if __name__ == "__main__":
    main()
