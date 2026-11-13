#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die gemeinsame Exit-Simulation
(Signal-Hub/src/exit_simulation.py, Systempruefung Punkt 4).

Konstruierte Kursverlaeufe mit bekanntem Ergebnis -> prueft die Konventionen,
auf denen die ganze Strategie-Spur beruht: Stop schlaegt Ziel am selben Tag,
Teilverkaeufe rechnen anteilig, der Horizont ist ein FESTER Stichtag, MFE/MAE
laufen auch nach dem Exit weiter. Kein Netz, kein Server, keine POSTs ->
gefahrlos gegen die Live-Daten.

  python3 tests/test_exit_simulation.py
"""
import os
import sys
from datetime import date, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import exit_simulation as ex


def _chart(bars, start="2026-01-01"):
    """bars: Liste von (high, low, close) ab dem Starttag, ein Bar je
    Kalendertag (fuer den Test genuegt das - die Simulation liest nur die
    Datumsreihe, nicht den Boersenkalender)."""
    d0 = date.fromisoformat(start)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(len(bars))]
    return {
        "dates": dates,
        "highs": [b[0] for b in bars],
        "lows": [b[1] for b in bars],
        "closes": [b[2] for b in bars],
    }


def _flach(tage, kurs=100.0):
    return [(kurs, kurs, kurs)] * tage


def test_stop_schlaegt_ziel_am_selben_tag():
    """Ein Tag, der T1 (+8 %) UND den Stop (-8 %) beruehrt, zaehlt als
    ausgestoppt - taeglich ist die Reihenfolge von Hoch und Tief unbekannt,
    die konservative Annahme ist Pflicht (sonst sieht die Staffel besser aus,
    als sie ist)."""
    bars = [(100.0, 100.0, 100.0)] + [(120.0, 90.0, 100.0)] + _flach(90)
    sim = ex.simuliere(_chart(bars), "2026-01-01", 100.0, 92.0)
    assert sim["gestoppt"] is True, sim
    assert sim["t1"] is False, sim
    assert sim["strategie_return"] == -8.0, sim
    print("ok  Stop schlaegt Ziel am selben Tag (konservativ)")


def test_staffel_rechnet_teilverkaeufe_anteilig():
    """Kurs laeuft ueber alle drei Ziele: 50 % zu +8 %, 25 % zu +20 %,
    25 % zu +40 % -> 0,5*8 + 0,25*20 + 0,25*40 = 19 %."""
    bars = [(100.0, 100.0, 100.0), (108.0, 100.0, 108.0), (120.0, 108.0, 120.0),
            (140.0, 120.0, 140.0)] + _flach(90, 140.0)
    sim = ex.simuliere(_chart(bars), "2026-01-01", 100.0, 92.0)
    assert (sim["t1"], sim["t2"], sim["t3"]) == (True, True, True), sim
    assert sim["gestoppt"] is False, sim
    assert sim["strategie_return"] == 19.0, sim
    print(f"ok  Staffel rechnet Teilverkaeufe anteilig ({sim['strategie_return']} %)")


def test_restposition_wird_zum_stichtag_bewertet():
    """Nur T1 erreicht -> 50 % zu +8 %, die restlichen 50 % zum Schlusskurs
    am festen Stichtag (hier +10 %) -> 0,5*8 + 0,5*10 = 9 %."""
    bars = [(100.0, 100.0, 100.0), (110.0, 100.0, 110.0)] + _flach(90, 110.0)
    sim = ex.simuliere(_chart(bars), "2026-01-01", 100.0, 92.0)
    assert sim["t1"] is True and sim["t2"] is False, sim
    assert sim["strategie_return"] == 9.0, sim
    assert sim["hold_return"] == 10.0, sim
    print("ok  offene Restposition wird zum Stichtag bewertet")


def test_horizont_nicht_erreicht_liefert_none():
    """Chart kuerzer als 78 Kalendertage -> keine Simulation (ein frueher
    abgeschnittener Hold-Vergleich waere unfair)."""
    sim = ex.simuliere(_chart(_flach(40)), "2026-01-01", 100.0, 92.0)
    assert sim is None, sim
    print("ok  zu kurzer Chart liefert None statt eines unfairen Vergleichs")


def test_mfe_mae_laufen_nach_dem_exit_weiter():
    """Nach dem Ausstoppen steigt der Kurs deutlich - genau das soll mfe
    zeigen ('der Stop war zu eng'), nicht beim Exit abgeschnitten werden."""
    bars = [(100.0, 100.0, 100.0), (100.0, 90.0, 91.0)] + [(150.0, 140.0, 150.0)] + _flach(90, 150.0)
    sim = ex.simuliere(_chart(bars), "2026-01-01", 100.0, 92.0)
    assert sim["gestoppt"] is True, sim
    assert sim["mfe"] == 50.0, sim      # Hoch 150 nach dem Stop
    assert sim["mae"] == -10.0, sim     # Tief 90 am Stop-Tag
    print("ok  mfe/mae laufen ueber das ganze Fenster, auch nach dem Exit")


def test_stop_aus_prozent_entspricht_den_echten_trades():
    """8 % unter Einstieg - der Abstand, den alle 252 realen Trades nutzen."""
    assert ex.stop_aus_prozent(100.0) == 92.0
    assert ex.stop_aus_prozent(None) is None
    print("ok  prozentualer Stop = 8 % unter Einstieg")


def test_unplausible_eingaben_liefern_none():
    chart = _chart(_flach(90))
    assert ex.simuliere(chart, "2026-01-01", 100.0, 100.0) is None   # Stop >= Entry
    assert ex.simuliere(chart, "2026-01-01", None, 92.0) is None     # kein Einstiegskurs
    assert ex.simuliere({}, "2026-01-01", 100.0, 92.0) is None       # kein Chart
    assert ex.simuliere(chart, "2027-01-01", 100.0, 92.0) is None    # Signaltag nach dem Chart
    print("ok  unplausible Eingaben liefern None statt einer Scheinzahl")


def test_aggregiere_kennzahlen():
    sims = [
        {"strategie_return": 10.0, "hold_return": 20.0, "t1": True, "t2": False,
         "t3": False, "gestoppt": False, "mfe": 25.0, "mae": -3.0},
        {"strategie_return": -8.0, "hold_return": -25.0, "t1": False, "t2": False,
         "t3": False, "gestoppt": True, "mfe": 9.0, "mae": -30.0},
    ]
    agg = ex.aggregiere(sims)
    assert agg["n"] == 2, agg
    assert agg["strategie"]["avg"] == 1.0, agg      # (10 - 8) / 2
    assert agg["hold"]["avg"] == -2.5, agg          # (20 - 25) / 2
    assert agg["vorsprung_avg"] == 3.5, agg
    assert agg["gestoppt_pct"] == 50.0, agg
    # Der gestoppte Trade lief spaeter noch ueber +8 % -> Stop war zu eng
    assert agg["gestoppt_mfe_ge_t1_pct"] == 50.0, agg
    assert ex.aggregiere([])["n"] == 0
    print("ok  Aggregation rechnet Kennzahlen und Vorsprung korrekt")


def test_einstieg_zum_signaltagsschluss_ab_folgetag_beobachtet():
    """Seit 2026-09-13: Einstieg zum Schlusskurs am Signaltag, nicht zum
    geloggten Preis. Hoch und Tief des Signaltags liegen VOR dem Einstieg und
    duerfen weder Stop noch Ziel ausloesen; der prozentuale Stop bezieht sich auf
    den tatsaechlichen Einstieg."""
    signaltag = [(130.0, 80.0, 100.0)]          # riesige Spanne, Schluss 100
    bars = signaltag + _flach(90, 100.0)
    sim = ex.simuliere(_chart(bars), "2026-01-01", 95.0, None, stop_pct=ex.STOP_PCT)
    assert sim["gestoppt"] is False and sim["t1"] is False, sim   # Tief 80 / Hoch 130 zaehlen nicht
    assert sim["hold_return"] == 0.0, sim                         # Basis 100, nicht 95
    # Stop 8 % unter 100 = 92: ein Folgetags-Tief von 92 loest ihn aus
    bars2 = signaltag + [(100.0, 92.0, 95.0)] + _flach(90, 95.0)
    sim2 = ex.simuliere(_chart(bars2), "2026-01-01", 95.0, None, stop_pct=ex.STOP_PCT)
    assert sim2["gestoppt"] is True and sim2["strategie_return"] == -8.0, sim2
    print("ok  Einstieg zum Signaltagsschluss, Stop/Ziel erst ab dem Folgetag")


if __name__ == "__main__":
    test_stop_schlaegt_ziel_am_selben_tag()
    test_staffel_rechnet_teilverkaeufe_anteilig()
    test_restposition_wird_zum_stichtag_bewertet()
    test_horizont_nicht_erreicht_liefert_none()
    test_mfe_mae_laufen_nach_dem_exit_weiter()
    test_stop_aus_prozent_entspricht_den_echten_trades()
    test_unplausible_eingaben_liefern_none()
    test_aggregiere_kennzahlen()
    test_einstieg_zum_signaltagsschluss_ab_folgetag_beobachtet()
    print("\nAlle Exit-Simulations-Tests bestanden.")
