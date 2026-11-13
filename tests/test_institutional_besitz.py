#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer den Info-Indikator "Institutioneller Besitz"
(Signal-Hub/src/scorer.py::f_institutional + Hilfsfunktionen).

Kein Netz: der Yahoo-Abruf wird durch Attrappen ersetzt, der Cache liegt in
einem Temp-Ordner. Deckt ab: fehlende Daten, Wert > 100 %, alle drei
Groessenklassen, pctChange-Zaehlung, 1x-pro-Tag-Cache und dass Score/Tier/
Faktoren unberuehrt bleiben.

  python3 tests/test_institutional_besitz.py
"""
import copy
import os
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import pfade
import scorer


def qs(held=None, flo=None, anzahl=None, mcap=None, waehrung="USD", pct_changes=None):
    """Baut ein quoteSummary-result[0] wie Yahoo es liefert."""
    r = lambda v: {"raw": v} if v is not None else {}
    liste = []
    for p in (pct_changes or []):
        h = {"organization": "X"}
        if p is not None:
            h["pctChange"] = {"raw": p}
        liste.append(h)
    return {
        "majorHoldersBreakdown": {"institutionsPercentHeld": r(held),
                                  "institutionsFloatPercentHeld": r(flo),
                                  "institutionsCount": r(anzahl)},
        "institutionOwnership": {"ownershipList": liste},
        "price": {"marketCap": r(mcap), "currency": waehrung},
    }


def eintrag(roh):
    d = scorer.parse_institutional(roh)
    d["marktkap_usd"] = d["marktkap"]
    return d


# --- Fehlende Daten -----------------------------------------------------------

def test_fehlende_daten_na_ohne_fehler():
    for roh in ({}, None, qs(), {"majorHoldersBreakdown": None, "price": None}):
        i = scorer.baue_institutional(eintrag(roh))
        assert i["verfuegbar"] is False, i
        assert i["prozent"] is None and i["prozent_float"] is None and i["anzahl"] is None, i
        assert i["label"] == "n/a", i
        assert i["top_holder"] is None, i
    # auch ganz ohne Cache-Eintrag
    assert scorer.baue_institutional(None)["label"] == "n/a"
    print("ok  fehlende Daten -> n/a")


def test_wert_ohne_marktkap_label_na():
    i = scorer.baue_institutional(eintrag(qs(held=0.5, flo=0.55, anzahl=100)))
    assert i["prozent"] == 50.0 and i["groesse"] is None and i["label"] == "n/a", i
    print("ok  ohne Market Cap -> Label n/a, Werte trotzdem sichtbar")


# --- Werte > 100 % ------------------------------------------------------------

def test_ueber_100_unplausibel_nicht_gekappt():
    i = scorer.baue_institutional(eintrag(qs(held=1.054, flo=1.12, anzahl=300, mcap=5e9)))
    assert i["prozent"] == 105.4, i          # nicht gekappt
    assert i["prozent_float"] == 112.0, i
    assert i["ueber_100"] is True and i["float_ueber_100"] is True, i
    assert i["label"] == "Daten unplausibel", i
    print("ok  >100 % -> Daten unplausibel, Wert bleibt 105,4")


def test_nur_float_ueber_100():
    # CELH-Muster: 75,2 % gehalten, 105,8 % vom Float
    i = scorer.baue_institutional(eintrag(qs(held=0.7521, flo=1.05769, anzahl=720, mcap=6.99e9)))
    assert i["float_ueber_100"] is True and i["ueber_100"] is False, i
    assert i["label"] == "hoch", i           # Mid Cap, > 75 %
    assert i["hinweis"] == "Daten unplausibel (über 100 %)", i
    print("ok  nur Float >100 % -> Float markiert, Label aus % gehalten")


# --- Groessenklassen ----------------------------------------------------------

def test_small_cap_schwellen():
    assert scorer.groessenklasse(1.5e9) == "small"
    for held, erwartet in ((0.15, "niedrig"), (0.20, "normal"), (0.60, "normal"), (0.65, "hoch")):
        i = scorer.baue_institutional(eintrag(qs(held=held, mcap=1.5e9)))
        assert i["groesse"] == "small" and i["label"] == erwartet, (held, i)
    print("ok  Small Cap  <20 niedrig / >60 hoch")


def test_mid_cap_schwellen():
    assert scorer.groessenklasse(2e9) == "mid"
    assert scorer.groessenklasse(10e9) == "mid"
    for held, erwartet in ((0.39, "niedrig"), (0.40, "normal"), (0.75, "normal"), (0.76, "hoch")):
        i = scorer.baue_institutional(eintrag(qs(held=held, mcap=5e9)))
        assert i["groesse"] == "mid" and i["label"] == erwartet, (held, i)
    print("ok  Mid Cap    <40 niedrig / >75 hoch")


def test_large_cap_schwellen():
    assert scorer.groessenklasse(10.1e9) == "large"
    for held, erwartet in ((0.54, "niedrig"), (0.6634, "normal"), (0.85, "normal"), (0.86, "hoch")):
        i = scorer.baue_institutional(eintrag(qs(held=held, anzahl=7759, mcap=4.8e12)))
        assert i["groesse"] == "large" and i["label"] == erwartet, (held, i)
        assert i["groesse_label"] == "Large Cap" and i["schwelle_niedrig"] == 55 and i["schwelle_hoch"] == 85
    print("ok  Large Cap  <55 niedrig / >85 hoch")


def test_schwellen_sind_konstanten():
    alt = copy.deepcopy(scorer.INSTITUTIONAL_SCHWELLEN)
    try:
        scorer.INSTITUTIONAL_SCHWELLEN["large"] = (70, 90)
        assert scorer.institutional_label(66.0, "large") == "niedrig"
    finally:
        scorer.INSTITUTIONAL_SCHWELLEN.clear(); scorer.INSTITUTIONAL_SCHWELLEN.update(alt)
    print("ok  Schwellen per Konstante steuerbar")


# --- Top-Holder-Trend ---------------------------------------------------------

def test_pct_change_zaehlung():
    d = eintrag(qs(held=0.6, mcap=3e9, pct_changes=[0.016, 1.0, -0.25, 0.0, None, -0.92]))
    assert (d["aufgestockt"], d["reduziert"], d["top_mit_pct"], d["top_n"]) == (2, 2, 5, 6), d
    th = scorer.baue_institutional(d)["top_holder"]
    assert th == {"aufgestockt": 2, "reduziert": 2, "anzahl": 5}, th
    print("ok  pctChange: 2 aufgestockt, 2 reduziert")


def test_ohne_pct_change_spalte_kein_trend():
    d = eintrag(qs(held=0.6, mcap=3e9, pct_changes=[None, None]))
    assert scorer.baue_institutional(d)["top_holder"] is None
    print("ok  ohne pctChange-Spalte kein Trend-Hinweis")


# --- Waehrung -----------------------------------------------------------------

def test_gbp_pence_alias():
    memo = {"GBP": 1.35}
    assert scorer.yahoo_fx_usd("GBp", None, memo) == 1.35
    assert scorer.yahoo_fx_usd("USD", None, {}) == 1.0
    assert scorer.yahoo_fx_usd(None, None, {}) is None
    print("ok  GBp -> GBP, USD = 1")


# --- Post-Pass: Cache 1x/Tag, kein Score-Einfluss -----------------------------

def test_f_institutional_cache_und_kein_score_einfluss():
    alt_pfad, alt_abruf, alt_sleep = pfade.INSTITUTIONAL_CACHE, scorer.yahoo_institutional, scorer.time.sleep
    aufrufe = []

    def attrappe(symbol, op, crumb, fx_memo=None):
        aufrufe.append(symbol)
        daten = {
            "SMALL": qs(held=0.10, flo=0.12, anzahl=40, mcap=8e8),
            "MID": qs(held=1.02, flo=1.10, anzahl=200, mcap=4e9, pct_changes=[0.1, -0.1, 0.2]),
            "LARGE": qs(held=0.66, flo=0.67, anzahl=7759, mcap=4.8e12),
            "ETF": qs(),
        }[symbol]
        d = scorer.parse_institutional(daten)
        d["marktkap_usd"] = d["marktkap"]
        return d

    with tempfile.TemporaryDirectory() as tmp:
        pfade.INSTITUTIONAL_CACHE = os.path.join(tmp, "institutional_cache.json")
        scorer.yahoo_institutional = attrappe
        scorer.time.sleep = lambda s: None
        try:
            erg = [{"yahoo_symbol": s, "score": 80, "tier": "A", "einstufung": "Kauf-Kandidat",
                    "faktoren": {"x": {"wert": 1}}} for s in ("SMALL", "MID", "LARGE", "ETF")]
            erg.append({"yahoo_symbol": "UNTER", "score": 10, "tier": "C", "einstufung": "—", "faktoren": {}})
            vorher = copy.deepcopy(erg)
            schw = {"beobachten": 50, "kauf_kandidat": 70}

            scorer.f_institutional(erg, object(), "crumb", schw)
            assert sorted(aufrufe) == ["ETF", "LARGE", "MID", "SMALL"], aufrufe   # UNTER nicht abgerufen
            for a, b in zip(vorher, erg):
                for k in ("score", "tier", "einstufung", "faktoren"):
                    assert a[k] == b[k], (k, a, b)
            lab = {e["yahoo_symbol"]: e["institutional"]["label"] for e in erg}
            assert lab == {"SMALL": "niedrig", "MID": "Daten unplausibel", "LARGE": "normal",
                           "ETF": "n/a", "UNTER": "n/a"}, lab
            assert erg[1]["institutional"]["prozent"] == 102.0
            assert erg[1]["institutional"]["top_holder"] == {"aufgestockt": 2, "reduziert": 1, "anzahl": 3}

            # Zweiter Lauf am selben Tag: kein erneuter Abruf
            aufrufe.clear()
            erg2 = copy.deepcopy(vorher)
            scorer.f_institutional(erg2, object(), "crumb", schw)
            assert aufrufe == [], aufrufe
            assert erg2[2]["institutional"]["label"] == "normal"
        finally:
            pfade.INSTITUTIONAL_CACHE = alt_pfad
            scorer.yahoo_institutional = alt_abruf
            scorer.time.sleep = alt_sleep
    print("ok  f_institutional: 1x/Tag, Score/Tier/Faktoren unberuehrt")


if __name__ == "__main__":
    test_fehlende_daten_na_ohne_fehler()
    test_wert_ohne_marktkap_label_na()
    test_ueber_100_unplausibel_nicht_gekappt()
    test_nur_float_ueber_100()
    test_small_cap_schwellen()
    test_mid_cap_schwellen()
    test_large_cap_schwellen()
    test_schwellen_sind_konstanten()
    test_pct_change_zaehlung()
    test_ohne_pct_change_spalte_kein_trend()
    test_gbp_pence_alias()
    test_f_institutional_cache_und_kein_score_einfluss()
    print("\nAlle Tests fuer Institutionellen Besitz bestanden.")
