#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer das Code-33-Margenkriterium (Signal-Hub/src/scorer.py::yahoo_code33).

Seit 2026-09-24 (Befund HALO): ein Vergleichsquartal mit negativer Marge
zaehlt nicht, wenn die Reihe davor schon profitabel war (einmaliger
Sonderverlust). Turnarounds aus durchgehenden Verlusten und Reihen ohne
Verlustquartal bleiben unveraendert. Dieselbe Regel steht in
cloudflare-worker/mts-cors-proxy.js::fetchCode33() - beide mitpflegen.

Synthetische Yahoo-Antwort ueber einen Fake-Opener -> kein Netz.

  python3 tests/test_code33_marge.py
"""
import io
import json
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import scorer

DATEN = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


class FakeOpener:
    """Liefert eine fundamentals-timeseries-Antwort mit Umsatz 100 je Quartal,
    Nettogewinn = Marge in Prozent und EPS mit +50 % ggü. Vorjahr."""
    def __init__(self, margen_pct):
        self.margen = margen_pct

    def open(self, req, timeout=12):
        def reihe(key, werte):
            return {key: [{"asOfDate": d, "reportedValue": {"raw": v}} for d, v in zip(DATEN, werte)]}
        antwort = {"timeseries": {"result": [
            reihe("quarterlyTotalRevenue", [100.0, 100.0, 100.0, 100.0, 110.0]),
            reihe("quarterlyNetIncome", [m * (110.0 if i == 4 else 100.0) / 100 for i, m in enumerate(self.margen)]),
            reihe("quarterlyDilutedEPS", [1.0, 1.0, 1.0, 1.0, 1.5]),
        ]}}
        return io.BytesIO(json.dumps(antwort).encode())


def code33(margen):
    return scorer.yahoo_code33("TEST", FakeOpener(margen), "crumb")


def test_einmaliger_sonderverlust_zaehlt_nicht_als_basis():
    # HALO-Muster: profitabel, ein Verlustquartal, wieder profitabel.
    c = code33([50.7, 49.5, -31.3, 39.8, 47.8])
    assert c["marge_basis_ausgeschlossen"] == ["2025-12-31"]
    assert c["marge_erfuellt"] is False
    assert c["marge_delta_pp"] == -1.7
    assert c["anzahl_erfuellt"] == 2


def test_beide_vergleichsquartale_ausgeschlossen_ergibt_kein_delta():
    c = code33([4.2, -48.0, -0.3, -1.9, 21.1])
    assert c["marge_basis_ausgeschlossen"] == ["2025-12-31", "2025-09-30"]
    assert c["marge_erfuellt"] is False
    assert c["marge_delta_pp"] is None


def test_turnaround_aus_dauerverlust_bleibt_erfuellt():
    c = code33([-1.7, -3.6, -2.7, -5.5, 7.9])
    assert c["marge_basis_ausgeschlossen"] == []
    assert c["marge_erfuellt"] is True


def test_ohne_verlustquartal_unveraendert_max_logik():
    # Verbesserung nur gegen eines der beiden Vergleichsquartale reicht weiter.
    c = code33([20.0, 25.0, 21.0, 22.0, 24.0])
    assert c["marge_basis_ausgeschlossen"] == []
    assert c["marge_erfuellt"] is True
    assert c["marge_delta_pp"] == 3.0


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok ", name)
