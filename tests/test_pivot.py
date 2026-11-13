#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer den Pivot-Armed-Detektor (Signal-Hub/src/pivot.py).

Synthetische Charts -> erwartete Klassifikation. Kein Netz, kein Server, keine
POSTs -> gefahrlos gegen die Live-Daten.

  python3 tests/test_pivot.py
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import pivot


def chart(c, v):
    """Baut den signals.json-Chart-Block inkl. SMA-Reihen aus Kurs/Volumen."""
    def sma(n):
        out = []
        for i in range(len(c)):
            out.append(round(sum(c[i - n + 1:i + 1]) / n, 2) if i - n + 1 >= 0 else None)
        return out
    return {"c": c, "v": v, "s50": sma(50), "s150": sma(150), "s200": sma(200)}


def uptrend_basis(level=100.0):
    """210 Tage sauberer Aufwaertstrend bis `level` (Stage-2-Kontext erfuellt)."""
    return [40 + (level - 40) * i / 209 for i in range(210)]


def test_armed():
    # Aufwaertstrend, dann enge, austrocknende Basis dicht unter dem Pivot.
    c = uptrend_basis(100.0)
    pivot_lvl = c[-1]
    for _ in range(24):                       # 24 Tage enge Basis 98..100 (Range ~2%)
        c.append(pivot_lvl * 0.99)
    v = [1000] * (len(c) - 10) + [550] * 10    # juengstes Volumen trocknet aus
    r = pivot.klassifiziere(chart(c, v))
    assert r["status"] == "ARMED", r
    assert r["dryup"] < 0.8, r
    print("ok  ARMED        ", r["status"], r.get("detail"))


def test_breakout():
    # Enge Basis, dann heute frischer Durchbruch auf Volumenschub.
    c = uptrend_basis(100.0)
    base = c[-1]
    for _ in range(22):                        # enge Basis knapp unter 100
        c.append(base * 0.985)
    c.append(base * 1.03)                      # heute: ueber Pivot
    v = [1000] * (len(c) - 1) + [2200]         # Volumenschub am Ausbruchstag
    r = pivot.klassifiziere(chart(c, v))
    assert r["status"] == "BREAKOUT", r
    assert r["vol_surge"] >= 1.4, r
    print("ok  BREAKOUT     ", r["status"], r.get("detail"))


def test_downtrend_kein_setup():
    # Fallender Wert -> Uptrend-Gate blockt (kein fallendes Messer).
    c = [200 - 150 * i / 209 for i in range(210)]
    v = [1000] * len(c)
    r = pivot.klassifiziere(chart(c, v))
    assert r["status"] == "-", r
    print("ok  kein Setup   ", r["status"], r.get("grund"))


def test_cheat_pivot():
    # Abwaertstrend (Uptrend-Gate faellt), aber Stabilisierung + fruehe enge Basis
    # nahe der 50-Tage-Linie mit frischem Ausbruch auf Volumen -> CHEAT statt "-".
    c = [150 - 70 * i / 199 for i in range(200)]   # 150 -> 80 fallend
    for _ in range(8):
        c.append(80.5)                              # kurze, enge Basis um 80.5
    c.append(83.5)                                   # heute: Ausbruch ueber die Basis
    v = [1000] * (len(c) - 1) + [1500]                # Volumenschub
    r = pivot.klassifiziere(chart(c, v))
    assert r["status"] == "CHEAT", r
    assert r["vol_surge"] >= pivot.CHEAT_VOL, r
    print("ok  CHEAT        ", r["status"], r.get("detail"))
    # Deaktiviert (cheat_aktiv=False) -> faellt zurueck auf "-" wie vor der Erweiterung.
    r2 = pivot.klassifiziere(chart(c, v), cheat_aktiv=False)
    assert r2["status"] == "-", r2
    print("ok  CHEAT aus    ", r2["status"])


def test_extended_kein_breakout():
    # Laengst ausgebrochen + ausgedehnt: kein FRISCHER Durchbruch mehr.
    c = uptrend_basis(100.0)
    for k in range(1, 16):                     # 15 Tage stetig weiter hoch (kein Pivot-Kreuz)
        c.append(100.0 + k)
    v = [1000] * len(c)
    r = pivot.klassifiziere(chart(c, v))
    assert r["status"] != "BREAKOUT", r
    print("ok  nicht frisch ", r["status"])


def test_backtest_r_multiple():
    # R-Logik: ausgestoppter Pfad -> -1R; sauberer Lauf -> positives R.
    import pivot_backtest as bt
    c = [100, 101, 102, 103, 104, 105]      # Stop bei 99 -> nie gerissen
    assert bt._r_multiple(c, 0, 5, 99) == (105 - 100) / (100 - 99), bt._r_multiple(c, 0, 5, 99)
    c2 = [100, 98, 97, 101, 102, 103]       # faellt unter Stop 99 -> -1R
    assert bt._r_multiple(c2, 0, 5, 99) == -1.0
    assert bt._r_multiple([100, 101], 0, 1, 100) is None   # Risiko 0 -> verworfen
    print("ok  R-Multiple   bestanden")


def test_backtest_stats():
    import pivot_backtest as bt
    s = bt._stats([0.10, -0.05, 0.20, 0.0, 0.15])
    assert s["n"] == 5 and s["win"] == 60.0, s
    print("ok  _stats       bestanden")


if __name__ == "__main__":
    test_armed()
    test_breakout()
    test_downtrend_kein_setup()
    test_cheat_pivot()
    test_extended_kein_breakout()
    test_backtest_r_multiple()
    test_backtest_stats()
    print("\nAlle Pivot-Tests bestanden.")
