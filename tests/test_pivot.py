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


def _vcp_wellen():
    """Basis mit drei enger werdenden Wellen (ca. 15 % -> 8 % -> 4 %) auf
    Schlusskursen, dazu h/l je 1 % ueber/unter dem Schluss."""
    c = [94.0, 96.0, 98.0, 99.0]           # Anlauf, damit das erste Hoch ein Swing-Hoch ist
    for tiefe, laenge in ((0.15, 16), (0.08, 12), (0.04, 10)):
        halb = laenge // 2
        c += [100 * (1 - tiefe * i / halb) for i in range(halb)]
        c += [100 * (1 - tiefe + tiefe * i / halb) for i in range(halb)]
    h = [x * 1.01 for x in c]
    l = [x * 0.99 for x in c]
    return c, h, l


def test_kontraktionen_hl():
    # Hoch/Tief misst dieselben Wellen, aber tiefer als die Schlusskurse.
    c, h, l = _vcp_wellen()
    t_c = pivot._kontraktionen(c, len(c))
    t_hl = pivot._kontraktionen_hl(h, l, len(c))
    assert len(t_c) == 3, t_c
    assert len(t_hl) == 3, t_hl
    assert all(a > b for a, b in zip(t_hl, t_c)), (t_hl, t_c)
    assert pivot._verengung_stufen(t_hl) == (2, 2), t_hl
    e = pivot._eng_hl(h, l)
    assert e is not None and e > pivot._range(c[-pivot.ENG_FENSTER:]), e
    print("ok  Kontraktionen H/L", [round(x * 100, 1) for x in t_hl])


def test_kontraktionen_hl_ohne_daten():
    # Ohne h/l: None ("nicht messbar"), nicht 0 Kontraktionen.
    c, _, _ = _vcp_wellen()
    assert pivot._kontraktionen_hl([], [], len(c)) is None
    assert pivot._eng_hl([], []) is None
    cc = uptrend_basis(100.0) + [99.0] * 24
    v = [1000] * (len(cc) - 10) + [550] * 10
    r = pivot.klassifiziere(chart(cc, v))
    assert r["kontraktionen_hl"] is None and r["eng_hl_pct"] is None, r
    # Mit h/l werden die Felder gefuellt.
    ch = chart(cc, v)
    ch["h"] = [x * 1.01 for x in cc]
    ch["l"] = [x * 0.99 for x in cc]
    r2 = pivot.klassifiziere(ch)
    assert r2["status"] == r["status"], (r, r2)          # Status unveraendert
    assert r2["kontraktionen_hl"] is not None and r2["eng_hl_pct"] is not None, r2
    # Falsche Laenge -> nicht verwenden
    ch["h"] = ch["h"][:-1]
    assert pivot.klassifiziere(ch)["kontraktionen_hl"] is None
    print("ok  H/L ohne Daten   bestanden")


def test_backtest_hl_kohorten():
    import pivot_backtest as bt
    ch = {"c": [1, 2, 3], "v": [1, 1, 1], "h": [2, 3, 4], "l": [0, 1, 2]}
    tr = bt._truncate(ch, 1)
    assert tr["h"] == [2, 3] and tr["l"] == [0, 1], tr
    labels = bt._split_stati({"status": "ARMED", "kontraktionen_hl": 3, "eng_hl_pct": 4.0})
    assert "ARMED_vcp_hl_ge3" in labels and "ARMED_eng_hl_le5" in labels, labels
    labels = bt._split_stati({"status": "BREAKOUT", "kontraktionen_hl": 2, "eng_hl_pct": 7.5})
    assert "BREAKOUT_vcp_hl_lt3" in labels and "BREAKOUT_eng_hl_gt5" in labels, labels
    assert all(l in bt.SPLIT_STATI for l in ("ARMED_vcp_hl_ge3", "BREAKOUT_eng_hl_gt5"))
    print("ok  H/L-Kohorten     bestanden")


if __name__ == "__main__":
    test_armed()
    test_breakout()
    test_downtrend_kein_setup()
    test_cheat_pivot()
    test_extended_kein_breakout()
    test_backtest_r_multiple()
    test_backtest_stats()
    test_kontraktionen_hl()
    test_kontraktionen_hl_ohne_daten()
    test_backtest_hl_kohorten()
    print("\nAlle Pivot-Tests bestanden.")
