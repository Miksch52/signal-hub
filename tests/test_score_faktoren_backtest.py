#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die Pro-Faktor-Erfolgsanalyse
(Signal-Hub/src/score_faktoren_backtest.py).

Synthetische Logbuch-Episoden + gemockte Kursabrufe (kein Netz) -> prueft,
ob die gruen/nicht-gruen-Bucketing-Logik korrekt rechnet. Wichtig, weil sich
die echte Auswertung erst in ~6 Wochen (sobald Episoden reif sind) am
Live-Logbuch pruefen laesst - dieser Test faengt Logikfehler JETZT ab, nicht
erst wenn es zu spaet ist, das noch leicht zu korrigieren. Kein Server,
keine POSTs -> gefahrlos gegen die Live-Daten.

  python3 tests/test_score_faktoren_backtest.py
"""
import os
import sys
from datetime import datetime, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import score_faktoren_backtest as sfb
import scorer


def _mock_kurse(kurse, signal_datum):
    """Chart mit Datumsreihe bis gestern. kurse: symbol -> (Schlusskurs bis
    einschliesslich Signaltag, Schlusskurs danach). Seit 2026-09-13 messen die
    Forward-Tests feste Fenster ab dem Schlusskurs am Signaltag - der Chart
    braucht deshalb eine Datumsreihe und einen Kursverlauf ueber den Signaltag."""
    heute = datetime.now().date()
    tage = [(heute - timedelta(days=i)).isoformat() for i in range(120, 0, -1)]
    scorer.lade_cache = lambda: {}
    scorer.speichere_cache = lambda cache: None
    scorer.hole_chart_cached = lambda symbol, cache: {
        "closes": [kurse[symbol][0] if d <= signal_datum else kurse[symbol][1] for d in tage],
        "dates": tage}


def test_evaluiere_trennt_gruen_vs_nicht_gruen_korrekt():
    alt = (datetime.now().date() - timedelta(days=30)).strftime("%Y-%m-%d")
    # AAA hat stage2_trend gruen und gewinnt (+20%), BBB hat ihn rot und
    # verliert (-10%) -> edge_win sollte klar positiv sein.
    picks = [
        {"ticker": "AAA", "datum": alt, "preis": 100.0,
         "faktoren": {"stage2_trend": {"wert": 1.0, "ampel": "gruen"}}},
        {"ticker": "BBB", "datum": alt, "preis": 100.0,
         "faktoren": {"stage2_trend": {"wert": 0.2, "ampel": "rot"}}},
    ]
    _mock_kurse({"AAA": (100.0, 120.0), "BBB": (100.0, 90.0)}, alt)

    ergebnis, gewertet = sfb.evaluiere(picks)

    assert gewertet == 2, gewertet
    s = ergebnis["stage2_trend"]["4W"]
    assert s["gruen"]["n"] == 1 and s["gruen"]["win"] == 100.0, s["gruen"]
    assert s["nicht_gruen"]["n"] == 1 and s["nicht_gruen"]["win"] == 0.0, s["nicht_gruen"]
    assert s["edge_win"] == 100.0, s   # 100% - 0% = klarer Vorhersagewert
    # Faktoren ohne Nennung in den Picks bleiben leer (n=0), kein Crash.
    assert ergebnis["cmf"]["4W"]["gruen"]["n"] == 0
    print("ok  evaluiere trennt gruen/nicht-gruen korrekt, edge_win =", s["edge_win"])


def test_evaluiere_ohne_faktoren_detail_wird_uebersprungen():
    alt = (datetime.now().date() - timedelta(days=30)).strftime("%Y-%m-%d")
    picks = [{"ticker": "CCC", "datum": alt, "preis": 50.0, "faktoren": {}}]
    _mock_kurse({"CCC": (50.0, 55.0)}, alt)

    ergebnis, gewertet = sfb.evaluiere(picks)

    assert gewertet == 1                                          # Kurs wird trotzdem gewertet
    assert ergebnis["stage2_trend"]["4W"]["gruen"]["n"] == 0       # aber kein Faktor-Eimer befuellt
    print("ok  Episode ohne Faktoren-Detail befuellt keine Faktor-Eimer")


def test_evaluiere_zu_junge_episode_wird_ignoriert():
    heute = datetime.now().date().strftime("%Y-%m-%d")   # 0 Tage alt -> kein Horizont reif
    picks = [{"ticker": "DDD", "datum": heute, "preis": 10.0,
              "faktoren": {"stage2_trend": {"wert": 1.0, "ampel": "gruen"}}}]
    _mock_kurse({"DDD": (10.0, 12.0)}, heute)

    ergebnis, gewertet = sfb.evaluiere(picks)

    assert gewertet == 0, gewertet
    assert ergebnis["stage2_trend"]["4W"]["gruen"]["n"] == 0
    print("ok  zu junge Episode wird ignoriert (kein Horizont reif)")


if __name__ == "__main__":
    test_evaluiere_trennt_gruen_vs_nicht_gruen_korrekt()
    test_evaluiere_ohne_faktoren_detail_wird_uebersprungen()
    test_evaluiere_zu_junge_episode_wird_ignoriert()
    print("\nAlle Pro-Faktor-Backtest-Tests bestanden.")
