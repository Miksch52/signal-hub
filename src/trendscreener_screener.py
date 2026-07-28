#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trend-Screener-Quelle fuer den Daily Signal Hub.

Liest die vom Lokalen-Trend-Screener (eigenes Repo
Miksch52/lokaler-trend-screener, GitHub Actions) erzeugte und nach R2
hochgeladene signals.json (vom signal-hub.yml-Workflow bereits nach
_magazine/trendscreener/ gezogen; Schema {markets:{us:{rows:[...]},
europe:{...}}, order, default, generated}) und schreibt die auffaelligen
Treffer (Leader oder Minervini-Setup) im gemeinsamen Rohsignal-Schema nach
data/signals_raw_trendscreener.json.

Fehlt die Datei (z. B. lokaler Testlauf ohne R2-Sync), wird sauber mit
0 Signalen beendet statt einen Fehler zu werfen.

Test:  python3 src/trendscreener_screener.py
"""

import json
import os
from datetime import datetime

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade

def screene_trendscreener():
    # Lokaler Lauf (Mac mini): der Trend-Screener schreibt dort schon direkt
    # hin, kein rclone/_magazine noetig. Cloud-Lauf (GitHub Actions): nur der
    # per rclone gezogene _magazine-Pfad existiert.
    pfad = pfade.LOKAL_TRENDSCREENER if os.path.exists(pfade.LOKAL_TRENDSCREENER) else pfade.EXTERN_TRENDSCREENER
    if not os.path.exists(pfad):
        print(f"Trend-Screener-Quelle nicht gefunden ({pfad}) - uebersprungen.")
        return []
    try:
        with open(pfad, encoding="utf-8") as f:
            hub = json.load(f)
    except Exception as e:
        print(f"Trend-Screener-Quelle nicht lesbar ({e}) - uebersprungen.")
        return []
    heute = datetime.now().strftime("%Y-%m-%d")
    signale = []
    for markt, payload in (hub.get("markets") or {}).items():
        for r in payload.get("rows", []):
            if not (r.get("leader") or r.get("minervini")):
                continue  # nur auffaellige Treffer, nicht das ganze Universum
            teile = []
            if r.get("mscore") is not None:
                teile.append(f"M-Score {r['mscore']}")
            if r.get("rs") is not None:
                teile.append(f"RS {r['rs']}")
            for flag, label in (("minervini", "Minervini-Setup"), ("code33", "Code 33"),
                                 ("vcp", "VCP"), ("darvas", "Darvas")):
                if r.get(flag):
                    teile.append(label)
            signale.append({
                "ticker": (r.get("symbol") or "").upper(), "name": None,
                "exchange": None, "markt": markt,
                "quelle_typ": "trendscreener", "quelle_datei": "trendscreener",
                "datum": heute, "seite": None,
                "kontext": "Trend-Screener: " + " · ".join(teile), "fund_art": "trendscreener",
            })
    return signale

def main():
    signale = screene_trendscreener()
    print(f"\n=== {len(signale)} Roh-Signale aus dem Trend-Screener ===")
    print(", ".join(s["ticker"] for s in signale[:40]))
    ziel = pfade.RAW_TRENDSCREENER
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(signale, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {ziel}")

if __name__ == "__main__":
    main()
