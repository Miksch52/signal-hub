#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer das Wochen-Dossier (Signal-Hub/src/wochen_dossier.py).

Konstruierte Minimaldaten statt echter Engine-Ausgaben - kein Netz, keine
Dateien. Geprueft werden die vier Dinge, bei denen ein stiller Fehler teuer
waere:

  1. Reihenfolge  Kern-Setup -> Konfluenz -> Abstand zum Pivot
  2. Markierungen dieselben Grenzen wie agenten/pre_trade_check.py, und sie
                  sortieren NIE etwas aus (MPC-Fall: Stop 16.5 % bleibt drin)
  3. Datenschutz  kein im_depot in der Ausgabe (das Dossier liegt oeffentlich)
  4. Datenstand   fehlende/veraltete Quellen werden gemeldet, und die KW
                  kommt vom belegten Datenstand, nicht vom Schreibtag

  python3 -m pytest tests/test_wochen_dossier.py -q
"""
import json
import os
import sys
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import wochen_dossier as wd

JETZT = datetime(2026, 9, 19, 22, 0, tzinfo=timezone.utc)   # Samstagfrueh Berlin
STAND = "2026-09-19T19:54:46+00:00"                          # Freitags-Schlusskurse


def top_setup(ticker, **kw):
    s = {"ticker": ticker, "name": f"{ticker} AG", "markt": "USA", "preis": 100.0,
         "score": 80.0, "pivot_status": "ARMED", "pivot": 101.0, "stop": 95.0,
         "qualitaet": 70.0, "earnings_tage": None, "im_depot": False,
         "quellen_unabhaengig": 1, "kern_setup": False, "pa_score": 1.0,
         "trend_template": {"stage2_trend": "gruen"},
         "backtest": {"win": 27.4, "n": 2290, "horizont": "4W", "reif": True}}
    s.update(kw)
    return s


def pivot(ticker, **kw):
    p = {"ticker": ticker, "dist_pct": 1.0, "stop_pct": 5.0, "stop_warnung": False,
         "chance_risiko": 2.0, "detail": "Basis 6 %"}
    p.update(kw)
    return p


def daten(setups, pivots=None, **rest):
    d = {
        "top_setups": {"erstellt": STAND, "setups": setups,
                       "marktregime": {"USA": {"ampel": "gruen", "hinweis": "Aufwärtstrend"}}},
        "signals": {"erstellt": STAND, "treffer": []},
        "pivot": {"erstellt": STAND, "treffer": pivots if pivots is not None else
                  [pivot(s["ticker"]) for s in setups], "zaehler": {"ARMED": 12}},
        "katalysator": {"erstellt": STAND, "katalysatoren": {}},
        "priceaction": {"erstellt": STAND, "treffer": []},
        "rotation": {"generated": "2026-09-19 21:54", "sectors": [], "leaders": [], "regime": {}},
    }
    d.update(rest)
    return d


# ------------------------------------------------------------ 1. Reihenfolge

def test_reihenfolge_kern_setup_vor_konfluenz_vor_pivotabstand():
    d = daten(
        [top_setup("AAA", quellen_unabhaengig=4), top_setup("BBB", quellen_unabhaengig=2),
         top_setup("CCC", quellen_unabhaengig=2), top_setup("DDD", quellen_unabhaengig=1, kern_setup=True)],
        [pivot("AAA", dist_pct=3.4), pivot("BBB", dist_pct=6.0),
         pivot("CCC", dist_pct=0.5), pivot("DDD", dist_pct=9.0)],
    )
    k, n = wd.kandidaten(d, warn_tage=7)
    assert [x["ticker"] for x in k] == ["DDD", "AAA", "CCC", "BBB"]
    assert n == 4


def test_hoechstens_acht_kandidaten():
    d = daten([top_setup(f"T{i:02d}") for i in range(12)])
    k, n = wd.kandidaten(d, warn_tage=7)
    assert len(k) == wd.MAX_KANDIDATEN == 8
    assert n == 12


def test_fehlender_pivot_eintrag_sortiert_nach_hinten_statt_zu_krachen():
    d = daten([top_setup("AAA"), top_setup("OHNE")], [pivot("AAA", dist_pct=5.0)])
    k, _ = wd.kandidaten(d, warn_tage=7)
    assert [x["ticker"] for x in k] == ["AAA", "OHNE"]
    assert k[1]["stop_pct"] is None and k[1]["warnungen"] == []


# ----------------------------------------------------------- 2. Markierungen

def test_stop_grenzen_wie_im_pre_trade_pruefer():
    d = daten([top_setup("WEIT"), top_setup("WARN"), top_setup("OK")],
              [pivot("WEIT", stop_pct=16.5), pivot("WARN", stop_pct=9.0), pivot("OK", stop_pct=5.0)])
    k = {x["ticker"]: x for x in wd.kandidaten(d, warn_tage=7)[0]}
    assert [w["stufe"] for w in k["WEIT"]["warnungen"]] == ["stop"]
    assert "über 10" in k["WEIT"]["warnungen"][0]["text"]
    assert [w["stufe"] for w in k["WARN"]["warnungen"]] == ["warn"]
    assert k["OK"]["warnungen"] == []


def test_markierung_sortiert_nie_aus():
    """Der MPC-Fall vom 19.09.: hoechste Konfluenz, aber 16.5 % Stop."""
    d = daten([top_setup("MPC", quellen_unabhaengig=4), top_setup("BRAV", quellen_unabhaengig=1)],
              [pivot("MPC", stop_pct=16.5, stop_warnung=True), pivot("BRAV")])
    k, _ = wd.kandidaten(d, warn_tage=7)
    assert k[0]["ticker"] == "MPC"
    assert len(k[0]["warnungen"]) == 2


def test_extended_und_earnings_markierung():
    d = daten([top_setup("EXT", earnings_tage=3)], [pivot("EXT", dist_pct=6.9)])
    texte = [w["text"] for w in wd.kandidaten(d, warn_tage=7)[0][0]["warnungen"]]
    assert any("über Pivot" in t for t in texte)
    assert any("Earnings in 3" in t for t in texte)
    # Grenze aus config.json: bei warn_tage=2 ist derselbe Termin keine Warnung
    texte2 = [w["text"] for w in wd.kandidaten(d, warn_tage=2)[0][0]["warnungen"]]
    assert not any("Earnings" in t for t in texte2)


def test_rote_ampel_markiert_kaufpause():
    d = daten([top_setup("AAA")])
    d["top_setups"]["marktregime"]["USA"] = {"ampel": "rot", "hinweis": "Abwärtstrend"}
    k, _ = wd.kandidaten(d, warn_tage=7)
    assert k[0]["warnungen"][0]["stufe"] == "stop"
    dossier = wd.baue_dossier(d, jetzt=JETZT)
    assert dossier["marktlage"]["maerkte"]["USA"]["kaufpause"] is True
    assert "Kaufpause" in wd.push_text(dossier)[1]


# ------------------------------------------------------------ 3. Datenschutz

def test_kein_depotfeld_in_der_ausgabe():
    d = daten([top_setup("AAA", im_depot=True)])
    d["signals"]["treffer"] = [{"ticker": "AAA", "im_depot": True, "sektor": "Tech"}]
    roh = json.dumps(wd.baue_dossier(d, jetzt=JETZT), ensure_ascii=False)
    assert "im_depot" not in roh
    assert "Tech" in roh          # uebrige Signal-Felder kommen weiterhin mit


def test_katalysator_bleibt_gekennzeichneter_fremdinhalt():
    d = daten([top_setup("AAA")])
    d["katalysator"]["katalysatoren"] = {"AAA": {
        "klasse": "uebernahme", "text": "X" * 400,
        "quellen": ["https://www.reuters.com/a", "javascript:boese"], "stand": STAND}}
    kat = wd.kandidaten(d, warn_tage=7)[0][0]["katalysator"]
    assert kat["fremdinhalt"] is True
    assert len(kat["text"]) == wd.MAX_TEXT_LAENGE == 280
    assert kat["quellen"] == ["https://www.reuters.com/a"]


# ------------------------------------------------------- 4. Stand & Vergleich

def test_kalenderwoche_kommt_vom_datenstand_nicht_vom_schreibtag():
    d = daten([top_setup("AAA")])
    # Schreibtag Montag der Folgewoche, Daten weiterhin von Freitag/KW38
    assert wd.baue_dossier(d, jetzt=datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc))["kw"] == "2026-KW38"


def test_datenstand_meldet_fehlende_und_veraltete_quellen():
    d = daten([top_setup("AAA")])
    d["rotation"] = None
    d["katalysator"]["erstellt"] = "2026-09-15T05:00:00+00:00"
    stand = wd.baue_dossier(d, jetzt=JETZT)["datenstand"]
    assert stand["rotation"]["fehlt"] is True
    assert stand["katalysator"]["veraltet"] is True
    assert stand["top_setups"]["veraltet"] is False


def test_vergleich_zur_vorwoche():
    d = daten([top_setup("NEU"), top_setup("BLEIBT")])
    vorwoche = {"kw": "2026-KW37", "kandidaten": [{"ticker": "BLEIBT", "name": "BLEIBT AG"},
                                                  {"ticker": "WEG", "name": "Weg AG"}]}
    v = wd.baue_dossier(d, vorwoche=vorwoche, jetzt=JETZT)["vergleich"]
    assert v["neu"] == ["NEU"] and v["geblieben"] == ["BLEIBT"]
    assert v["raus"] == [{"ticker": "WEG", "name": "Weg AG"}]
    assert v["vorwoche_kw"] == "2026-KW37"


def test_ohne_vorwoche_kein_vergleich():
    assert wd.baue_dossier(daten([top_setup("AAA")]), jetzt=JETZT)["vergleich"] is None


# ------------------------------------------------------------- Price-Action

def test_musternamen():
    d = daten([top_setup("AAA")])
    d["priceaction"]["treffer"] = [{"ticker": "AAA", "muster": {
        "status": "ok",
        "trend_bar": {"stark": True, "richtung": "bull"},
        "inside_bar": {"inside": True},
        "breakout": {"breakout_up": True, "failed": False},
        "gap": {"gap_up": True, "gap_pct": 1.23, "gefuellt": False},
        "bar_counting": {"typ": "High2", "zuverlaessig": True}}}]
    muster = wd.kandidaten(d, warn_tage=7)[0][0]["pa_muster"]
    assert muster == ["starke Trendbar ↑", "Inside Bar", "Breakout ↑", "Gap ↑ 1.2 %", "High2"]


def test_unzuverlaessiges_bar_counting_und_fehlerstatus():
    d = daten([top_setup("AAA"), top_setup("BBB")])
    d["priceaction"]["treffer"] = [
        {"ticker": "AAA", "muster": {"status": "ok", "bar_counting": {"typ": "High3+", "zuverlaessig": False}}},
        {"ticker": "BBB", "muster": {"status": "zu_wenig_daten"}}]
    k = {x["ticker"]: x for x in wd.kandidaten(d, warn_tage=7)[0]}
    assert k["AAA"]["pa_muster"] == [] and k["BBB"]["pa_muster"] == []
