#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die taegliche OHLC-Kurshistorie (Signal-Hub/src/ohlc_history.py).

Deckt den Schema-Umbau vom 2026-09-12 ab (Systempruefung, Punkt 3 der
Optimierungsliste: ticker,open,high,low,close,volume statt nur
ticker,close,volume - ohne Tageshoch/-tief laesst sich im Backtest nicht
rekonstruieren, ob ein Stop unterwegs gerissen worden waere). Synthetische
signals.json + ein temporaeres Ausgabeverzeichnis, kein Netz, keine POSTs ->
gefahrlos gegen die Live-Daten, analog test_katalysator.py.

  python3 tests/test_ohlc_history.py
"""
import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import ohlc_history as oh

BERLIN = ZoneInfo("Europe/Berlin")
# Weit nach Handelsschluss + innerhalb von MAX_BAR_ALTER_TAGE ab dem Bar-Datum.
JETZT = datetime(2026, 9, 12, 23, 0, tzinfo=BERLIN)


def _treffer(ticker, markt, bar_datum, o=None, h=None, l=None, c=100.0, v=1000, abg=None):
    chart = {"c": [c], "d": bar_datum, "abg": abg or "2026-09-11T23:00Z"}
    if v is not None:
        chart["v"] = [v]
    if o is not None:
        chart["o"] = [o]
    if h is not None:
        chart["h"] = [h]
    if l is not None:
        chart["l"] = [l]
    return {"ticker": ticker, "markt": markt, "chart": chart}


def test_zeilen_mit_vollen_ohlc_daten():
    signals = {"treffer": [
        _treffer("AAA", "USA", "2026-09-11", o=99.0, h=101.5, l=98.0, c=100.5, v=2000),
    ]}
    tage, zaehler = oh.zeilen_nach_handelstag(signals, JETZT)
    assert list(tage.keys()) == ["2026-09-11"], tage
    zeile = tage["2026-09-11"][0]
    assert zeile == {"ticker": "AAA", "open": 99.0, "high": 101.5, "low": 98.0,
                      "close": 100.5, "volume": 2000000}, zeile
    print("ok  volle o/h/l/c/v-Zeile wird korrekt aufgebaut")


def test_zeile_ohne_ohlc_bleibt_close_erhalten():
    """Ticker ohne volle Kerzenreihe (siehe scorer.py::_chartdaten) liefern nur
    c/v - o/h/l bleiben leer, die Zeile wird trotzdem geschrieben."""
    signals = {"treffer": [_treffer("BBB", "USA", "2026-09-11", c=50.0, v=500)]}
    tage, _ = oh.zeilen_nach_handelstag(signals, JETZT)
    zeile = tage["2026-09-11"][0]
    assert zeile["open"] == "" and zeile["high"] == "" and zeile["low"] == "", zeile
    assert zeile["close"] == 50.0 and zeile["volume"] == 500000, zeile
    print("ok  fehlendes o/h/l wirft die Zeile nicht weg (close/volume bleiben)")


def test_schreibt_neues_schema_in_frische_datei():
    with tempfile.TemporaryDirectory() as tmp:
        signals = {"treffer": [
            _treffer("AAA", "USA", "2026-09-11", o=99.0, h=101.5, l=98.0, c=100.5, v=2000),
        ]}
        pfad_signals = os.path.join(tmp, "signals.json")
        import json
        json.dump(signals, open(pfad_signals, "w"))
        out_dir = os.path.join(tmp, "ohlc-history")
        oh.schreibe(signals_pfad=pfad_signals, out_dir=out_dir, jetzt=JETZT)

        pfad = os.path.join(out_dir, "2026-09-11.csv")
        with open(pfad, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert list(rows[0].keys()) == oh.NEUE_SPALTEN, rows[0].keys()
        assert rows[0]["high"] == "101.5" and rows[0]["low"] == "98.0", rows[0]
        print("ok  frische Tagesdatei bekommt das neue 6-Spalten-Schema")


def test_anhaengen_an_altes_schema_mischt_keine_spalten():
    """Eine Tagesdatei, die (z.B. kurz vor diesem Deploy) noch im alten
    3-Spalten-Schema begonnen wurde, darf beim Anhaengen nicht auf 6 Spalten
    wechseln - das wuerde die CSV-Struktur dieser Datei kaputtmachen."""
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = os.path.join(tmp, "ohlc-history")
        os.makedirs(out_dir)
        pfad = os.path.join(out_dir, "2026-09-11.csv")
        with open(pfad, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(oh.ALTE_SPALTEN)
            w.writerow(["ZZZ", "10.0", "999"])

        signals = {"treffer": [
            _treffer("AAA", "USA", "2026-09-11", o=99.0, h=101.5, l=98.0, c=100.5, v=2000),
        ]}
        import json
        pfad_signals = os.path.join(tmp, "signals.json")
        json.dump(signals, open(pfad_signals, "w"))
        oh.schreibe(signals_pfad=pfad_signals, out_dir=out_dir, jetzt=JETZT)

        with open(pfad, encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == oh.ALTE_SPALTEN, rows[0]
        assert all(len(r) == 3 for r in rows[1:]), rows
        neue_zeile = [r for r in rows if r[0] == "AAA"][0]
        assert neue_zeile == ["AAA", "100.5", "2000000"], neue_zeile
        print("ok  Anhaengen an alte Tagesdatei uebernimmt deren Spaltenformat")


def test_bereits_vorhandener_ticker_wird_nicht_doppelt_geschrieben():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = os.path.join(tmp, "ohlc-history")
        os.makedirs(out_dir)
        pfad = os.path.join(out_dir, "2026-09-11.csv")
        with open(pfad, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(oh.NEUE_SPALTEN)
            w.writerow(["AAA", "1.0", "2.0", "0.5", "1.5", "100"])

        signals = {"treffer": [
            _treffer("AAA", "USA", "2026-09-11", o=99.0, h=101.5, l=98.0, c=100.5, v=2000),
        ]}
        import json
        pfad_signals = os.path.join(tmp, "signals.json")
        json.dump(signals, open(pfad_signals, "w"))
        oh.schreibe(signals_pfad=pfad_signals, out_dir=out_dir, jetzt=JETZT)

        with open(pfad, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1, rows
        assert rows[0]["close"] == "1.5", rows[0]   # erste Aufnahme bleibt, wird nicht ueberschrieben
        print("ok  bereits belegter Ticker/Tag wird nicht erneut geschrieben")


if __name__ == "__main__":
    test_zeilen_mit_vollen_ohlc_daten()
    test_zeile_ohne_ohlc_bleibt_close_erhalten()
    test_schreibt_neues_schema_in_frische_datei()
    test_anhaengen_an_altes_schema_mischt_keine_spalten()
    test_bereits_vorhandener_ticker_wird_nicht_doppelt_geschrieben()
    print("\nAlle OHLC-Historie-Tests bestanden.")
