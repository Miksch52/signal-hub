#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemeinsame Exit-Simulation fuer alle Backtests (seit 2026-09-12,
Systempruefung Punkt 4).

Problem, das dieses Modul loest: Die Backtests messen bisher, was ein SIGNAL
gebracht haette (Kurs am Signaltag vs. Kurs am Stichtag) - gehandelt wird
aber eine STRATEGIE mit Stop und gestaffelter Gewinnmitnahme. Die -11,5 %
der 12-Wochen-Kohorte in score_backtest.py entstehen zu einem unbekannten
Teil aus Positionen, die real laengst ausgestoppt gewesen waeren. Solange
beide Zahlen nicht nebeneinander stehen, laesst sich nicht sagen, ob ein
schwaches Kohorten-Ergebnis am Signal liegt oder am fehlenden Risikomanagement
in der Messung.

Die Logik stand seit 2026-08-21 schon in pivot_backtest.py::_simulate_exit,
aber NUR dort - und hat bis heute (2026-09-12) null Ergebnisse geliefert,
weil sie ein "stop"-Feld im Logbuch braucht, das erst ab 2026-08-21 mitge-
schrieben wird, und zusaetzlich 78 Kalendertage Reife verlangt (erste Zahlen
also fruehestens Anfang November). Hier liegt sie jetzt als eigenes Modul,
damit die uebrigen Engines dieselbe Konvention nutzen statt jede eine eigene
zu erfinden - und mit dem prozentualen Stop (siehe unten) auch rueckwirkend
auf Kohorten anwendbar sind, die gar kein Stop-Feld haben.

Belegte Konventionen (nicht gesetzt, sondern aus dem System abgelesen):
  - Stop: 8 % unter Einstieg. Alle 252 Trades in mts_data.json nutzen exakt
    diesen Abstand (Median = Mittelwert = 8,00 %, kein einziger Ausreisser) -
    das ist die real gehandelte Regel, keine Annahme.
  - Ausstiegsstaffel T1 +8 %/50 %, T2 +20 %/25 %, T3 +40 %/25 % - dieselbe
    Staffel wie der Trade-Planner in "Maick's Trading System.html" und wie
    pivot_backtest.py sie seit 2026-08-21 verwendet.
  - Stop schlaegt Ziel innerhalb desselben Handelstags: Taeglich liegen nur
    Hoch und Tief vor, nicht ihre Reihenfolge. Wird an einem Tag beides
    beruehrt, zaehlt der Stop. Bewusst die konservative Variante - die
    optimistische wuerde die Staffel besser aussehen lassen, als sie ist.
  - Horizont: 78 Kalendertage (deckt sich mit dem 12W-Bucket der Backtests).
    Eine Episode, deren Chart diesen Stichtag noch nicht erreicht, wird NICHT
    simuliert (ein frueher abgeschnittener Hold-Vergleich waere unfair).

Anders als der uebrige Forward-Test misst die Simulation gegen einen FESTEN
Stichtag (Signaltag + 78 Tage) statt gegen "heute, wann auch immer der Lauf
stattfindet" - strategie_return und hold_return sind deshalb untereinander
exakt vergleichbar. Das ist zugleich der erste Baustein fuer Punkt 2 der
Systempruefung (feste Fenster fuer alle Kohorten).

Test: python3 tests/test_exit_simulation.py
"""

from datetime import datetime, timedelta

# Ausstiegsstaffel (Trade-Planner "Maick's Trading System.html")
T1_PCT, T1_ANTEIL = 0.08, 0.50
T2_PCT, T2_ANTEIL = 0.20, 0.25
T3_PCT, T3_ANTEIL = 0.40, 0.25

STOP_PCT = 0.08           # belegt aus allen 252 realen Trades, siehe Docstring
HORIZONT_TAGE = 78        # Kalendertage, deckt sich mit dem 12W-Bucket


def stop_aus_prozent(entry_preis, pct=STOP_PCT):
    """Stop fuer Kohorten ohne eigenes Stop-Feld (Score-Episoden, Muster,
    Rotation): fester prozentualer Abstand zum Einstieg. Rueckwirkend auf
    jede Episode anwendbar, weil nur der Einstiegskurs gebraucht wird."""
    if entry_preis is None:
        return None
    return entry_preis * (1 - pct)


def finde_start_index(dates, entry_datum):
    """Erster Chart-Index mit dates[i] >= entry_datum - der Entry gilt zum
    Signalkurs am Signaltag, die Simulation beobachtet ab diesem Tag."""
    for i, d in enumerate(dates):
        if d and d >= entry_datum:
            return i
    return None


def simuliere(chart, entry_datum, entry_preis, stop, horizont_tage=HORIZONT_TAGE):
    """Simuliert die Ausstiegsstaffel Tag fuer Tag gegen den echten Kursverlauf.

    Nutzt Hoch/Tief je Tag statt nur Schluss - ein Stop oder Ziel kann intraday
    ausgeloest werden, ohne dass der Schlusskurs das zeigt.

    chart: dict mit dates/highs/lows/closes (scorer.py::yahoo_chart-Format).
    Gibt None zurueck, wenn der Signaltag im Chart fehlt, kein plausibler Stop
    vorliegt oder der Horizont im verfuegbaren Chart noch nicht erreicht ist.

    Rueckgabe:
      strategie_return  Gesamtergebnis der Staffel in % (gewichtet ueber alle
                        Teilverkaeufe; eine zum Stichtag offene Restposition
                        wird zum dortigen Schlusskurs bewertet)
      hold_return       einfach halten bis zum SELBEN Stichtag, in %
      t1/t2/t3          wurde die jeweilige Stufe erreicht
      gestoppt          wurde der Stop beruehrt
      mfe/mae           groesster Buchgewinn / groesster Buchverlust im
                        Fenster in % (unabhaengig vom simulierten Exit) -
                        zeigt, ob der Stop zu eng oder das erste Ziel zu nah
                        liegt: hohes mfe bei gestoppten Trades heisst, die
                        Position lief spaeter ohne uns weiter.
    """
    dates = chart.get("dates") or []
    highs, lows = chart.get("highs") or [], chart.get("lows") or []
    closes = chart.get("closes") or []
    if not dates or not entry_preis or stop is None or stop >= entry_preis:
        return None
    start = finde_start_index(dates, entry_datum)
    if start is None:
        return None
    try:
        grenze = (datetime.strptime(entry_datum, "%Y-%m-%d")
                  + timedelta(days=horizont_tage)).strftime("%Y-%m-%d")
    except ValueError:
        return None
    horizont_idx = None
    for i in range(start, len(dates)):
        if dates[i] and dates[i] >= grenze:
            horizont_idx = i
            break
    if horizont_idx is None:
        return None   # Chart deckt den Horizont noch nicht ab
    if horizont_idx >= len(closes) or horizont_idx >= len(highs) or horizont_idx >= len(lows):
        return None
    hold_close = closes[horizont_idx]

    t1_preis = entry_preis * (1 + T1_PCT)
    t2_preis = entry_preis * (1 + T2_PCT)
    t3_preis = entry_preis * (1 + T3_PCT)
    rest, erloese = 1.0, 0.0
    t1_ok = t2_ok = t3_ok = gestoppt = False
    hoch, tief = entry_preis, entry_preis
    for i in range(start, horizont_idx + 1):
        # MFE/MAE laufen ueber das ganze Fenster weiter, auch nach dem Exit -
        # genau der Vergleich "was waere ohne Stop noch gekommen" ist die
        # interessante Frage (siehe Docstring).
        hoch = max(hoch, highs[i])
        tief = min(tief, lows[i])
        if rest <= 0:
            continue
        if lows[i] <= stop:
            erloese += rest * stop
            rest = 0.0
            gestoppt = True
            continue
        if not t1_ok and highs[i] >= t1_preis:
            erloese += T1_ANTEIL * t1_preis
            rest -= T1_ANTEIL
            t1_ok = True
        if not t2_ok and highs[i] >= t2_preis:
            verkauf = min(T2_ANTEIL, rest)
            erloese += verkauf * t2_preis
            rest -= verkauf
            t2_ok = True
        if not t3_ok and highs[i] >= t3_preis:
            erloese += rest * t3_preis
            rest = 0.0
            t3_ok = True
    if rest > 0:
        erloese += rest * hold_close   # offene Restposition zum Stichtag bewertet

    return {
        "strategie_return": round((erloese / entry_preis - 1.0) * 100, 2),
        "hold_return": round((hold_close / entry_preis - 1.0) * 100, 2),
        "t1": t1_ok, "t2": t2_ok, "t3": t3_ok, "gestoppt": gestoppt,
        "mfe": round((hoch / entry_preis - 1.0) * 100, 2),
        "mae": round((tief / entry_preis - 1.0) * 100, 2),
    }


def aggregiere(sims):
    """Kohorten-Statistik ueber eine Liste von simuliere()-Ergebnissen."""
    sims = [s for s in sims if s]
    if not sims:
        return {"n": 0}

    def _stats(werte):
        werte = sorted(werte)
        n = len(werte)
        return {
            "win": round(sum(1 for w in werte if w > 0) / n * 100, 1),
            "avg": round(sum(werte) / n, 2),
            "median": round(werte[n // 2] if n % 2 else (werte[n // 2 - 1] + werte[n // 2]) / 2, 2),
        }

    n = len(sims)
    strategie = _stats([s["strategie_return"] for s in sims])
    hold = _stats([s["hold_return"] for s in sims])
    ergebnis_extra = {}
    # Index-Bezug (seit 2026-09-12, Systempruefung Punkt 5): der Aufrufer kann
    # je Episode "index_return" mitgeben - die Rendite des Leitindex ueber
    # GENAU dasselbe feste Fenster (index_vergleich.index_return_fenster).
    # Ohne diesen Abzug sagt auch die Strategie-Spur nur, wie der Markt lief.
    mit_idx = [s for s in sims if s.get("index_return") is not None]
    if mit_idx:
        ergebnis_extra["vs_index"] = {
            "n": len(mit_idx),
            "index_avg": round(sum(s["index_return"] for s in mit_idx) / len(mit_idx), 2),
            "strategie_edge_avg": round(
                sum(s["strategie_return"] - s["index_return"] for s in mit_idx) / len(mit_idx), 2),
            "hold_edge_avg": round(
                sum(s["hold_return"] - s["index_return"] for s in mit_idx) / len(mit_idx), 2),
            "strategie_schlaegt_index_pct": round(
                sum(1 for s in mit_idx if s["strategie_return"] > s["index_return"])
                / len(mit_idx) * 100, 1),
        }
    return {
        "n": n,
        "strategie": strategie,
        "hold": hold,
        **ergebnis_extra,
        "vorsprung_avg": round(strategie["avg"] - hold["avg"], 2),
        "gestoppt_pct": round(sum(1 for s in sims if s["gestoppt"]) / n * 100, 1),
        "t1_pct": round(sum(1 for s in sims if s["t1"]) / n * 100, 1),
        "t2_pct": round(sum(1 for s in sims if s["t2"]) / n * 100, 1),
        "t3_pct": round(sum(1 for s in sims if s["t3"]) / n * 100, 1),
        "mfe_median": _stats([s["mfe"] for s in sims])["median"],
        "mae_median": _stats([s["mae"] for s in sims])["median"],
        # Gestoppte Trades, die spaeter noch deutlich ueber den Einstieg
        # gelaufen waeren - das Mass dafuer, ob der 8-%-Stop zu eng sitzt.
        "gestoppt_mfe_ge_t1_pct": round(
            sum(1 for s in sims if s["gestoppt"] and s["mfe"] >= T1_PCT * 100) / n * 100, 1),
    }
