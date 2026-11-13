#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest: Umstellung auf feste Fenster loest keinen Push-Schwall aus
(Signal-Hub/src/pivot_backtest.py::push_reife, seit 2026-09-13).

Hintergrund: Seit dem Umbau auf feste Fenster zaehlt eine Episode in jedem
erreichten Horizont - die Kohorten 8W/12W fuellen sich beim ersten Lauf
schlagartig. push_reife() pusht, wenn eine Kohorte erstmals n >= 8 erreicht
oder n auf das 1,5-fache waechst. Unter dem alten Zustandsschluessel haette
allein der Methodenwechsel einen Schwall "Forward-Test reif"-Nachrichten an
alle Geraete geschickt. Der neue Schluessel wird deshalb beim ersten Lauf ohne
Push vorbelegt.

notify wird durch ein Attrappen-Modul ersetzt, der Zustand liegt in einem
Wegwerfverzeichnis - kein echter Push, keine Live-Datei beruehrt.

  python3 tests/test_pivot_push_fenster.py
"""
import json
import os
import sys
import tempfile
import types

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import pfade
import pivot_backtest as pb


def _fr(n):
    leer = {"n": 0, "win": None, "avg": None}
    return {"ARMED": {"4W": {"n": n, "win": 50.0, "avg": 1.0, "avg_R": 0.1},
                      "8W": dict(leer), "12W": dict(leer)}}


def test_umstellung_belegt_vor_und_pusht_erst_bei_echtem_wachstum():
    gesendet = []
    attrappe = types.ModuleType("notify")
    attrappe.lade_config = lambda: {"benachrichtigung": {
        "ntfy_thema": "test-thema", "ntfy_server": "https://example.invalid"}}
    attrappe.sende_ntfy = lambda *a, **k: gesendet.append(a)
    sys.modules["notify"] = attrappe

    with tempfile.TemporaryDirectory() as td:
        pfade.PIVOT_EVAL_STATE = os.path.join(td, "pivot_eval_state.json")
        # So sieht der Zustand in R2 am Umstellungstag aus: nur der alte Schluessel.
        json.dump({"gepusht": {"ARMED_4W": 10}}, open(pfade.PIVOT_EVAL_STATE, "w"))

        pb.push_reife(_fr(40))          # n vervierfacht - frueher ein Push
        assert gesendet == [], "erster Lauf nach der Umstellung darf nicht pushen"
        zustand = json.load(open(pfade.PIVOT_EVAL_STATE))
        assert zustand["gepusht_fenster"] == {"ARMED_4W": 40}, zustand
        assert zustand["gepusht"] == {"ARMED_4W": 10}, "alter Schluessel bleibt unangetastet"

        pb.push_reife(_fr(50))          # 50 < 1,5 * 40
        assert gesendet == [], gesendet

        pb.push_reife(_fr(60))          # 60 >= 1,5 * 40 -> echtes Wachstum
        assert len(gesendet) == 1, gesendet
        assert json.load(open(pfade.PIVOT_EVAL_STATE))["gepusht_fenster"]["ARMED_4W"] == 60
    print("ok  Umstellung belegt den Push-Zustand vor; gepusht wird erst bei echtem Wachstum")


if __name__ == "__main__":
    test_umstellung_belegt_vor_und_pusht_erst_bei_echtem_wachstum()
    print("\nAlle Push-Tests zu festen Fenstern bestanden.")
