#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Mail-Screener fuer den Daily Signal Hub.

Liest per IMAP (freenet) die in config.json gelisteten Ordner, extrahiert aus
Betreff + Textkoerper Aktien-Signale (gleiche Logik wie pdf_screener) und
schreibt data/signals_raw_mail.json.

Passwort kommt aus dem macOS-Schluesselbund (Dienst aus config: keychain_dienst).
Niemals im Klartext / nicht in Git.

Test:  python3 src/mail_screener.py
"""

import email
import html
import imaplib
import json
import os
import re
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.header import decode_header

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import pdf_screener as PDF  # extrahiere_aus_text, lade_config

import pfade
PROJEKT = pfade.PROJEKT
DATA = pfade.DATA

# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def keychain_passwort(dienst):
    """macOS-Schluesselbund lokal; auf Linux (z.B. GitHub Actions, kein Schluesselbund)
    faellt das auf die Umgebungsvariable SIGNALHUB_IMAP_PASSWORD zurueck."""
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", dienst, "-w"],
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.environ.get("SIGNALHUB_IMAP_PASSWORD")

def dekodiere(s):
    if not s:
        return ""
    teile = decode_header(s)
    out = []
    for txt, enc in teile:
        if isinstance(txt, bytes):
            try:
                out.append(txt.decode(enc or "utf-8", "replace"))
            except (LookupError, TypeError):
                out.append(txt.decode("utf-8", "replace"))
        else:
            out.append(txt)
    return "".join(out)

def html_zu_text(h):
    h = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h)
    return re.sub(r"\s+", " ", h).strip()

def mail_text(msg):
    """Betreff + bester Textkoerper (plain bevorzugt, sonst HTML entschlackt)."""
    betreff = dekodiere(msg.get("Subject"))
    plain, htmltext = [], []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            if ct == "text/plain":
                plain.append(_part_text(part))
            elif ct == "text/html":
                htmltext.append(_part_text(part))
    else:
        if msg.get_content_type() == "text/html":
            htmltext.append(_part_text(msg))
        else:
            plain.append(_part_text(msg))
    body = "\n".join(t for t in plain if t)
    if not body.strip():
        body = html_zu_text("\n".join(t for t in htmltext if t))
    return betreff + "\n" + body

def _part_text(part):
    try:
        roh = part.get_payload(decode=True) or b""
        enc = part.get_content_charset() or "utf-8"
        return roh.decode(enc, "replace")
    except Exception:
        return ""

def mail_datum(msg):
    try:
        dt = email.utils.parsedate_to_datetime(msg.get("Date"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

# ---------------------------------------------------------------------------
# TraderFox-Betreff -> Firmenname + Signaltyp
# ---------------------------------------------------------------------------
SIGNAL_RX = {  # spezifisch -> generisch
    "jahreshoch":    re.compile(r"Jahreshoch\s+(.+?)\s*\(", re.I),
    "jahrestief":    re.compile(r"Jahrestief\s+(.+?)\s*\(", re.I),
    "trendtemplate": re.compile(r"Trendtemplate\s+neu\s+bei\s+(.+?)\s*$", re.I),
    "kursalarm":     re.compile(r"Kursalarm.*?\bbei\s+(.+?)\s*(?:-|$)", re.I),
    "tagesperf":     re.compile(r"Tagesperformance.*?\bbei\s+(.+?)\s*$", re.I),
    "chartanalyse":  re.compile(r"Chartanalyse\s+(.+?)\s*:", re.I),
}
PROZENT = re.compile(r"(.+?)\s*\(-?\d+[.,]\d+\s*%\)")
FUEHREND_STOPP = re.compile(
    r"^(neues|neuer|jahreshoch|jahrestief|rivalland|trendtemplate|neu|bei|gd|\d+|"
    r"short|long|kauf|verkauf|tagesperformance|chartsignal|signal)\b\s*", re.I)

def _saeubere_firma(n):
    n = re.sub(r"\s*-\s*Kurs.*$", "", n, flags=re.I)
    n = re.sub(r"\s+zu\s+[\d.,]+\$?\s*$", "", n, flags=re.I)
    n = n.strip(" .:-")
    return n if len(n) >= 3 else None

def firma_aus_betreff(subj):
    """Gibt (firmenname, signaltyp) oder (None, None)."""
    if not subj:
        return None, None
    s = subj.replace("\n", " ").strip()
    for typ, rx in SIGNAL_RX.items():
        m = rx.search(s)
        if m:
            return _saeubere_firma(m.group(1)), typ
    m = PROZENT.search(s)
    if m:
        kand = m.group(1)
        while FUEHREND_STOPP.match(kand):
            kand = FUEHREND_STOPP.sub("", kand, count=1)
        return _saeubere_firma(kand), "chartsignal"
    m = re.search(r"\bbei\s+(.+?)\s*(?:-|$)", s, re.I)
    if m:
        return _saeubere_firma(m.group(1)), "alert"
    return None, None

# ---------------------------------------------------------------------------
# Ordner-Namen robust aufloesen (config-Namen -> echte IMAP-Ordner)
# ---------------------------------------------------------------------------
def liste_ordner(M):
    typ, zeilen = M.list()
    echte = []
    for z in zeilen or []:
        s = z.decode("utf-8", "replace")
        m = re.search(r'"([^"]*)"\s*$', s) or re.search(r'([^ ]+)\s*$', s)
        if m:
            echte.append(m.group(1))
    return echte

def _leaf(name):
    return re.split(r"[./]", name)[-1].strip().lower()

def passe_ordner_an(ziel, echte):
    zl = _leaf(ziel)
    for e in echte:                      # exakter Leaf-Treffer
        if _leaf(e) == zl:
            return e
    for e in echte:                      # Teilstring
        if zl in e.lower():
            return e
    return None

# ---------------------------------------------------------------------------
# Retry/Reconnect: freenet killt die IMAP-Verbindung im Cloud-Lauf (GitHub-
# Runner, viele UID-FETCHs am Stueck) gelegentlich mitten im Abruf
# ("socket error: EOF", imaplib.IMAP4.abort) - beobachtet am 2026-08-20 bei
# ca. 600 Mails, unregelmaessig (mal geht der komplette Lauf durch, mal
# nicht). Ohne Auffangen war das ein unbehandelter Absturz: screene_mails()
# brach mitten in der UID-Schleife ab, signals_raw_mail.json wurde gar nicht
# geschrieben -> Mail-Quelle zeigte 0 Treffer im Dashboard, obwohl die Suche
# zuvor hunderte passende Mails gefunden hatte.
IMAP_MAX_VERSUCHE = 3
IMAP_RETRY_PAUSE = 2  # Sekunden zwischen Reconnect-Versuchen

def _neu_verbinden(ecfg, pw, ordner):
    M = imaplib.IMAP4_SSL(ecfg["imap_host"], ecfg.get("imap_port", 993), ssl_context=SSL_CTX)
    M.login(ecfg["benutzer"], pw)
    M.select(f'"{ordner}"', readonly=True)
    return M

def _mit_retry(M, ecfg, pw, ordner, aktion, beschreibung):
    """Fuehrt aktion(M) aus; bricht die IMAP-Verbindung mitten drin ab
    (IMAP4.abort/OSError), wird neu verbunden + der Ordner neu selektiert und
    bis zu IMAP_MAX_VERSUCHE erneut versucht. Gibt (M, ergebnis) zurueck -
    ergebnis ist None, wenn nach allen Versuchen weiterhin kein Erfolg (dann
    wird nur diese eine UID/Suche uebersprungen, nicht der ganze Lauf)."""
    for versuch in range(1, IMAP_MAX_VERSUCHE + 1):
        try:
            return M, aktion(M)
        except (imaplib.IMAP4.abort, OSError) as e:
            if versuch == IMAP_MAX_VERSUCHE:
                print(f"    ! {beschreibung}: {e} (nach {versuch} Versuchen aufgegeben)")
                return M, None
            print(f"    ! {beschreibung}: {e} - Reconnect ({versuch}/{IMAP_MAX_VERSUCHE})")
            time.sleep(IMAP_RETRY_PAUSE)
            try:
                M = _neu_verbinden(ecfg, pw, ordner)
            except Exception as e2:
                print(f"    ! Reconnect fehlgeschlagen: {e2}")
    return M, None

# ---------------------------------------------------------------------------
def screene_mails():
    cfg = PDF.lade_config()
    ecfg = cfg["quellen"]["email"]
    if not ecfg.get("aktiv"):
        print("E-Mail-Quelle in config deaktiviert.")
        return []

    pw = keychain_passwort(ecfg.get("keychain_dienst", "signal-hub-imap"))
    if not pw:
        sys.exit("Kein IMAP-Passwort im Schluesselbund. Erst hinterlegen:\n"
                 '  security add-generic-password -a "<adresse>" -s "signal-hub-imap" -U -w')

    try:
        M = imaplib.IMAP4_SSL(ecfg["imap_host"], ecfg.get("imap_port", 993), ssl_context=SSL_CTX)
        M.login(ecfg["benutzer"], pw)
    except imaplib.IMAP4.error as e:
        sys.exit(f"IMAP-Login fehlgeschlagen: {e}")

    grenze = datetime.now() - timedelta(days=ecfg.get("max_alter_tage", 14))
    since = grenze.strftime("%d-%b-%Y")
    filter_ = ecfg.get("absender_filter", [])

    signale = []
    verarbeitet = 0
    for ordner in ecfg.get("ordner", ["INBOX"]):
        try:
            M.select(f'"{ordner}"', readonly=True)
        except imaplib.IMAP4.error as e:
            print(f"  ! {ordner}: {e}")
            continue
        # passende UIDs sammeln (pro Absenderfilter), dedupliziert
        uids = set()
        if filter_:
            for fr in filter_:
                M, res = _mit_retry(M, ecfg, pw, ordner,
                    lambda m, fr=fr: m.uid("search", None, f'(SINCE "{since}" FROM "{fr}")'),
                    f"Suche ({fr})")
                if res:
                    typ, d = res
                    if d and d[0]:
                        uids.update(d[0].split())
        else:
            M, res = _mit_retry(M, ecfg, pw, ordner,
                lambda m: m.uid("search", None, f'(SINCE "{since}")'),
                "Suche")
            if res:
                typ, d = res
                if d and d[0]:
                    uids.update(d[0].split())
        print(f"  {ordner}: {len(uids)} passende Mails (seit {since})")

        for uid in uids:
            uid_txt = uid.decode() if isinstance(uid, bytes) else str(uid)
            # 1) nur Header laden (schnell)
            M, res = _mit_retry(M, ecfg, pw, ordner,
                lambda m, uid=uid: m.uid("fetch", uid,
                    "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"),
                f"Fetch Header UID {uid_txt}")
            if not res:
                continue
            typ, fh = res
            if not fh or not fh[0]:
                continue
            kopf = email.message_from_bytes(fh[0][1])
            subj = dekodiere(kopf.get("Subject"))
            frm = (dekodiere(kopf.get("From")) or "").lower()
            datum = mail_datum(kopf)
            mid = dekodiere(kopf.get("Message-ID")) or f"mail:{uid.decode()}"
            ist_tf = "traderfox" in frm
            gesehen = set()

            # 2) strukturierter TraderFox-Betreff -> Firma + Signaltyp
            firma, sigtyp = firma_aus_betreff(subj)
            if firma:
                signale.append({"ticker": None, "name": firma, "exchange": None, "markt": None,
                    "quelle_typ": "mail:" + sigtyp, "quelle_datei": mid, "datum": datum,
                    "seite": None, "kontext": subj[:200], "fund_art": "mail_betreff"})
                gesehen.add(("HL:" + firma.upper(), None))

            # 3) generische Extraktion: Betreff (TF) bzw. ganzer Text (aktienmagazin)
            if ist_tf:
                text, quelle = subj, "mail:traderfox"
            else:
                M, res = _mit_retry(M, ecfg, pw, ordner,
                    lambda m, uid=uid: m.uid("fetch", uid, "(RFC822)"),
                    f"Fetch RFC822 UID {uid_txt}")
                typ, fb = res if res else (None, None)
                msg = email.message_from_bytes(fb[0][1]) if fb and fb[0] else kopf
                text, quelle = mail_text(msg), "mail:aktienmagazin"
            signale.extend(PDF.extrahiere_aus_text(text, quelle_typ=quelle,
                quelle_datei=mid, datum=datum, seite=None, gesehen=gesehen))
            verarbeitet += 1
        print(f"  -> {verarbeitet} Mails verarbeitet")
    try:
        M.logout()
    except (imaplib.IMAP4.abort, OSError):
        pass
    return signale

# ---------------------------------------------------------------------------
def main():
    signale = screene_mails()
    print(f"\n=== {len(signale)} Roh-Signale aus E-Mails ===")
    from collections import Counter
    c = Counter((s.get("ticker") or "~" + (s.get("name") or "?")) for s in signale)
    for k, n in c.most_common(20):
        print(f"  {str(k)[:10]:10s} {n}x")
    os.makedirs(DATA, exist_ok=True)
    ziel = pfade.RAW_MAIL
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(signale, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {ziel}")

if __name__ == "__main__":
    main()
