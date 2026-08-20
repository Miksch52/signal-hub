#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Taeglicher OHLC-Snapshot fuer das gescreente Universum (seit 2026-08-20).

Hintergrund (Top-Setups-Roadmap Punkt 4): Alle fuenf Screening-Engines
cachen Kursdaten bisher nur mit Tages-TTL auf dem jeweiligen Runner/Mac -
auf dem Cloud-Runner (GitHub Actions, pro Lauf frisch) heisst das: der
Yahoo-Cache faengt bei jedem der vier taeglichen Signal-Hub-Laeufe leer an
und wird am Lauf-Ende komplett verworfen. Es gibt bisher KEINE tatsaechlich
wachsende Kurshistorie im System - dieses Modul ist der erste Baustein
dafuer.

Kein zusaetzlicher Yahoo-Abruf: scorer.py hat pro Treffer bereits ein
126-Tage-Chartfenster in signals.json eingebettet (treffer[].chart.c/.v,
siehe scorer.py::_chartdaten) - hier wird nur der jeweils LETZTE Eintrag
(= aktuellster Handelstag) je Ticker herausgezogen und in eine kompakte,
datumsgestempelte CSV geschrieben. Klein pro Tag (~15 KB fuer das gesamte
Universum), waechst kontrolliert - Grundlage fuer einen spaeteren
Punkt-in-Zeit-Backtest ohne den im RETRO-Modus von pivot_backtest.py
dokumentierten Universums-Bias.

Aufruf: python3 src/ohlc_history.py (nach scorer.py, siehe run.py::pipeline).
Schreibt Signal-Hub/data/ohlc-history/JJJJ-MM-TT.csv (lokal + Cloud
identischer Pfad; die Cloud-Pipeline laedt die Datei zusaetzlich nach
r2:signalhub-magazine/ohlc-history/ hoch, siehe pipeline.yml und - fuer den
lokalen Mac-mini-Lauf - scripts/upload_ohlc_to_r2.sh).
"""

import csv
import os
from datetime import date

import pfade

SIGNALS = pfade.SIGNALS_JSON
OUT_DIR = pfade.OHLC_HISTORY_DIR


def schreibe():
    try:
        with open(SIGNALS, encoding="utf-8") as f:
            import json
            signals = json.load(f)
    except Exception as e:
        print(f"OHLC-Historie: signals.json nicht lesbar ({e}) - uebersprungen.")
        return False

    treffer = signals.get("treffer") or []
    if not treffer:
        print("OHLC-Historie: keine Treffer in signals.json - uebersprungen.")
        return False

    zeilen = []
    for t in treffer:
        chart = t.get("chart") or {}
        closes = chart.get("c") or []
        if not closes:
            continue
        vols = chart.get("v") or []
        zeilen.append((
            t.get("ticker"),
            closes[-1],
            (vols[-1] * 1000) if vols else "",
        ))
    if not zeilen:
        print("OHLC-Historie: kein Treffer mit Chartdaten - uebersprungen.")
        return False

    heute = date.today().isoformat()
    os.makedirs(OUT_DIR, exist_ok=True)
    pfad = os.path.join(OUT_DIR, f"{heute}.csv")
    with open(pfad, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "close", "volume"])
        w.writerows(zeilen)

    print(f"OHLC-Historie: {len(zeilen)} Ticker -> {pfad}")
    return True


if __name__ == "__main__":
    schreibe()
