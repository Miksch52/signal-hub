#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer Price-Action-Hub-Musteranalyse + PA-Score
(Price-Action-Hub/src/muster.py, scorer.py::pa_score).

Synthetische OHLC-Reihen bzw. direkt konstruierte Musterergebnisse ->
erwartetes Verhalten. Kein Netz, kein Server, keine POSTs -> gefahrlos
gegen die Live-Daten. Deckt bisher UNGETESTETE Kernberechnungen ab
(Performance-Review 2026-08-02) - analog test_pivot.py.

  python3 tests/test_priceaction_muster.py
"""
import importlib.util
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Price-Action-Hub", "src"))
import muster

# Price-Action-Hub/src/scorer.py UNGLEICH Signal-Hub/src/scorer.py - ein
# normales "import scorer" wuerde unter dem generischen Namen "scorer" in
# sys.modules landen und damit fuer den Rest des pytest-Prozesses JEDEN
# spaeteren "import scorer" (z.B. in test_scorer_faktoren.py, das den
# Signal-Hub-Scorer braucht) auf dieses falsche Modul umlenken - mit genau
# den AttributeErrors, die dort dann wie ein Code-Defekt aussehen, obwohl es
# nur eine Testisolations-Luecke ist (gefunden bei der Pipeline-Einbindung,
# Systempruefung Punkt 10). Gezielt unter eigenem Namen laden, statt den
# gemeinsamen sys.modules-Cache zu verschmutzen.
_spec = importlib.util.spec_from_file_location(
    "pa_scorer", os.path.join(HIER, "..", "..", "Price-Action-Hub", "src", "scorer.py"))
pa_scorer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa_scorer)


def _gen_uptrend_ohlc(n=260, start=50.0, end=100.0):
    """Ruhiger Aufwaertstrend mit kleinem, konsistentem Tages-Range (h>l immer)."""
    closes = [start + (end - start) * i / (n - 1) for i in range(n)]
    opens, highs, lows, volumes = [], [], [], []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) * 1.003
        l = min(o, c) * 0.997
        opens.append(o)
        highs.append(h)
        lows.append(l)
        volumes.append(1000)
    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "volumes": volumes}


def test_analysiere_zu_kurz():
    ohlc = _gen_uptrend_ohlc(n=30)
    m = muster.analysiere(ohlc)
    assert m["status"] == "-", m
    print("ok  analysiere zu kurz     ", m["status"], m.get("grund"))


def test_analysiere_starke_bull_trendbar():
    ohlc = _gen_uptrend_ohlc()
    # Letzte Bar zu einer klaren starken Bull-Trendbar umbauen: Open nahe
    # Low, Close nahe High, grosser Body -> stark=True, richtung=bull.
    o = ohlc["closes"][-2] * 1.001
    hi = o * 1.05
    lo = o * 0.995
    c = hi * 0.999
    ohlc["opens"][-1], ohlc["highs"][-1] = o, hi
    ohlc["lows"][-1], ohlc["closes"][-1] = lo, c
    m = muster.analysiere(ohlc)
    assert m["status"] == "ok", m
    assert m["trend_bar"]["stark"] is True, m["trend_bar"]
    assert m["trend_bar"]["richtung"] == "bull", m["trend_bar"]
    # Sauberer, langer Aufwaertstrend -> Marktstadium sollte bullisch sein.
    assert m["marktstadium"]["stadium"] in ("Advancing", "Accumulation"), m["marktstadium"]
    print("ok  analysiere starke Bull-Trendbar", m["trend_bar"])


def test_pa_score_bullisch_im_trend_volle_gewichtung():
    m = {
        "status": "ok",
        "marktstadium": {"stadium": "Advancing"},
        "trend_bar": {"stark": True, "richtung": "bull"},
        "bar_counting": {"zuverlaessig": True, "typ": "High1"},
        "breakout": {"breakout_up": True, "failed": False, "breakout_down": False},
        "gap": {"gap_up": True, "gefuellt": False, "gap_down": False},
    }
    score = pa_scorer.pa_score(m)
    # trend_bar 2.0 + bar_counting 1.0 + breakout 2.0 + gap 1.0, alles im
    # bullischen Marktstadium -> keine Daempfung.
    assert score == 6.0, score
    print("ok  pa_score bullisch voll ", score)


def test_pa_score_gegen_trend_gedaempft():
    m = {
        "status": "ok",
        "marktstadium": {"stadium": "Declining"},          # baerisches Regime
        "trend_bar": {"stark": True, "richtung": "bull"},  # bullisches Signal GEGEN den Trend
        "bar_counting": {"zuverlaessig": False, "typ": None},
        "breakout": {"breakout_up": False, "breakout_down": False},
        "gap": {"gap_up": False, "gap_down": False},
    }
    score = pa_scorer.pa_score(m)
    assert score == 1.0, score   # 2.0 * 0.5 Daempfung (Gegentrend-Setup)
    print("ok  pa_score gedaempft     ", score)


def test_pa_score_status_nicht_ok():
    assert pa_scorer.pa_score({"status": "-"}) == 0.0
    print("ok  pa_score status!=ok    0.0")


if __name__ == "__main__":
    test_analysiere_zu_kurz()
    test_analysiere_starke_bull_trendbar()
    test_pa_score_bullisch_im_trend_volle_gewichtung()
    test_pa_score_gegen_trend_gedaempft()
    test_pa_score_status_nicht_ok()
    print("\nAlle Price-Action-Hub-Tests bestanden.")
