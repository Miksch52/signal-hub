#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fremdtext-Filter des Scorers (Signal-Hub/src/scorer.py::fremdtext, seit
2026-11-21, Sicherheitspruefung).

Firmennamen (Yahoo) und Betreffzeilen aus Screener-Mails gehen ueber
signals.json in Seiten auf mts-hub.pages.dev, die mit innerHTML arbeiten -
dieselbe Herkunft haelt den Gist-Token im Browser-Speicher. HTML-wirksame
Zeichen muessen deshalb schon im Scorer verschwinden.

  python3 -m pytest Signal-Hub/tests/test_fremdtext.py
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import scorer  # noqa: E402


def test_html_wird_entschaerft():
    t = scorer.fremdtext('Kursalarm bei <img src=x onerror="fetch(`//boese`)">')
    assert "<" not in t and ">" not in t and "`" not in t and '"' not in t
    assert t.startswith("Kursalarm bei img src=x onerror=")


def test_echte_namen_bleiben_lesbar():
    assert scorer.fremdtext("De'Longhi S.p.A.") == "De'Longhi S.p.A."
    assert scorer.fremdtext("L'Air Liquide S.A.") == "L'Air Liquide S.A."
    assert scorer.fremdtext("Bob's Discount Furniture, Inc.") == "Bob's Discount Furniture, Inc."
    assert scorer.fremdtext("Ålandsbanken Abp – A") == "Ålandsbanken Abp – A"
    assert scorer.fremdtext('Rheinmetall "AG"') == "Rheinmetall ”AG”"


def test_steuerzeichen_und_leerraum():
    assert scorer.fremdtext("  Nvidia\x00\x07 \n Corp\t ") == "Nvidia Corp"


def test_laenge_und_leere_werte():
    assert scorer.fremdtext("x" * 300, 240) == "x" * 240
    assert scorer.fremdtext(None) is None
    assert scorer.fremdtext("") == ""
    assert scorer.fremdtext("<>") == ""


def test_scorer_nutzt_den_filter_fuer_name_und_kontext():
    """Wird der Filter an der Ausgabe umgangen, nuetzt er nichts."""
    with open(scorer.__file__, encoding="utf-8") as f:
        quelle = f.read()
    assert '"name": fremdtext(meta.get("longName")' in quelle
    assert '"kontext": fremdtext(kontext, 240)' in quelle


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
