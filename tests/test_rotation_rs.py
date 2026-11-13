#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer das Rotation-Dashboard-RS-Rating (Rotation-Dashboard/src/rs.py).

Synthetische Kursreihen -> erwartetes RS-Verhalten. Kein Netz, kein Server,
keine POSTs -> gefahrlos gegen die Live-Daten. Deckt bisher UNGETESTETE
Kernberechnung ab (Performance-Review 2026-08-02) - analog test_pivot.py.

  python3 tests/test_rotation_rs.py
"""
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "..", "Rotation-Dashboard", "src"))
import rs


def uptrend(level, tage=260, start=100.0):
    return [start + (level - start) * i / (tage - 1) for i in range(tage)]


def test_raw_rs_score_outperformer_positiv():
    etf = uptrend(200.0)       # verdoppelt sich
    bench = uptrend(120.0)     # Benchmark legt nur moderat zu
    score = rs.raw_rs_score(etf, bench)
    assert score is not None and score > 0, score
    print("ok  raw_rs_score Outperformer", score)


def test_raw_rs_score_underperformer_negativ():
    etf = uptrend(110.0)
    bench = uptrend(200.0)
    score = rs.raw_rs_score(etf, bench)
    assert score is not None and score < 0, score
    print("ok  raw_rs_score Underperformer", score)


def test_raw_rs_score_zu_wenig_historie():
    assert rs.raw_rs_score([100, 101, 102], [100, 101, 102]) is None
    print("ok  raw_rs_score zu kurz     None")


def test_assign_rs_ratings_perzentil_rang():
    raw = {"A": -0.10, "B": 0.05, "C": 0.20, "D": None}
    ratings = rs.assign_rs_ratings(raw)
    # D (None) faellt raus; A < B < C -> Ratings aufsteigend, Range 1..99.
    assert set(ratings) == {"A", "B", "C"}, ratings
    assert ratings["A"] < ratings["B"] < ratings["C"], ratings
    assert 1 <= min(ratings.values()) and max(ratings.values()) <= 99, ratings
    print("ok  assign_rs_ratings        ", ratings)


def test_assign_rs_ratings_leer():
    assert rs.assign_rs_ratings({}) == {}
    print("ok  assign_rs_ratings leer   {}")


if __name__ == "__main__":
    test_raw_rs_score_outperformer_positiv()
    test_raw_rs_score_underperformer_negativ()
    test_raw_rs_score_zu_wenig_historie()
    test_assign_rs_ratings_perzentil_rang()
    test_assign_rs_ratings_leer()
    print("\nAlle Rotation-Dashboard-RS-Tests bestanden.")
