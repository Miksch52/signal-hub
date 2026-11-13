#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die Gewichts-Simulation (Signal-Hub/src/gewichte_sim.py,
Systempruefung Punkt 1).

Prueft die Bausteine, auf denen die Kalibrierentscheidung beruht:
Score-Rekonstruktion, faire Normierung bei fehlenden Faktoren, Einhaltung der
Leitplanken (kein Faktor auf 0, keiner ueber dem Doppelten), konstante
Gewichtssumme und ueberschneidungsfreie Out-of-Sample-Schnitte. Synthetische
Episoden, kein Netz, kein Server, keine POSTs -> gefahrlos gegen die
Live-Daten.

  python3 tests/test_gewichte_sim.py
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import gewichte_sim as gs

# "code33" ist einer der real ungemessenen Faktoren (gewichte_sim.UNGEMESSEN) -
# absichtlich mit echtem Namen, damit der Test dieselbe Sonderbehandlung trifft
# wie der Produktivlauf und nicht an einem Platzhalter vorbeiprueft.
BASIS = {"a": 20, "b": 10, "c": 6, "code33": 16}
# edge_win je Faktor, wie ihn score_faktoren_backtest.json liefert
EDGES = {"a": -6.0, "b": 0.0, "c": +5.0, "code33": None}


def _ep(datum, markt, edge, werte, horizont="4W"):
    return {"ticker": "X", "datum": datum, "markt": markt, "horizont": horizont,
            "tage": 30, "ret": edge, "edge": edge, "werte": werte}


def test_score_mit_rechnet_gewichtete_linearkombination():
    werte = {"a": 1.0, "b": 0.5, "c": 0.0}
    gew = {"a": 20, "b": 10, "c": 10}
    # (20*1,0 + 10*0,5 + 10*0,0) / 40 * 100 = 62,5
    assert gs.score_mit(werte, gew) == 62.5, gs.score_mit(werte, gew)
    print("ok  Score = gewichtete Linearkombination der Faktorwerte")


def test_normierung_ignoriert_fehlende_faktoren():
    """Alte Episoden kennen institutional_trend/code33 nicht. Wuerden sie mit
    der vollen Gewichtssumme im Nenner gerechnet, faenden sie die Schwelle 70
    nie - der Vergleich zwischen Varianten waere systematisch verzerrt."""
    werte = {"a": 1.0, "b": 1.0}          # "code33" fehlt
    gew = {"a": 20, "b": 10, "code33": 70}
    assert gs.score_mit(werte, gew) == 100.0, gs.score_mit(werte, gew)
    assert gs.score_mit({}, gew) is None
    print("ok  fehlende Faktoren werden aus dem Nenner genommen, nicht als 0 gewertet")


def test_leitplanken_werden_eingehalten():
    """Nutzerentscheidung: die Messung fuehrt, schreibt die Methodik aber nicht
    um - kein Faktor auf 0, keiner ueber dem Doppelten."""
    v = gs.varianten(BASIS, EDGES)
    for name in ("edge_gefuehrt", "edge_und_ungemessen"):
        for k, alt in BASIS.items():
            neu = v[name][k]
            assert neu > 0, f"{name}/{k} auf 0 gefallen: {neu}"
            # Normierung auf konstante Summe kann die Grenze leicht dehnen,
            # deshalb 5 % Toleranz auf die reine Faktor-Leitplanke.
            assert neu <= alt * gs.MAX_ANTEIL * 1.05, f"{name}/{k} ueber dem Doppelten: {neu}"
    print("ok  Leitplanken eingehalten (kein Faktor auf 0, keiner ueber dem Doppelten)")


def test_gewichtssumme_bleibt_konstant():
    """Sonst bedeutet die Schwelle 70 in jeder Variante etwas anderes."""
    v = gs.varianten(BASIS, EDGES)
    soll = sum(BASIS.values())
    for name, g in v.items():
        assert abs(sum(g.values()) - soll) < 0.5, (name, sum(g.values()))
    print(f"ok  alle Varianten behalten die Gewichtssumme ({soll})")


def test_edge_gefuehrt_sortiert_in_die_richtige_richtung():
    v = gs.varianten(BASIS, EDGES)["edge_gefuehrt"]
    # c hat den besten Edge (+5), a den schlechtesten (-6) -> c muss relativ
    # zu seinem Ausgangsgewicht staerker zulegen als a
    assert v["c"] / BASIS["c"] > v["a"] / BASIS["a"], v
    print("ok  edge_gefuehrt hebt den belegt besten Faktor relativ an")


def test_ungemessen_zurueck_stuft_nur_ungemessene_ab():
    v = gs.varianten(BASIS, EDGES)["ungemessen_zurueck"]
    assert v["code33"] < BASIS["code33"], v
    assert v["c"] > BASIS["c"], v      # frei werdende Punkte gehen an belegt positive
    print("ok  ungemessen_zurueck stuft nur Ungemessenes ab, verteilt an Belegtes")


def test_kontrollvariante_ist_gleichverteilt():
    v = gs.varianten(BASIS, EDGES)["gleichverteilt"]
    assert len(set(round(x, 1) for x in v.values())) == 1, v
    print("ok  Kontrollvariante gleichverteilt vorhanden")


def test_bewerte_trennt_nach_schwelle_und_nutzt_index_bezug():
    episoden = [
        _ep("2026-07-01", "USA", 0.10, {"a": 1.0, "b": 1.0, "c": 1.0}),   # Score 100 -> A
        _ep("2026-07-02", "USA", -0.05, {"a": 0.0, "b": 0.0, "c": 0.0}),  # Score 0   -> B
    ]
    res = gs.bewerte(episoden, {"a": 1, "b": 1, "c": 1}, 70)["4W"]
    assert res["A"]["n"] == 1 and res["B"]["n"] == 1, res
    assert res["A"]["avg"] == 10.0 and res["B"]["avg"] == -5.0, res
    assert res["trennschaerfe"] == 15.0, res
    # Episoden ohne Index-Bezug zaehlen nicht mit (sonst mischt sich eine
    # nicht index-bereinigte Zahl in die Kennziffer)
    ohne = [dict(episoden[0], edge=None)]
    assert gs.bewerte(ohne, {"a": 1}, 70) == {}
    print("ok  bewerte() trennt an der Schwelle und rechnet nur index-bereinigt")


def test_schnitte_sind_ueberschneidungsfrei():
    episoden = [_ep(f"2026-07-{d:02d}", "USA" if d % 2 else "Europa", 0.01,
                    {"a": 1.0}) for d in range(1, 21)]
    for name, (kal, pruef) in gs.schnitte(episoden).items():
        ids_k = {(e["datum"], e["markt"]) for e in kal}
        ids_p = {(e["datum"], e["markt"]) for e in pruef}
        assert not (ids_k & ids_p), f"{name}: Ueberschneidung {ids_k & ids_p}"
        assert len(kal) + len(pruef) == len(episoden), name
    print("ok  Zeit- und Markt-Schnitt sind ueberschneidungsfrei und vollstaendig")


if __name__ == "__main__":
    test_score_mit_rechnet_gewichtete_linearkombination()
    test_normierung_ignoriert_fehlende_faktoren()
    test_leitplanken_werden_eingehalten()
    test_gewichtssumme_bleibt_konstant()
    test_edge_gefuehrt_sortiert_in_die_richtige_richtung()
    test_ungemessen_zurueck_stuft_nur_ungemessene_ab()
    test_kontrollvariante_ist_gleichverteilt()
    test_bewerte_trennt_nach_schwelle_und_nutzt_index_bezug()
    test_schnitte_sind_ueberschneidungsfrei()
    print("\nAlle Gewichts-Simulations-Tests bestanden.")
