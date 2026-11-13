#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die Morning-Brief-Erweiterung in notify.py
(_staerkste_kandidaten_zeilen, _earnings_depot_zeilen, Click-Header).

Kein Netz, kein echter Push - sende_ntfy() wird nie aufgerufen, pivot.json
wird auf ein temporaeres Verzeichnis umgebogen (nicht die echte lokale
Signal-Hub/data/pivot.json). Gefahrlos, analog test_katalysator.py.

  python3 tests/test_notify_morgens.py
"""
import json
import os
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import pfade
import notify


def _pivot_treffer_schreiben(treffer):
    json.dump({"treffer": treffer}, open(pfade.PIVOT_JSON, "w"))


def test_staerkste_kandidaten_nur_armed_breakout_und_qualifiziert():
    with tempfile.TemporaryDirectory() as td:
        pfade.PIVOT_JSON = os.path.join(td, "pivot.json")
        _pivot_treffer_schreiben([
            {"ticker": "NVDA", "pivot_status": "ARMED", "pivot": 180.0, "stop": 166.0},
            {"ticker": "AAPL", "pivot_status": "WATCH", "pivot": 200.0, "stop": 190.0},  # kein ARMED/BREAKOUT
            {"ticker": "MSFT", "pivot_status": "BREAKOUT", "pivot": 400.0, "stop": 380.0},
            {"ticker": "TSLA", "pivot_status": "ARMED", "pivot": 250.0, "stop": 230.0},  # nicht in qual (roter Markt)
        ])
        qual = {"NVDA": 82.0, "AAPL": 90.0, "MSFT": 75.0}  # TSLA fehlt bewusst
        zeilen = notify._staerkste_kandidaten_zeilen(qual, max_n=3)
        assert len(zeilen) == 2, zeilen   # AAPL faellt raus (kein ARMED/BREAKOUT), TSLA raus (nicht qualifiziert)
        assert "NVDA" in zeilen[0] and "82" in zeilen[0], zeilen[0]   # hoechster Score zuerst
        assert "MSFT" in zeilen[1], zeilen[1]
        assert "180.00" in zeilen[0] and "166.00" in zeilen[0]        # Pivot/Stop mitgeliefert
    print("ok  Staerkste Kandidaten -> nur ARMED/BREAKOUT + qualifiziert, Score-sortiert")


def test_staerkste_kandidaten_max_n_und_leere_datei():
    with tempfile.TemporaryDirectory() as td:
        pfade.PIVOT_JSON = os.path.join(td, "pivot.json")
        _pivot_treffer_schreiben([
            {"ticker": t, "pivot_status": "ARMED", "pivot": 100.0, "stop": 90.0}
            for t in ["A", "B", "C", "D", "E"]
        ])
        qual = {t: i for i, t in enumerate(["A", "B", "C", "D", "E"])}
        zeilen = notify._staerkste_kandidaten_zeilen(qual, max_n=3)
        assert len(zeilen) == 3, zeilen

        pfade.PIVOT_JSON = os.path.join(td, "nicht_vorhanden.json")
        assert notify._staerkste_kandidaten_zeilen(qual) == []
    print("ok  Staerkste Kandidaten -> Deckel max_n, fehlende Datei -> leer statt Fehler")


def test_earnings_depot_zeilen():
    with tempfile.TemporaryDirectory() as td:
        pfade.PIVOT_JSON = os.path.join(td, "pivot.json")
        _pivot_treffer_schreiben([
            {"ticker": "AMZN", "im_depot": True, "earnings": {"status": "termin", "tage": 3}},
            {"ticker": "GOOG", "im_depot": True, "earnings": {"status": "termin", "tage": 25}},   # ausserhalb Fenster
            {"ticker": "META", "im_depot": False, "earnings": {"status": "termin", "tage": 2}},   # nicht im Depot
            {"ticker": "SAP", "im_depot": True, "earnings": {"status": "kein_termin"}},           # kein Termin
            {"ticker": "NFLX", "im_depot": True, "earnings": {"status": "termin", "tage": 1}},
        ])
        zeilen = notify._earnings_depot_zeilen(warn_tage=10)
        assert len(zeilen) == 2, zeilen
        assert "NFLX" in zeilen[0] and "1T" in zeilen[0]   # frueheste zuerst
        assert "AMZN" in zeilen[1] and "3T" in zeilen[1]
    print("ok  Earnings-in-Depot   -> nur im_depot + Fenster, frueheste zuerst")


def test_click_header_wird_gesetzt():
    gesehen = {}

    class _FakeReq:
        def __init__(self, url, data=None, method=None):
            self.headers = {}
        def add_header(self, k, v):
            gesehen[k] = v

    class _FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import urllib.request
    orig_request, orig_urlopen = urllib.request.Request, urllib.request.urlopen
    urllib.request.Request = _FakeReq
    urllib.request.urlopen = lambda req, timeout=None, context=None: _FakeResp()
    try:
        notify.sende_ntfy("https://ntfy.sh", "thema", "Titel", "Text", click=notify.DASHBOARD_URL)
    finally:
        urllib.request.Request, urllib.request.urlopen = orig_request, orig_urlopen
    assert gesehen.get("Click") == notify.DASHBOARD_URL, gesehen
    print("ok  Click-Header        -> gesetzt, oeffnet das Dashboard")


if __name__ == "__main__":
    test_staerkste_kandidaten_nur_armed_breakout_und_qualifiziert()
    test_staerkste_kandidaten_max_n_und_leere_datei()
    test_earnings_depot_zeilen()
    test_click_header_wird_gesetzt()
    print("\nAlle Morning-Brief-Tests bestanden.")
