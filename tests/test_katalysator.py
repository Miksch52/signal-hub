#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer den Katalysator-Layer (Price-Action-Hub/src/katalysator.py
und katalysator_backtest.py).

Kein echter Netzaufruf - urllib.request.urlopen wird gemockt (fester
Bytes-Payload, kein Netz noetig), alle Pfade zeigen auf ein temporaeres
Verzeichnis statt auf den echten lokalen Zustand (~/Library/Application
Support/PriceActionHub) oder die echte Signal-Hub/data/. Gefahrlos gegen die
Live-Daten, analog test_pivot.py.

  python3 tests/test_katalysator.py
"""
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest import mock

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Price-Action-Hub", "src"))
import pfade
import katalysator
import katalysator_backtest


def _tmp_umleiten(tmpdir):
    """Alle relevanten pfade-Konstanten in ein Wegwerfverzeichnis umbiegen -
    kein Test darf die echte lokale Zustandsdatei oder Signal-Hub/data/
    beruehren."""
    pfade.SIGNAL_HUB_TOP_SETUPS_JSON = os.path.join(tmpdir, "top_setups.json")
    pfade.KATALYSATOR_JSON = os.path.join(tmpdir, "top_setups_katalysator.json")
    pfade.KATALYSATOR_JS = os.path.join(tmpdir, "top_setups_katalysator.js")
    pfade.KATALYSATOR_BACKTEST = os.path.join(tmpdir, "katalysator_backtest.json")
    pfade.KATALYSATOR_BACKTEST_JS = os.path.join(tmpdir, "katalysator_backtest.js")
    pfade.KATALYSATOR_STATE = os.path.join(tmpdir, "katalysator_state.json")
    pfade.KATALYSATOR_LOGBUCH = os.path.join(tmpdir, "katalysator_logbuch.json")
    # Fehlte bis zur Pipeline-Einbindung (Systempruefung Punkt 10, 13.09.2026):
    # katalysator_backtest.evaluate() laedt und schreibt ueber kursdaten.py
    # auch den Yahoo-Kurs-Cache. Ohne Umleitung traf test_backtest_kontroll-
    # gruppe_und_kohorte() dabei die ECHTE lokale Cache-Datei
    # (~/Library/Application Support/PriceActionHub/yahoo_cache.json, real
    # >50 MB) - unveraendert zurueckgeschrieben (der Mock unten ersetzt
    # hole_chart_cached() komplett, der Cache-Inhalt wird gar nicht genutzt),
    # aber Laden+Schreiben dieser Datei allein kostete ~52 der ~54 Sekunden
    # der gesamten Testsuite und war ausserdem ein unnoetiger Zugriff auf
    # echten lokalen Zustand waehrend eines Tests.
    pfade.YAHOO_CACHE = os.path.join(tmpdir, "yahoo_cache.json")


def _mock_antwort(klasse, text, quellen=None):
    """Baut eine Perplexity-chat/completions-foermige Antwort, wie sie
    urllib.request.urlopen(...).read() liefern wuerde."""
    inhalt = json.dumps({"klasse": klasse, "text": text})
    payload = {
        "choices": [{"message": {"content": inhalt}}],
        "citations": quellen or [],
    }
    daten = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return daten

    def _urlopen(req, timeout=None, context=None):
        r = _Resp(daten)
        return r
    return _urlopen


def test_ohne_api_key_bricht_still_ab():
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        env = dict(os.environ)
        env.pop("PERPLEXITY_API_KEY", None)
        with mock.patch.dict(os.environ, env, clear=True):
            r = katalysator.lauf()
        assert r == {}, r
    print("ok  kein Key         -> stiller Abbruch")


def test_unbekannte_klasse_wird_geklemmt():
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        json.dump({"setups": [{"ticker": "NVDA", "name": "Nvidia", "markt": "USA", "preis": 180.0}]},
                   open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        with mock.patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen", _mock_antwort("erfundene_klasse", "Text.")):
                r = katalysator.lauf()
        assert r["NVDA"]["klasse"] == "keiner", r
    print("ok  unbekannte Klasse -> auf 'keiner' geklemmt")


def test_text_wird_laengenbegrenzt():
    lang = "x" * 5000
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        json.dump({"setups": [{"ticker": "SAP", "name": "SAP", "markt": "Europa", "preis": 240.0}]},
                   open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        with mock.patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen", _mock_antwort("zahlen", lang)):
                r = katalysator.lauf()
        assert len(r["SAP"]["text"]) == katalysator.MAX_TEXT_LAENGE, len(r["SAP"]["text"])
    print("ok  Fremdtext         -> auf MAX_TEXT_LAENGE begrenzt")


def test_schreibt_json_und_js():
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        json.dump({"setups": [{"ticker": "AAPL", "name": "Apple", "markt": "USA", "preis": 200.0}]},
                   open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        with mock.patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen", _mock_antwort("guidance", "Prognose angehoben.", ["https://reuters.com/x"])):
                katalysator.lauf()
        assert os.path.exists(pfade.KATALYSATOR_JSON)
        assert os.path.exists(pfade.KATALYSATOR_JS)
        gespeichert = json.load(open(pfade.KATALYSATOR_JSON))
        assert gespeichert["katalysatoren"]["AAPL"]["klasse"] == "guidance"
        assert gespeichert["katalysatoren"]["AAPL"]["quellen"] == ["https://reuters.com/x"]
        js_inhalt = open(pfade.KATALYSATOR_JS, encoding="utf-8").read()
        assert js_inhalt.startswith("window.TOP_SETUPS_KATALYSATOR = {")
    print("ok  Ausgabedateien    -> json+js korrekt geschrieben")


def test_tages_gate_verhindert_zweiten_lauf():
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        json.dump({"setups": [{"ticker": "MSFT", "name": "Microsoft", "markt": "USA", "preis": 400.0}]},
                   open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        rufe = {"n": 0}
        urlopen = _mock_antwort("analyst", "Upgrade.")

        def _zaehlend(req, timeout=None, context=None):
            rufe["n"] += 1
            return urlopen(req, timeout, context)

        with mock.patch.dict(os.environ, {"PERPLEXITY_API_KEY": "test-key"}):
            with mock.patch("urllib.request.urlopen", _zaehlend):
                r1 = katalysator.taeglich_falls_faellig()
                r2 = katalysator.taeglich_falls_faellig()
        assert r1 != {} and r2 == {}, (r1, r2)
        assert rufe["n"] == 1, rufe["n"]
    print("ok  Tages-Gate        -> zweiter Lauf am selben Tag uebersprungen")


def test_backtest_kontrollgruppe_und_kohorte():
    with tempfile.TemporaryDirectory() as td:
        _tmp_umleiten(td)
        heute = datetime.now().strftime("%Y-%m-%d")
        json.dump({"setups": [
            {"ticker": "AMZN", "name": "Amazon", "markt": "USA", "preis": 100.0},
            {"ticker": "TSLA", "name": "Tesla", "markt": "USA", "preis": 200.0},
        ]}, open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        json.dump({"erstellt": heute, "modell": "sonar", "katalysatoren": {
            "AMZN": {"klasse": "zahlen", "text": "Zahlen geschlagen."},
            "TSLA": {"klasse": "keiner", "text": "Kein Anlass gefunden."},
        }}, open(pfade.KATALYSATOR_JSON, "w"))

        # Seit 2026-10-04 holt log_heute() die Index-Charts (belegter
        # Handelstag) - ohne Stub ginge das ins Netz. Ohne Chart wird wie
        # frueher nur das Logdatum geschrieben; das genuegt fuer diesen Test.
        # Patch direkt am gebundenen Modul, Begruendung siehe unten.
        kein_netz = mock.patch.object(katalysator_backtest.kursdaten, "hole_chart_cached",
                                      return_value=None)
        with kein_netz:
            katalysator_backtest.log_heute()
        lb = json.load(open(pfade.KATALYSATOR_LOGBUCH))
        assert len(lb) == 2
        stufen = {e["ticker"]: e["stufe"] for e in lb}
        assert stufen["AMZN"] == "mit_katalysator"
        assert stufen["TSLA"] == "ohne_katalysator"

        # Ein Ticker OHNE Katalysator-Antwort an diesem Tag darf in KEINE
        # Kohorte einsortiert werden (Zustand unbekannt, nicht "kein Katalysator").
        json.dump({"setups": [
            {"ticker": "AMZN", "name": "Amazon", "markt": "USA", "preis": 100.0},
            {"ticker": "GOOG", "name": "Alphabet", "markt": "USA", "preis": 150.0},
        ]}, open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, "w"))
        json.dump({"erstellt": heute, "modell": "sonar", "katalysatoren": {
            "AMZN": {"klasse": "zahlen", "text": "Zahlen geschlagen."},
        }}, open(pfade.KATALYSATOR_JSON, "w"))
        with kein_netz:
            katalysator_backtest.log_heute()
        lb2 = json.load(open(pfade.KATALYSATOR_LOGBUCH))
        assert all(e["ticker"] != "GOOG" for e in lb2), lb2
        print("ok  Backtest-Log      -> Kohorten korrekt, unabgefragte Ticker ausgeschlossen")

        # Reife Kohorte (35 Tage alt -> 4W-Bucket) mit mockbarem Kurs auswerten.
        alt = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
        lb3 = [
            {"datum": alt, "ticker": "AMZN", "markt": "USA", "stufe": "mit_katalysator",
             "klasse": "zahlen", "preis_signal": 100.0},
            {"datum": alt, "ticker": "TSLA", "markt": "USA", "stufe": "ohne_katalysator",
             "klasse": "keiner", "preis_signal": 200.0},
        ]
        json.dump(lb3, open(pfade.KATALYSATOR_LOGBUCH, "w"))

        # Bewusst NICHT "import kursdaten" (frisch, per Namen): Price-Action-Hub
        # und Rotation-Dashboard haben je ein eigenes, gleichnamiges kursdaten.py
        # (zwei getrennte Repos, siehe pfade.py-Docstring) - in einem gemeinsamen
        # pytest-Lauf sind beide irgendwann in sys.modules['kursdaten'] gelandet,
        # je nachdem, welche Testdatei zuletzt "import kursdaten" ausgefuehrt hat.
        # Ein frischer Import haette dann mit ungewisser Wahrscheinlichkeit NICHT
        # dasjenige Modulobjekt getroffen, das katalysator_backtest.py selbst
        # beim eigenen Import gebunden hat - der Mock unten haette dann nicht
        # gegriffen und einen echten Yahoo-Netzwerkaufruf ausgeloest (gefunden
        # bei der Pipeline-Einbindung, Systempruefung Punkt 10: 52s statt 2s
        # Laufzeit, Assertions dabei vom echten Kurs abhaengig statt fest).
        # Stattdessen direkt das Attribut patchen, das katalysator_backtest.py
        # tatsaechlich aufruft - unabhaengig vom globalen sys.modules-Zustand.
        # Datumsreihe bis gestern: seit 2026-09-13 messen die Forward-Tests
        # feste Fenster und brauchen dafuer die Datumsreihe des Charts.
        tage = [(datetime.now().date() - timedelta(days=i)).isoformat() for i in range(120, 0, -1)]
        def _chart(bis_signal, danach):
            # Forward-Tests starten seit 2026-09-13 am Schlusskurs des Signaltags
            return {"closes": [bis_signal if d <= alt else danach for d in tage], "dates": tage}
        with mock.patch.object(katalysator_backtest.kursdaten, "hole_chart_cached",
                                side_effect=lambda sym, cache, heute: _chart(100.0, 120.0) if sym == "AMZN" else _chart(200.0, 190.0)):
            fr, faelle = katalysator_backtest.evaluate()
        assert fr["mit_katalysator"]["4W"]["n"] == 1
        assert fr["mit_katalysator"]["4W"]["avg"] == 20.0
        assert fr["ohne_katalysator"]["4W"]["avg"] == -5.0
        print("ok  Backtest-Evaluate -> Renditen je Kohorte korrekt berechnet")


if __name__ == "__main__":
    test_ohne_api_key_bricht_still_ab()
    test_unbekannte_klasse_wird_geklemmt()
    test_text_wird_laengenbegrenzt()
    test_schreibt_json_und_js()
    test_tages_gate_verhindert_zweiten_lauf()
    test_backtest_kontrollgruppe_und_kohorte()
    print("\nAlle Katalysator-Tests bestanden.")
