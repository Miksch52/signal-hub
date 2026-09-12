#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Index-Vergleich fuer alle Forward-Tests (seit 2026-09-12, Systempruefung
Punkt 5).

Problem: Bis dahin rechnete nur score_backtest.py den Vorsprung gegen den
Markt-Index. Pivot-, Muster-, Hebel- und Rotations-Backtest meldeten reine
Absolutrenditen - in einem fallenden Markt sieht damit jedes Signal schlecht
aus, in einem steigenden jedes gut. "Leader-Aktie 91,7 % Trefferquote" und
"Trendbar stark bullisch 21,1 %" waren deshalb gleich wenig belegt, nur in
entgegengesetzte Richtungen. Erst der Abzug des Index macht aus einer
Kohorten-Statistik eine Aussage ueber das SIGNAL.

DIESE DATEI LIEGT IDENTISCH IN DREI REPOS:
  Signal-Hub/src/index_vergleich.py        (Referenz)
  Price-Action-Hub/src/index_vergleich.py
  Rotation-Dashboard/src/index_vergleich.py
Bewusste Duplizierung statt Import ueber Repo-Grenzen: die drei Engines
laufen als eigenstaendige Jobs mit unterschiedlichen relativen Pfaden (lokal
Geschwisterordner, im Cloud-Lauf Secondary-Checkout) - ein Import waere genau
die Art stiller Bruch, die das Projekt schon bei den zwei Datenpfaden der
Datenfusion kennt. Damit die Kopien nicht auseinanderlaufen, vergleicht
tests/test_index_vergleich.py ihre Pruefsummen: wer hier etwas aendert, muss
alle drei Dateien anfassen, sonst schlaegt der Test fehl.

Methodik: Der Pick wird gegen den Leitindex SEINES Marktes gemessen
(USA -> ^GSPC, Europa -> ^STOXX), ueber exakt denselben Zeitraum. Liegt eine
Datumsreihe vor (Signal-Hub-Charts seit 2026-08-21, Price-Action-Hub und
Rotation-Dashboard seit 2026-09-12), wird der Startpunkt exakt ueber das
Signaldatum gesucht; sonst faellt die Rechnung auf die Naeherung
"Handelstage ~ Kalendertage * 5/7" zurueck (so rechnete score_backtest.py
bis hierher, Fehler ca. zwei Handelstage durch Feiertage). Der Fallback
greift auch bei Charts, die am Umstellungstag noch aus dem Tages-Cache ohne
Datumsreihe kommen - kein Grund, deshalb einen ganzen Lauf auszusetzen.
"""

INDEX = {"USA": "^GSPC", "Europa": "^STOXX"}
STANDARD_MARKT = "USA"

# Naeherung fuer Charts ohne Datumsreihe: Handelstage je Kalendertag.
HANDELSTAGE_PRO_KALENDERTAG = 5 / 7


def index_symbol(markt):
    """Leitindex-Symbol fuer einen Markt. Unbekannt/leer -> US-Leitindex
    (Rotation-Dashboard loggt z.B. gar keinen Markt, arbeitet aber
    ausschliesslich mit US-Themen-ETFs und deren Leadern)."""
    return INDEX.get(markt or STANDARD_MARKT, INDEX[STANDARD_MARKT])


def index_return(idx_chart, signal_datum, kalendertage):
    """Index-Rendite vom Signaltag bis zum letzten Kurs des Charts.

    idx_chart: dict mit "closes" (optional "dates") - das Chart-Format aller
    drei Repos. Gibt None zurueck, wenn sich der Zeitraum nicht abbilden
    laesst; der Aufrufer laesst den Pick dann aus der Edge-Kohorte weg
    (er bleibt in der normalen Kohorte, nur ohne Index-Bezug)."""
    closes = (idx_chart or {}).get("closes") or []
    dates = (idx_chart or {}).get("dates") or []
    if not closes or not closes[-1]:
        return None

    start = None
    if dates and len(dates) == len(closes):
        for i, d in enumerate(dates):
            if d and d >= signal_datum:
                start = closes[i]
                break
    else:
        offset = max(1, round((kalendertage or 0) * HANDELSTAGE_PRO_KALENDERTAG))
        if len(closes) > offset:
            start = closes[-1 - offset]
    if not start:
        return None
    return closes[-1] / start - 1


def index_return_fenster(idx_chart, start_datum, horizont_tage):
    """Index-Rendite ueber ein FESTES Fenster (Signaltag bis Signaltag +
    horizont_tage Kalendertage) statt bis heute - das Gegenstueck zur
    Strategie-Spur aus exit_simulation.py, die denselben festen Stichtag
    verwendet. Braucht zwingend eine Datumsreihe; ohne sie None (eine
    Naeherung waere hier irrefuehrend, weil der Vergleichswert selbst exakt
    datiert ist)."""
    from datetime import datetime, timedelta

    closes = (idx_chart or {}).get("closes") or []
    dates = (idx_chart or {}).get("dates") or []
    if not closes or not dates or len(dates) != len(closes):
        return None
    try:
        grenze = (datetime.strptime(start_datum, "%Y-%m-%d")
                  + timedelta(days=horizont_tage)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    start = ende = None
    for i, d in enumerate(dates):
        if d is None:
            continue
        if start is None and d >= start_datum:
            start = closes[i]
        if d >= grenze:
            ende = closes[i]
            break
    if not start or not ende:
        return None
    return ende / start - 1


def ergaenze_edge(stats, edge_rets):
    """Haengt die Index-Kennzahlen an ein fertiges _stats()-Ergebnis - gleiche
    Feldnamen wie score_backtest.py sie seit jeher schreibt, damit
    backtest-vergleich.html alle Engines gleich behandeln kann:
      edge_idx_avg = Ø-Vorsprung gegenueber dem Index (Prozentpunkte)
      edge_idx_win = Anteil der Picks, die den Index geschlagen haben
    edge_rets: Liste von (Pick-Return - Index-Return) als Dezimalwerte."""
    if not edge_rets:
        return stats
    n = len(edge_rets)
    stats["edge_idx_avg"] = round(sum(edge_rets) / n * 100, 2)
    stats["edge_idx_win"] = round(sum(1 for r in edge_rets if r > 0) / n * 100, 1)
    stats["edge_idx_n"] = n
    return stats


def lade_index_charts(hole_chart, *args, **kwargs):
    """Holt die Leitindex-Charts einmal pro Lauf. `hole_chart` ist die
    repo-eigene Abruffunktion; zusaetzliche Argumente werden durchgereicht
    (Signal-Hub: (symbol, cache), Price-Action-Hub/Rotation-Dashboard:
    (symbol, cache, heute)). Zwei zusaetzliche Yahoo-Abrufe je Lauf."""
    charts = {}
    for markt, sym in INDEX.items():
        try:
            charts[markt] = hole_chart(sym, *args, **kwargs) or {}
        except Exception:
            charts[markt] = {}
    return charts


def edge_fuer(idx_charts, markt, signal_datum, kalendertage, pick_return):
    """Bequemer Einzelaufruf: Vorsprung eines Picks gegenueber seinem Index.
    None, wenn der Index-Zeitraum nicht bestimmbar ist."""
    markt = markt if markt in idx_charts else STANDARD_MARKT
    idx_ret = index_return(idx_charts.get(markt), signal_datum, kalendertage)
    if idx_ret is None:
        return None
    return pick_return - idx_ret
