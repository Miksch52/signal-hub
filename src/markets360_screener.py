#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markets-360-Quelle fuer den Daily Signal Hub.

Liest die von der Markets-360-Cloud-Pipeline (eigenes Repo
Miksch52/minervini-markets-360, GitHub Actions) erzeugte und nach R2
hochgeladene markets360_latest.csv (vom signal-hub.yml-Workflow bereits
nach _magazine/markets360/ gezogen) und schreibt sie im gemeinsamen
Rohsignal-Schema (gleiches Format wie PDF/Mail/Finviz) nach
data/signals_raw_markets360.json.

Fehlt die Datei (z. B. lokaler Testlauf ohne R2-Sync), wird sauber mit
0 Signalen beendet statt einen Fehler zu werfen.

Test:  python3 src/markets360_screener.py
"""

import csv
import json
import os
from datetime import datetime

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade

def _float(s):
    s = (s or "").strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def screene_markets360():
    # Lokaler Lauf (Mac mini): Markets 360 schreibt dort schon direkt hin,
    # kein rclone/_magazine noetig. Cloud-Lauf (GitHub Actions): nur der per
    # rclone gezogene _magazine-Pfad existiert.
    pfad = pfade.LOKAL_MARKETS360 if os.path.exists(pfade.LOKAL_MARKETS360) else pfade.EXTERN_MARKETS360
    if not os.path.exists(pfad):
        print(f"Markets-360-Quelle nicht gefunden ({pfad}) - uebersprungen.")
        return []
    heute = datetime.now().strftime("%Y-%m-%d")
    signale = []
    with open(pfad, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            ticker = (row.get("Symbol") or "").strip().upper()
            if not ticker:
                continue
            fab5, rs, vcp = _float(row.get("Fab5")), _float(row.get("RS")), _float(row.get("VCP"))
            tt_pass = (row.get("TT_Pass") or "").strip().lower() == "true"
            teile = []
            if fab5 is not None:
                teile.append(f"Fab5 {fab5:.0f}")
            if rs is not None:
                teile.append(f"RS {rs:.0f}")
            if tt_pass:
                teile.append("Trend-Template ok")
            if vcp is not None:
                teile.append(f"VCP {vcp:.0f}")
            signale.append({
                "ticker": ticker, "name": (row.get("Name") or "").strip() or None,
                "exchange": None, "markt": None,
                "quelle_typ": "markets360", "quelle_datei": "markets360",
                "datum": heute, "seite": None,
                "kontext": "Markets 360: " + " · ".join(teile), "fund_art": "markets360",
            })
    return signale

def main():
    signale = screene_markets360()
    print(f"\n=== {len(signale)} Roh-Signale aus Markets 360 ===")
    print(", ".join(s["ticker"] for s in signale[:40]))
    ziel = pfade.RAW_MARKETS360
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(signale, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {ziel}")

if __name__ == "__main__":
    main()
