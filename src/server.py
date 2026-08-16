#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lokaler Webserver fuer den Daily Signal Hub.

Serviert das Dashboard + Daten ueber HTTP, damit iPhone/iPad/MacBook es im WLAN
(oder per Tailscale von unterwegs) oeffnen UND bedienen koennen:
  GET  /                 -> signal-hub.html
  GET  /data/signals.json, /config.json, ...   (statische Dateien)
  POST /api/config       -> speichert config.json (Einstellungen vom Handy)
  POST /api/run          -> startet einen Aktualisierungslauf (run.py) im Hintergrund
  GET  /api/status       -> {laeuft, stand, anzahl}

Nur fuer dein privates LAN/Tailscale gedacht (kein offenes Internet).
Start:  python3 src/server.py        (oder Doppelklick auf "Signal-Hub Server starten.command")
"""

import http.server
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone

PORT = 8091
HOST = "0.0.0.0"   # LAN/Tailscale-erreichbar; nur lokal: "127.0.0.1"

HIER = os.path.dirname(os.path.abspath(__file__))
PROJEKT = os.path.dirname(HIER)
DATA = os.path.join(PROJEKT, "data")
CONFIG = os.path.join(PROJEKT, "config.json")
PY = sys.executable
# "🗒️ Watchlist erstellen" (siehe do_POST /api/watchlist unten): landet zentral im
# MTS-Hauptordner (ein Verzeichnis ueber Signal-Hub/, das ist bereits der Geschwister-
# Ordner in iCloud, siehe CLAUDE.md "Nested Git-Repos") statt im eigenen data/ -
# gleiches Prinzip wie Lokaler-Trend-Screener/config.py::WATCHLISTEN_DIR, dort die
# Referenzimplementierung. Eigener Unterordner "Signal-Hub", damit der gemeinsame
# Watchlisten/-Ordner nicht unuebersichtlich wird.
WATCHLISTEN_DIR = os.path.join(os.path.dirname(PROJEKT), "Watchlisten", "Signal-Hub")

_lauf_lock = threading.Lock()
_laeuft = {"v": False, "start": None, "ende": None, "log": ""}
RUN_TIMEOUT = 600   # Sekunden — ein voller Lauf (PDF+Mail+Finviz+Scorer) dauert
                    # bei kaltem Cache einige Minuten; haengt er laenger, abbrechen.


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _starte_lauf(mit_push=False):
    if not _lauf_lock.acquire(blocking=False):
        return False
    def job():
        _laeuft.update(v=True, start=datetime.now().isoformat(timespec="seconds"), ende=None)
        proc = None
        try:
            args = [PY, os.path.join(HIER, "run.py")]
            if mit_push:
                args.append("--notify")
            # WICHTIG: harter Timeout MIT Prozessgruppen-Kill. run.py startet jede
            # Stufe (pdf/mail/finviz/scorer) als eigenen Subprozess. Würde man beim
            # Timeout nur run.py killen, hielten die Enkel (v.a. das langsame
            # scorer.py) die Ausgabe-Pipe offen → subprocess.run hinge ewig im
            # Aufräumen, das finally liefe nie, _laeuft.v bliebe für immer True
            # ("Lauf läuft bereits"). Daher eigene Session (start_new_session) und
            # beim Timeout die GANZE Gruppe beenden.
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, cwd=PROJEKT, start_new_session=True)
            try:
                out, _ = proc.communicate(timeout=RUN_TIMEOUT)
                _laeuft["log"] = (out or "")[-2000:]
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                try:
                    out, _ = proc.communicate(timeout=10)
                except Exception:
                    out = ""
                _laeuft["log"] = (f"Abbruch: Lauf überschritt {RUN_TIMEOUT}s, Prozessgruppe "
                                  f"beendet (hängender Abruf?). Bitte erneut versuchen.")
        except Exception as e:
            _laeuft["log"] = f"Fehler: {e}"
            if proc is not None:
                _kill_group(proc)
        finally:
            _laeuft.update(v=False, ende=datetime.now().isoformat(timespec="seconds"))
            _lauf_lock.release()
    threading.Thread(target=job, daemon=True).start()
    return True


def _kill_group(proc):
    """Beendet die gesamte Prozessgruppe (run.py + alle Kinder wie scorer.py)."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PROJEKT, **kw)

    def log_message(self, *a):
        pass  # ruhiger

    def _json(self, obj, code=200, cors=False):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/status":   # bewusst offen + CORS: Launcher-Badge (Port 8090/file://) fragt hier an
            stand, anzahl = None, 0
            sp = os.path.join(DATA, "signals.json")
            if os.path.exists(sp):
                try:
                    d = json.load(open(sp, encoding="utf-8"))
                    stand, anzahl = d.get("erstellt"), d.get("anzahl", 0)
                except Exception:
                    pass
            return self._json({"laeuft": _laeuft["v"], "stand": stand,
                               "anzahl": anzahl, "ende": _laeuft["ende"]}, cors=True)
        # Lesen (Dashboard + Signaldaten) ist offen — der Token schuetzt nur die
        # Schreib-Endpunkte (POST /api/config, /api/run). So oeffnet der Hub auf
        # iPhone/iPad/Mac ohne Token-Abfrage; config.json (enthaelt Token) bleibt gesperrt.
        if p == "/minervini-coach-2.html":   # Coach liegt eine Ebene ueber dem Hub-Ordner; Route existiert,
                                             # damit Fernzugriff (Tailscale) mit nur EINEM Port auskommt
            return self._datei(os.path.join(os.path.dirname(PROJEKT), "minervini-coach-2.html"))
        if p.startswith("/assets/") and ".." not in p:
            # Geteilte Assets (z.B. marktampel.js fuer die einheitliche
            # Marktampel) liegen im Repo-Root, eine Ebene ueber dem Hub-Ordner.
            # Wird der Hub per zumLiveServer() auf diesen 8091-Server umgeleitet,
            # muss /assets/ trotzdem erreichbar sein (auf 8090/Pages ist es das
            # ueber den Projekt-Root ohnehin).
            rel = p[len("/assets/"):]
            typ = ("application/javascript; charset=utf-8" if rel.endswith(".js")
                   else "text/css; charset=utf-8" if rel.endswith(".css")
                   else "application/octet-stream")
            return self._datei(os.path.join(os.path.dirname(PROJEKT), "assets", rel), typ)
        if p.endswith("config.json"):   # Token nicht ausliefern (Dashboard nutzt config.js)
            return self._json({"fehler": "nicht oeffentlich"}, 403)
        if p == "/":
            self.path = "/signal-hub.html"
        return super().do_GET()

    def _datei(self, pfad, typ="text/html; charset=utf-8"):
        try:
            body = open(pfad, "rb").read()
        except OSError:
            return self._json({"fehler": "nicht gefunden"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token_soll(self):
        try:
            return (json.load(open(CONFIG, encoding="utf-8")).get("server", {}) or {}).get("token")
        except Exception:
            return None

    def _token_ok(self):
        soll = self._token_soll()
        if not soll:
            return True   # kein Token gesetzt -> offen (Altverhalten)
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        ist = self.headers.get("X-Token") or (q.get("token", [None])[0])
        if ist == soll:
            # Token kam per URL -> als Cookie merken, damit Folge-Requests
            # (data/*.js, POSTs vom Dashboard) ohne ?token=... durchkommen
            self._cookie_setzen = bool(q.get("token"))
            return True
        kekse = [k.strip() for k in (self.headers.get("Cookie") or "").split(";")]
        return f"hubtoken={soll}" in kekse

    def end_headers(self):
        if getattr(self, "_cookie_setzen", False):
            self._cookie_setzen = False
            self.send_header("Set-Cookie",
                             f"hubtoken={self._token_soll()}; Path=/; Max-Age=31536000; SameSite=Lax")
        super().end_headers()

    def do_POST(self):
        pfad = self.path.split("?")[0]
        laenge = int(self.headers.get("Content-Length", 0) or 0)
        roh = self.rfile.read(laenge) if laenge else b""

        if not self._token_ok():
            return self._json({"ok": False, "fehler": "Token fehlt/falsch"}, 403)

        if pfad == "/api/config":
            try:
                neu = json.loads(roh.decode("utf-8"))
                assert isinstance(neu, dict) and "score_gewichte" in neu
            except Exception as e:
                return self._json({"ok": False, "fehler": f"Ungueltige Config: {e}"}, 400)
            # server-Block (Token) bewahren, falls das Dashboard ihn nicht mitsendet
            try:
                alt = json.load(open(CONFIG, encoding="utf-8"))
                if not (neu.get("server") or {}).get("token") and alt.get("server"):
                    neu["server"] = alt["server"]
            except Exception:
                pass
            tmp = CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(neu, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG)
            return self._json({"ok": True})

        if pfad == "/api/run":
            mit_push = b"push" in roh.lower()
            gestartet = _starte_lauf(mit_push=mit_push)
            return self._json({"ok": gestartet,
                               "info": "Lauf gestartet" if gestartet else "Lauf laeuft bereits"})

        if pfad == "/api/watchlist":
            return self._api_watchlist(roh)

        return self._json({"ok": False, "fehler": "unbekannt"}, 404)

    # "🗒️ Watchlist erstellen" im Signal-Hub-Dashboard: schreibt die aktuell
    # gefilterten Treffer (CURRENT_LIST im Frontend, siehe render()) als TradingView-
    # importierbare .txt + lesbare Zusammenfassung nach WATCHLISTEN_DIR. Referenz-
    # implementierung (zuerst gebaut): Lokaler-Trend-Screener/hub_server.py::
    # _api_watchlist - dieselbe Validierung/Dateistruktur, hier nur der Zielordner
    # (Signal-Hub statt Trend-Screener) und der Token-Schutz (POSTs, siehe oben) anders.
    def _api_watchlist(self, roh):
        if len(roh) > 200_000:
            return self._json({"ok": False, "fehler": "Anfrage zu groß"}, 413)
        try:
            body = json.loads(roh.decode("utf-8")) if roh else {}
        except Exception:
            return self._json({"ok": False, "fehler": "ungültiges JSON"}, 400)

        def _saubere_symbole(feld):
            out = []
            for s in (body.get(feld) or []):
                s = str(s).strip().upper()
                if s and re.fullmatch(r"[A-Z0-9.\-:]{1,20}", s):
                    out.append(s)
            return out

        roh_liste = body.get("symbole") or []
        if not isinstance(roh_liste, list) or len(roh_liste) > 3000:
            return self._json({"ok": False, "fehler": "ungültige oder zu lange Symbolliste"}, 400)
        symbole = _saubere_symbole("symbole")
        if not symbole:
            return self._json({"ok": False, "fehler": "keine gültigen Titel in der aktuellen Filteransicht"}, 400)
        roh_tf = body.get("symbole_tf")
        symbole_tf = _saubere_symbole("symbole_tf") if isinstance(roh_tf, list) else symbole

        lesbar = body.get("lesbar") or ""
        if not isinstance(lesbar, str) or len(lesbar) > 100_000:
            return self._json({"ok": False, "fehler": "ungültiger lesbarer Inhalt"}, 400)
        zusammenfassung = body.get("zusammenfassung") or "keine Angabe"
        if not isinstance(zusammenfassung, str) or len(zusammenfassung) > 2000:
            zusammenfassung = "keine Angabe"
        titel_roh = str(body.get("titel") or "Signal-Hub")
        titel = re.sub(r"[^A-Za-z0-9äöüÄÖÜß _-]", "", titel_roh).strip()[:60] or "Signal-Hub"

        os.makedirs(WATCHLISTEN_DIR, exist_ok=True)
        basisname = f"{datetime.now().strftime('%Y-%m-%d')}_Signal-Hub_{titel}"
        pfad = os.path.join(WATCHLISTEN_DIR, basisname + ".txt")
        pfad_lesbar = os.path.join(WATCHLISTEN_DIR, basisname + "_lesbar.txt")
        n = 2
        while os.path.exists(pfad) or os.path.exists(pfad_lesbar):
            pfad = os.path.join(WATCHLISTEN_DIR, f"{basisname}_{n}.txt")
            pfad_lesbar = os.path.join(WATCHLISTEN_DIR, f"{basisname}_{n}_lesbar.txt")
            n += 1
        stand = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"###SIGNAL-HUB {titel} · {stand} · {len(symbole)} Titel"
        with open(pfad, "w", encoding="utf-8") as f:
            f.write(header + "\n" + ",".join(symbole))
        header_lesbar = (f"Signal-Hub – {titel} – {stand} – {len(symbole)} Titel\n"
                          f"Filter: {zusammenfassung}\n" + "-" * 60)
        with open(pfad_lesbar, "w", encoding="utf-8") as f:
            f.write(header_lesbar + "\n" + lesbar)

        tf_basis = f"{datetime.now().strftime('%Y-%m-%d')}_TF"
        pfad_tf = os.path.join(WATCHLISTEN_DIR, tf_basis + ".txt")
        n_tf = 2
        while os.path.exists(pfad_tf):
            pfad_tf = os.path.join(WATCHLISTEN_DIR, f"{tf_basis}_{n_tf}.txt")
            n_tf += 1
        with open(pfad_tf, "w", encoding="utf-8") as f:
            f.write("\n".join(symbole_tf))

        return self._json({"ok": True, "datei": os.path.basename(pfad),
                           "datei_lesbar": os.path.basename(pfad_lesbar),
                           "datei_tf": os.path.basename(pfad_tf), "anzahl": len(symbole)})


def main():
    # Dashboard so anpassen, dass relative Pfade unter / funktionieren:
    srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    ip = _lan_ip()
    print("=" * 56)
    print("  📈 Daily Signal Hub – Server laeuft")
    print("=" * 56)
    print(f"  Auf diesem Mac:   http://localhost:{PORT}/")
    print(f"  Im WLAN (iPhone): http://{ip}:{PORT}/")
    print(f"  Von unterwegs:    via Tailscale -> http://<mac-name>:{PORT}/")
    print("  Beenden: Strg+C")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
