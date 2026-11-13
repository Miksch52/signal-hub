#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest: Forward-Logbuecher fuehren den belegten Handelstag und zaehlen
jede Episode nur einmal (seit 2026-10-04).

Hintergrund: Alle sechs Engines schrieben als "datum" das Schreibdatum
(datetime.now()). Sa-, So- und Mo-Lauf sahen dieselben Freitagskurse, ebenso
ein Feiertag und der Tag danach - in der Auswertung starteten diese Eintraege
am selben Bar und zaehlten mit identischem Ergebnis mehrfach (Rotation ~24 %,
Pivot ~21 %, Muster ~15 %, Hebel ~26 %, Regime ~32 % der Episoden).
Die Rechenregeln selbst prueft tests/test_index_vergleich.py.

Zwei Pruefungen:
  1. statisch fuer alle sechs Engines: Log-Schluessel ueber
     index_vergleich.logbuch_schluessel, Startbar ueber start_datum,
     Dubletten-Filter ueber ist_dublette, Zaehler im Ausgabe-JSON - und keine
     Auswertung mehr direkt ab e["datum"],
  2. funktional am Katalysator-Backtest (ohne Netz, Wegwerfverzeichnis):
     der Eintrag traegt den Handelstag des Index-Charts, ein zweiter Lauf
     gegen denselben Handelstag legt nichts Neues an.

Kein Netz, kein Server, keine POSTs -> gefahrlos gegen die Live-Daten.

  python3 tests/test_logbuch_handelstag.py
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


def test_alle_engines_nutzen_handelstag_und_dubletten_filter():
    for rel in DATEIEN:
        with open(os.path.join(WURZEL, rel), encoding="utf-8") as f:
            s = f.read()
        for muss in ("index_vergleich.logbuch_schluessel(", "index_vergleich.start_datum(e)",
                     "index_vergleich.ist_dublette(", "dubletten_gefiltert",
                     '"handelstag"'):
            assert muss in s, f"{rel}: {muss} fehlt"
        assert not re.search(r'bekannt\s*=\s*\{\(e\["datum"\]', s), \
            f"{rel}: Log-Schluessel haengt wieder am Schreibdatum"
        assert not re.search(r'fenster_returns\([^)]*e\["datum"\]', s), \
            f"{rel}: Auswertung startet wieder ab dem Schreibdatum"
    print(f"ok  alle {len(DATEIEN)} Engines: Handelstag, Startbar, Dubletten-Filter")


def _fake_index(bis_einschl):
    """Werktags-Index bis einschliesslich bis_einschl (ISO-Datum)."""
    from datetime import date
    ende = date.fromisoformat(bis_einschl)
    d, dates = ende - timedelta(days=200), []
    while d <= ende:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return {"dates": dates, "closes": [100.0] * len(dates)}


def test_katalysator_logbuch_ein_eintrag_je_handelstag():
    sys.path.insert(0, os.path.join(WURZEL, "Price-Action-Hub", "src"))
    import katalysator_backtest
    # Ueber das Modul gehen statt "import pfade/kursdaten/index_vergleich":
    # gleichnamige Module liegen in mehreren Repos, im gemeinsamen pytest-Lauf
    # traefe ein frischer Import womoeglich das falsche (tests/test_katalysator.py).
    pfade = katalysator_backtest.pfade
    kursdaten = katalysator_backtest.kursdaten
    index_vergleich = katalysator_backtest.index_vergleich

    with tempfile.TemporaryDirectory() as td:
        pfade.SIGNAL_HUB_TOP_SETUPS_JSON = os.path.join(td, "top_setups.json")
        pfade.KATALYSATOR_JSON = os.path.join(td, "top_setups_katalysator.json")
        pfade.KATALYSATOR_LOGBUCH = os.path.join(td, "katalysator_logbuch.json")
        kursdaten.pfade.YAHOO_CACHE = os.path.join(td, "yahoo_cache.json")

        # Letzter Werktag vor heute = erwarteter Handelstag
        heute = datetime.now().date()
        erwartet = heute - timedelta(days=1)
        while erwartet.weekday() >= 5:
            erwartet -= timedelta(days=1)
        index = _fake_index(heute.isoformat())   # enthaelt evtl. den laufenden Tag - zaehlt nicht

        json.dump({"setups": [{"ticker": "AMZN", "markt": "USA", "preis": 100.0}]},
                  open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        json.dump({"katalysatoren": {"AMZN": {"klasse": "zahlen"}}},
                  open(pfade.KATALYSATOR_JSON, "w"))
        # Altbestand mit Logdatum == erwarteter Handelstag: darf NICHT blockieren
        json.dump([{"datum": erwartet.isoformat(), "ticker": "AMZN", "markt": "USA",
                    "stufe": "mit_katalysator", "klasse": "zahlen", "preis_signal": 99.0}],
                  open(pfade.KATALYSATOR_LOGBUCH, "w"))

        original = kursdaten.hole_chart_cached
        kursdaten.hole_chart_cached = lambda *a, **k: index
        try:
            katalysator_backtest.log_heute()
            katalysator_backtest.log_heute()   # zweiter Lauf, gleicher Handelstag
        finally:
            kursdaten.hole_chart_cached = original
        lb = json.load(open(pfade.KATALYSATOR_LOGBUCH))

    neu = [e for e in lb if e.get("handelstag")]
    assert len(lb) == 2, f"erwartet Altbestand + 1 neuer Eintrag, gefunden {len(lb)}"
    assert len(neu) == 1 and neu[0]["handelstag"] == erwartet.isoformat(), neu
    assert index_vergleich.start_datum(neu[0]) == (erwartet + timedelta(days=1)).isoformat()
    print("ok  Katalysator-Logbuch: Handelstag gesetzt, zweiter Lauf legt nichts doppelt an")


if __name__ == "__main__":
    test_alle_engines_nutzen_handelstag_und_dubletten_filter()
    test_katalysator_logbuch_ein_eintrag_je_handelstag()
    print("\nAlle Handelstag-Tests bestanden.")
