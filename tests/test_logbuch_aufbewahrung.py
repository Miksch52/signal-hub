#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest: Forward-Logbuecher werden nach ALTER gekuerzt, nicht nach
Eintragszahl (seit 2026-09-13).

Hintergrund: Sechs Engines kappten ihr Forward-Logbuch mit "lb = lb[-N:]".
Diese Kappe skalierte mit der Treffermenge - bei ~417 Hebel- bzw. ~172
Pivot-Eintraegen je Tag behielt sie in der Cloud nur 12 bzw. 29 Tage und
loeschte jede Episode, bevor sie den 4W-, 8W- oder 12W-Horizont erreichte.
Folge: Hebel-Backtest dauerhaft n=0, Pivot nie 8W/12W, Pivot-Episoden vom
02.07. bis 15.08.2026 unwiederbringlich verloren.

Zwei Pruefungen:
  1. statisch fuer alle sechs Engines: keine Kappe nach Eintragszahl mehr,
     Aufbewahrung 120 Tage gesetzt (verhindert, dass eine Kopie der alten
     Zeile irgendwo zurueckkommt),
  2. funktional am Katalysator-Backtest, dessen log_heute() sich ohne Netz
     vollstaendig in ein Wegwerfverzeichnis umleiten laesst (gleiches Muster
     wie tests/test_katalysator.py): 10.000 frische Eintraege bleiben
     vollstaendig, ein 130 Tage alter faellt heraus, ein 100 Tage alter bleibt.

Kein Netz, kein Server, keine POSTs -> gefahrlos gegen die Live-Daten.

  python3 tests/test_logbuch_aufbewahrung.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.join(HIER, "..", "..")

DATEIEN = [
    "Signal-Hub/src/pivot_backtest.py",
    "Signal-Hub/src/regime_backtest.py",
    "Price-Action-Hub/src/hebel_backtest.py",
    "Price-Action-Hub/src/muster_backtest.py",
    "Price-Action-Hub/src/katalysator_backtest.py",
    "Rotation-Dashboard/src/rotation_backtest.py",
]


def test_keine_kappe_nach_eintragszahl_mehr():
    for rel in DATEIEN:
        with open(os.path.join(WURZEL, rel), encoding="utf-8") as f:
            s = f.read()
        assert not re.search(r"lb\s*=\s*lb\[-\d+:\]", s), \
            f"{rel}: Kappe nach Eintragszahl ist zurueck"
        assert "aufbewahrung_tage = 120" in s, f"{rel}: Aufbewahrung nach Alter fehlt"
    print(f"ok  alle {len(DATEIEN)} Engines kuerzen nach Alter (120 Tage), keine lb[-N:]-Kappe mehr")


def test_katalysator_logbuch_behaelt_nach_alter():
    sys.path.insert(0, os.path.join(WURZEL, "Price-Action-Hub", "src"))
    import pfade
    import katalysator_backtest
    # Bewusst nicht "import kursdaten": Price-Action-Hub und Rotation-Dashboard
    # haben je ein gleichnamiges Modul (siehe tests/test_katalysator.py) - nur
    # das von katalysator_backtest gebundene Objekt ist sicher das richtige.
    kursdaten = katalysator_backtest.kursdaten

    with tempfile.TemporaryDirectory() as td:
        # Alle Pfade in ein Wegwerfverzeichnis (wie tests/test_katalysator.py) -
        # kein Test darf das echte lokale Logbuch beruehren.
        pfade.SIGNAL_HUB_TOP_SETUPS_JSON = os.path.join(td, "top_setups.json")
        pfade.KATALYSATOR_JSON = os.path.join(td, "top_setups_katalysator.json")
        pfade.KATALYSATOR_JS = os.path.join(td, "top_setups_katalysator.js")
        pfade.KATALYSATOR_BACKTEST = os.path.join(td, "katalysator_backtest.json")
        pfade.KATALYSATOR_BACKTEST_JS = os.path.join(td, "katalysator_backtest.js")
        pfade.KATALYSATOR_STATE = os.path.join(td, "katalysator_state.json")
        pfade.KATALYSATOR_LOGBUCH = os.path.join(td, "katalysator_logbuch.json")
        # Seit 2026-10-04 holt log_heute() die Index-Charts (belegter
        # Handelstag) - ohne Stub ginge das ins Netz und in den echten Cache.
        kursdaten.pfade.YAHOO_CACHE = os.path.join(td, "yahoo_cache.json")
        original_hole_chart = kursdaten.hole_chart_cached
        kursdaten.hole_chart_cached = lambda *a, **k: None

        jetzt = datetime.now()
        heute = jetzt.strftime("%Y-%m-%d")
        vor = lambda tage: (jetzt - timedelta(days=tage)).strftime("%Y-%m-%d")

        def _eintrag(datum, ticker):
            return {"datum": datum, "ticker": ticker, "markt": "USA",
                    "stufe": "ohne_katalysator", "klasse": "keiner", "preis_signal": 1.0}

        # Mehr frische Eintraege, als jede alte Kappe (5.000/8.000) je behalten haette
        vorbefuellt = ([_eintrag(vor(130), "ALT"), _eintrag(vor(100), "MITTEL")]
                       + [_eintrag(vor(1), f"T{i}") for i in range(10000)])
        json.dump(vorbefuellt, open(pfade.KATALYSATOR_LOGBUCH, "w"))

        json.dump({"setups": [{"ticker": "AMZN", "name": "Amazon", "markt": "USA",
                               "preis": 100.0}]},
                  open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        json.dump({"erstellt": heute, "modell": "sonar", "katalysatoren": {
            "AMZN": {"klasse": "zahlen", "text": "Zahlen geschlagen."}}},
            open(pfade.KATALYSATOR_JSON, "w"))

        try:
            katalysator_backtest.log_heute()
        finally:
            kursdaten.hole_chart_cached = original_hole_chart
        lb = json.load(open(pfade.KATALYSATOR_LOGBUCH))
        ticker = {e["ticker"] for e in lb}

        assert "ALT" not in ticker, "130 Tage alter Eintrag haette entfernt werden muessen"
        assert "MITTEL" in ticker, "100 Tage alter Eintrag muss erhalten bleiben"
        assert "AMZN" in ticker, "heutiger Eintrag fehlt"
        frische = sum(1 for e in lb if e["ticker"].startswith("T"))
        assert frische == 10000, f"frische Eintraege wurden gekappt: {frische} von 10000"
    print("ok  Katalysator-Logbuch: 10.000 frische bleiben, 130 Tage alt faellt, 100 Tage alt bleibt")


if __name__ == "__main__":
    test_keine_kappe_nach_eintragszahl_mehr()
    test_katalysator_logbuch_behaelt_nach_alter()
    print("\nAlle Logbuch-Aufbewahrungs-Tests bestanden.")
