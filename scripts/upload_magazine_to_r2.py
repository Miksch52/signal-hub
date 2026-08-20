#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spiegelt die aktuellen (noch nicht abgelaufenen) Traderfox-PDFs aus dem
lokalen iCloud-Ordner (config.json: quellen.pdf.ordner) nach R2
(signalhub-magazine/, Bucket-Root - genau da, wo der Cloud-Lauf sie erwartet,
siehe src/pfade.py::EXTERN_PDF_ORDNER). Nutzt dieselbe Alters-Filterfunktion
wie pdf_screener.py (finde_pdfs), damit lokaler Scan und R2-Spiegel nie
auseinanderlaufen.

Vorher gab es hierfuer KEINEN automatischen Schritt - die PDFs mussten von
Hand nach R2 hochgeladen werden. Blieb das aus, fand der Cloud-Lauf dort
nichts und zeigte PDF-Quelle 0, obwohl lokal (Mac mini/MacBook, echter
iCloud-Zugriff) alles funktionierte (Diagnose 2026-08-20).

Haelt einen kleinen lokalen Zustand (pdf_upload_state.json:
Dateiname -> mtime), um NUR neue/geaenderte PDFs hochzuladen und aus dem
Alters-Fenster gefallene wieder aus R2 zu loeschen - sonst waechst der
Bucket unbegrenzt und jeder Cloud-Lauf muesste immer mehr Altlast
("Magazine aus R2 laden"-Schritt in pipeline.yml) pullen.

Aufruf: scripts/upload_magazine_to_r2.py
Gedacht fuer einen LaunchAgent mit WatchPaths auf den PDF-Ordner (siehe
com.maick.signalhub.pdfsync.plist) - kein manueller Schritt mehr noetig,
sobald ein neues Magazin im iCloud-Ordner landet.
"""

import json
import os
import subprocess
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "src"))
import pdf_screener as PDF
import pfade

STATE_PFAD = os.path.join(
    os.path.expanduser("~/Library/Application Support/SignalHub"),
    "pdf_upload_state.json")
# .../Signal-Hub/scripts/.. -> Signal-Hub, davon .. -> Maick Trading System,
# darin cloudflare-worker (bereits per "npx wrangler login" authentifiziert,
# gleiches Muster wie scripts/upload_to_r2.sh).
WRANGLER_CWD = os.path.join(pfade.PROJEKT, "..", "cloudflare-worker")
NPX = "/usr/local/bin/npx"


def lade_state():
    if os.path.exists(STATE_PFAD):
        try:
            return json.load(open(STATE_PFAD, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def speichere_state(state):
    os.makedirs(os.path.dirname(STATE_PFAD), exist_ok=True)
    with open(STATE_PFAD, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def wrangler(*args):
    r = subprocess.run([NPX, "--yes", "wrangler", *args],
                        cwd=WRANGLER_CWD, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr)


def main():
    cfg = PDF.lade_config()
    pdfcfg = cfg["quellen"]["pdf"]
    ordner = pdfcfg["ordner"]
    if not os.path.isdir(ordner):
        print(f"PDF-Ordner nicht gefunden: {ordner}")
        return
    max_alter_tage = pdfcfg.get("max_alter_tage", 21)
    aktuelle_pfade = PDF.finde_pdfs(ordner, max_alter_tage)
    aktuell = {os.path.basename(p): os.path.getmtime(p) for p in aktuelle_pfade}

    state = lade_state()
    hochgeladen, fehler, geloescht = 0, 0, 0

    for name, mtime in aktuell.items():
        if state.get(name) == mtime:
            continue  # unveraendert, steht schon in R2
        pfad = os.path.join(ordner, name)
        ok, out = wrangler("r2", "object", "put", f"signalhub-magazine/{name}",
                            f"--file={pfad}", "--remote", "-y")
        if ok:
            state[name] = mtime
            hochgeladen += 1
            print(f"  + {name}")
        else:
            fehler += 1
            print(f"  ! {name}: {out.strip()[-300:]}")

    veraltet = set(state) - set(aktuell)
    for name in veraltet:
        ok, out = wrangler("r2", "object", "delete", f"signalhub-magazine/{name}",
                            "--remote", "-y")
        if ok or "does not exist" in out.lower() or "not found" in out.lower():
            del state[name]
            geloescht += 1
            print(f"  - {name} (aus dem {max_alter_tage}-Tage-Fenster gefallen)")
        else:
            print(f"  ! Loeschen fehlgeschlagen {name}: {out.strip()[-300:]}")

    speichere_state(state)
    print(f"\n{hochgeladen} hochgeladen, {geloescht} veraltete entfernt, {fehler} Fehler. "
          f"{len(aktuell)} PDFs aktuell im Fenster (<= {max_alter_tage} Tage).")


if __name__ == "__main__":
    main()
