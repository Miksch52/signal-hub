"""Tests fuer das Blaettern in Signal-Hub/src/finviz_screener.py.

Hintergrund (Bugfix 2026-11-15): die Chart-Ansicht v=211 zeigt 12 Treffer je
Seite, geblaettert wurde aber fest in 20er-Schritten und hoechstens 3 Seiten -
es kamen maximal 36 Treffer an, mit Loechern. Laeuft ohne Netz: hole_seite
wird durch eine nachgestellte Finviz-Antwort ersetzt.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import finviz_screener as F  # noqa: E402


def _ticker(n):
    """T-Nummer n -> eindeutiges Symbol aus Buchstaben (AAA, AAB, ...)."""
    a = ord("A")
    return "Q" + chr(a + n // 26) + chr(a + n % 26)


def _falsches_finviz(gesamt, je_seite, abrufe):
    """Liefert eine hole_seite-Attrappe: zeigt ab r=<start> je_seite Treffer;
    hinter dem Ende wiederholt Finviz die letzte Seite."""
    alle = [_ticker(i) for i in range(gesamt)]

    def hole(url):
        abrufe.append(url)
        m = re.search(r"[?&]r=(\d+)", url)
        start = int(m.group(1)) if m else 1
        if start > gesamt:
            start = max(1, gesamt - je_seite + 1)
        seite = alle[start - 1:start - 1 + je_seite]
        # jeder Ticker steht (wie bei Finviz) mehrfach im HTML
        return "".join(f'<a href="quote.ashx?t={t}&ty=c">x</a><img src="chart.ashx?t={t}&p=d">'
                       for t in seite)
    return hole


@pytest.fixture(autouse=True)
def _ohne_warten(monkeypatch):
    monkeypatch.setattr(F.time, "sleep", lambda s: None)


URL = "https://finviz.com/screener?v=211&f=ind_stocksonly&o=-perf4w"


def test_chart_ansicht_12_je_seite_ohne_luecken(monkeypatch):
    abrufe = []
    monkeypatch.setattr(F, "hole_seite", _falsches_finviz(81, 12, abrufe))
    treffer = F.ticker_einer_url(URL)
    assert treffer == [_ticker(i) for i in range(81)]      # alle, in Reihenfolge
    starts = [int(m.group(1)) for m in (re.search(r"[?&]r=(\d+)", u) for u in abrufe) if m]
    assert starts[:3] == [13, 25, 37]                       # 12er-Schritte, nicht 21/41


def test_tabellen_ansicht_20_je_seite(monkeypatch):
    abrufe = []
    monkeypatch.setattr(F, "hole_seite", _falsches_finviz(45, 20, abrufe))
    assert len(F.ticker_einer_url(URL)) == 45
    assert "r=21" in abrufe[1] and "r=41" in abrufe[2]


def test_obergrenze_ist_trefferzahl(monkeypatch):
    abrufe = []
    monkeypatch.setattr(F, "hole_seite", _falsches_finviz(500, 12, abrufe))
    assert len(F.ticker_einer_url(URL, max_treffer=50)) == 50
    assert len(abrufe) == 5                                 # 12*5 = 60 >= 50


def test_drosselung_wird_einmal_wiederholt(monkeypatch):
    abrufe = []
    echt = _falsches_finviz(30, 12, abrufe)
    fehler = {"offen": 1}

    def hole(url):
        if "r=13" in url and fehler["offen"]:
            fehler["offen"] -= 1
            raise OSError("HTTP Error 429: Too Many Requests")
        return echt(url)
    monkeypatch.setattr(F, "hole_seite", hole)
    assert len(F.ticker_einer_url(URL)) == 30


def test_dauerhafter_fehler_behaelt_bisherige_treffer(monkeypatch):
    abrufe = []
    echt = _falsches_finviz(30, 12, abrufe)

    def hole(url):
        if "r=13" in url:
            raise OSError("HTTP Error 429: Too Many Requests")
        return echt(url)
    monkeypatch.setattr(F, "hole_seite", hole)
    assert F.ticker_einer_url(URL) == [_ticker(i) for i in range(12)]
