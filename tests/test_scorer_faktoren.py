#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer die wichtigsten Signal-Hub-Score-Faktoren (Signal-Hub/src/scorer.py).

Synthetische Kursreihen -> erwartetes Faktor-Verhalten. Kein Netz, kein
Server, keine POSTs -> gefahrlos gegen die Live-Daten. Deckt bisher UNGETESTETE
Kernberechnungen ab (Performance-Review 2026-08-02) - analog test_pivot.py.

  python3 tests/test_scorer_faktoren.py
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Signal-Hub", "src"))
import scorer


def uptrend(level=100.0, tage=260):
    """Sauberer, langer Aufwaertstrend (Stage-2-Kontext fuer alle SMA-Kriterien erfuellt)."""
    return [40 + (level - 40) * i / (tage - 1) for i in range(tage)]


def downtrend(start=150.0, ende=80.0, tage=210):
    return [start - (start - ende) * i / (tage - 1) for i in range(tage)]


def test_f_stage2_uptrend_erfuellt_alle_kriterien():
    # Seit dem SEPA-Abgleich 2026-08-29 liefert f_stage2 zusaetzlich das
    # "benannt"-Dict der Einzelkriterien (Kriterium 8/RS wird separat in
    # f_trend_template() geprueft, siehe dortiger Kommentar) - Ratio und
    # Text bleiben unveraendert das, was dieser Test prueft.
    c = uptrend()
    wert, detail, _benannt = scorer.f_stage2(c, hi52=max(c), lo52=min(c))
    assert wert >= 0.9, (wert, detail)
    print("ok  f_stage2 Uptrend       ", wert, detail)


def test_f_stage2_downtrend_erfuellt_wenige():
    c = downtrend()
    wert, detail, _benannt = scorer.f_stage2(c, hi52=max(c), lo52=min(c))
    assert wert <= 0.5, (wert, detail)
    print("ok  f_stage2 Downtrend     ", wert, detail)


def test_f_relative_staerke_outperformer():
    # Aktie verdoppelt sich, Index legt nur leicht zu -> RS deutlich > 1.
    aktie = uptrend(level=200.0)
    index = uptrend(level=110.0)
    wert, detail, rs = scorer.f_relative_staerke(aktie, index)
    assert rs is not None and rs > 1.1, (wert, detail, rs)
    assert wert > 0.5, (wert, detail, rs)
    print("ok  f_relative_staerke     ", wert, detail)


def test_f_relative_staerke_zu_wenig_historie():
    wert, detail, rs = scorer.f_relative_staerke([100, 101, 102], [100, 101, 102])
    assert wert == 0.0 and rs is None, (wert, detail, rs)
    print("ok  f_relative_staerke kurz", wert, detail)


def test_f_naehe_hoch_am_hoch():
    c = uptrend()
    wert, detail = scorer.f_naehe_hoch(c, hi52=c[-1])
    assert wert == 1.0, (wert, detail)
    print("ok  f_naehe_hoch am Hoch   ", wert, detail)


def test_f_naehe_hoch_weit_entfernt():
    c = uptrend()
    wert, detail = scorer.f_naehe_hoch(c, hi52=c[-1] * 2)
    assert wert == 0.0, (wert, detail)
    print("ok  f_naehe_hoch weit weg  ", wert, detail)


def test_f_basis_enge_konsolidierung():
    # 40 Tage mit spuerbarer Schwankung (Saegezahn 85-100), dann 20 Tage enge
    # Seitwaertsbewegung (99-100) -> deutliche Verengung -> hoher Wert.
    davor = [85 + 15 * (i % 10) / 9 for i in range(40)]
    letzte = [99 + (i % 2) for i in range(20)]
    wert, detail = scorer.f_basis(davor + letzte)
    assert wert > 0.4, (wert, detail)
    print("ok  f_basis eng            ", wert, detail)


def test_f_volumen_schub():
    c = [100] * 60
    v = [1000] * 55 + [2000] * 5  # Volumenschub in den letzten 5 Tagen
    wert, detail = scorer.f_volumen(c, v)
    assert wert > 0.5, (wert, detail)
    print("ok  f_volumen Schub        ", wert, detail)


def test_f_volumen_zu_wenig_daten():
    wert, detail = scorer.f_volumen([100, 101], [1000, 1000])
    assert wert == 0.0, (wert, detail)
    print("ok  f_volumen kurz         ", wert, detail)


if __name__ == "__main__":
    test_f_stage2_uptrend_erfuellt_alle_kriterien()
    test_f_stage2_downtrend_erfuellt_wenige()
    test_f_relative_staerke_outperformer()
    test_f_relative_staerke_zu_wenig_historie()
    test_f_naehe_hoch_am_hoch()
    test_f_naehe_hoch_weit_entfernt()
    test_f_basis_enge_konsolidierung()
    test_f_volumen_schub()
    test_f_volumen_zu_wenig_daten()
    print("\nAlle Scorer-Faktor-Tests bestanden.")
