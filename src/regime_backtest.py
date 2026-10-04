#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forward-Test fuer das Markt-Regime (scorer.py::markt_regime()).

Bislang wird jeder einzelne Signal-Typ (Pivot, Score, Hebel-Ampel, Price-
Action-Muster, Rotation-Setups) unverzerrt forward-getestet - das
Marktregime selbst (gruen/gelb/rot-Ampel, entscheidet ueber 25/50/100 %
Exposure und ist damit der groesste Einzelhebel im gesamten System) aber
nie. Misst, OB "gruen" tatsaechlich bessere Index-Folgerenditen liefert als
"gelb"/"rot" - sonst automatisiert das System nur eine plausible, aber
unbewiesene Heuristik, genau wie der Pivot-Detektor vor seinem eigenen
Forward-Test.

Gleiches Grundprinzip wie Price-Action-Hub/src/hebel_backtest.py (--log/
--evaluate, kein Retro-Modus): eine rueckwirkende Rekonstruktion muesste
distribution_days()/follow_through_day() ueber jeden historischen Tag neu
laufen lassen - fuer einen ersten Baustein unverhaeltnismaessig aufwendig.
Die Stichprobe reift stattdessen ueber Kalenderzeit; pro Tag kommen nur
zwei neue Eintraege dazu (ein Regime-Wert je Markt), die Reife-Schwelle
(SCHWELLE_PUSH-Groessenordnung wie beim Pivot-Backtest) wird also langsamer
erreicht als bei den ticker-basierten Backtests.

  python3 src/regime_backtest.py --log       # haengt das heutige Regime
        (aus signals.json::marktregime, je Markt) mit Datum + Leitindex-
        Kurs ans Forward-Logbuch.
  python3 src/regime_backtest.py --evaluate  # bewertet gereifte Eintraege
        (>=21/50/78 Kalendertage) gegen den aktuellen Index-Kurs.

run.py ruft log_und_evaluate() bei jedem Lauf auf (zwei Maerkte, minimale
Stichprobe pro Tag - kein eigenes Scheduling wie beim woechentlichen
Pivot-Evaluations-Loop noetig).

Ausgabe: data/regime_backtest.json (+ .js-Fallback).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import index_vergleich
import pfade

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
DUBLETTEN = 0   # vom letzten evaluate() herausgefilterte Dubletten (seit 2026-10-04)
AMPELN = ("gruen", "gelb", "rot")
# Leitindex je Markt fuer die Renditemessung - identisch zum ersten Eintrag
# von config.json::maerkte.*.index_yahoo (dem "leit"-Index in scorer.py::
# markt_regime()), damit Ampel-Ursache und gemessene Rendite konsistent
# denselben Index meinen.
INDEX_SYMBOL = {"USA": "^GSPC", "Europa": "^STOXX"}


def _stats(rets):
    if not rets:
        return {"n": 0, "win": None, "avg": None, "median": None}
    rets = sorted(rets)
    n = len(rets)
    win = sum(1 for r in rets if r > 0) / n
    avg = sum(rets) / n
    median = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2
    return {"n": n, "win": round(win * 100, 1),
            "avg": round(avg * 100, 2), "median": round(median * 100, 2)}


def _bucket(elapsed_tage):
    """Kalendertage -> reifster Horizont (oder None, wenn noch zu jung)."""
    if elapsed_tage >= 78:
        return "12W"
    if elapsed_tage >= 50:
        return "8W"
    if elapsed_tage >= 21:
        return "4W"
    return None


# ---------------------------------------------------------------------------
def _logbuch_load():
    if os.path.exists(pfade.REGIME_LOGBUCH):
        try:
            return json.load(open(pfade.REGIME_LOGBUCH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _logbuch_save(lb):
    with open(pfade.REGIME_LOGBUCH, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)


def log_heute():
    if not os.path.exists(pfade.SIGNALS_JSON):
        print("Keine signals.json -> nichts zu loggen.")
        return
    try:
        import scorer
    except Exception as ex:
        print(f"  ! scorer-Import fehlgeschlagen ({ex}) -> kein Yahoo-Abruf moeglich.")
        return
    regime = json.load(open(pfade.SIGNALS_JSON, encoding="utf-8")).get("marktregime") or {}
    heute = datetime.now().strftime("%Y-%m-%d")
    lb = _logbuch_load()
    # Doppel-Pruefung ueber den belegten Handelstag des Leitindex statt das
    # Schreibdatum (seit 2026-10-04, siehe index_vergleich.logbuch_schluessel):
    # Sa-, So- und Mo-Lauf sehen dieselbe Freitags-Einstufung.
    bekannt = {index_vergleich.logbuch_schluessel(e, "markt") for e in lb}
    cache = scorer.lade_cache()
    neu = 0
    for markt, sym in INDEX_SYMBOL.items():
        r = regime.get(markt) or {}
        if r.get("ampel") not in AMPELN:
            continue
        d = scorer.hole_chart_cached(sym, cache)
        eintrag = {"datum": heute, "markt": markt}
        ht = index_vergleich.letzter_handelstag(d)
        if ht:
            eintrag["handelstag"] = ht
        key = index_vergleich.logbuch_schluessel(eintrag, "markt")
        if key in bekannt:
            continue
        kurs = d.get("closes")[-1] if d and d.get("closes") else None
        if not kurs:
            continue
        eintrag.update({"ampel": r["ampel"], "index_symbol": sym, "index_kurs": kurs})
        lb.append(eintrag)
        bekannt.add(key)
        neu += 1
    scorer.speichere_cache(cache)
    # Aufbewahrung nach ALTER statt nach Eintragszahl (seit 2026-09-13). Die
    # fruehere Kappe "lb[-N:]" skalierte mit der Treffermenge: bei ~417
    # Hebel- bzw. ~172 Pivot-Eintraegen je Tag behielt sie in der Cloud nur
    # 12 bzw. 29 Tage und loeschte Episoden, bevor sie 4W/8W/12W erreichen
    # konnten (Hebel-Backtest dauerhaft n=0, Pivot nie 8W/12W; Pivot-
    # Episoden 02.07.-15.08.2026 dadurch unwiederbringlich verloren).
    # 120 Tage = laengster Horizont (78 Kalendertage) plus Puffer.
    aufbewahrung_tage = 120
    grenze = (datetime.now().date() - timedelta(days=aufbewahrung_tage)).isoformat()
    lb = [e for e in lb if (e.get("datum") or "") >= grenze]
    _logbuch_save(lb)
    print(f"Regime-Forward-Logbuch: {neu} neue Eintraege ergaenzt (gesamt {len(lb)}).")


# ---------------------------------------------------------------------------
def evaluate():
    try:
        import scorer
    except Exception as ex:
        print(f"  ! scorer-Import fehlgeschlagen ({ex}) -> kein Yahoo-Abruf moeglich.")
        return {}, []
    lb = _logbuch_load()
    if not lb:
        print("Regime-Logbuch leer -> erst --log sammeln lassen.")
        return {}, []
    cache = scorer.lade_cache()
    heute_dt = datetime.now().date()
    eimer = {a: {h: [] for h, _ in HORIZONTE} for a in AMPELN}
    einzelfaelle = []
    charts = {}
    # Dubletten-Filter (seit 2026-10-04, siehe index_vergleich.ist_dublette):
    # eine Einstufung je Markt und Startbar - Sa/So/Mo bzw. Feiertag +
    # Folgetag zaehlten vorher mehrfach mit identischem Ergebnis.
    global DUBLETTEN
    DUBLETTEN = 0
    gesehen = set()
    for e in lb:
        try:
            tage = (heute_dt - datetime.strptime(e["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        if e.get("ampel") not in eimer:
            continue
        sym = e.get("index_symbol")
        if sym not in charts:
            charts[sym] = scorer.hole_chart_cached(sym, cache) or {}
        # Feste Fenster (seit 2026-09-13, Systempruefung Punkt 2): Leitindex vom
        # Tag der Einstufung bis genau 21/50/78 Kalendertage spaeter - jede
        # Einstufung zaehlt in jedem erreichten Horizont, ihr Wert bleibt fest.
        start = index_vergleich.start_datum(e)
        rets = index_vergleich.fenster_returns(charts[sym], start, e.get("index_kurs"))
        bk, ret = index_vergleich.laengster_horizont(rets)
        if bk is None:
            continue
        if index_vergleich.ist_dublette(gesehen, charts[sym], start, e["markt"]):
            DUBLETTEN += 1
            continue
        for h, r in rets.items():
            if r is not None:
                eimer[e["ampel"]][h].append(r)
        einzelfaelle.append({
            "markt": e["markt"], "ampel": e["ampel"], "datum": e["datum"],
            "handelstag": e.get("handelstag"),
            "index_symbol": sym, "index_kurs_signal": e["index_kurs"],
            "horizont": bk, "return_pct": round(ret * 100, 2),
            "fenster": {h: round(r * 100, 2) for h, r in rets.items() if r is not None},
        })
    scorer.speichere_cache(cache)
    fr = {a: {h: _stats(eimer[a][h]) for h, _ in HORIZONTE} for a in eimer}
    return fr, einzelfaelle


# ---------------------------------------------------------------------------
def _schreibe(out):
    with open(pfade.REGIME_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(pfade.REGIME_BACKTEST_JS, "w", encoding="utf-8") as f:
        f.write("window.REGIME_BACKTEST_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")


def _druck_tabelle(fr):
    print(f"{'Ampel':6s}{'Hor':5s}{'n':>5s}{'Win%':>7s}{'Ø%':>8s}")
    for ampel in AMPELN:
        for label, _ in HORIZONTE:
            s = fr.get(ampel, {}).get(label) or {}
            if not s.get("n"):
                continue
            print(f"{ampel:6s}{label:5s}{s['n']:5d}{s['win']:7.1f}{s['avg']:8.2f}")


def log_und_evaluate():
    """Bequemer Einstiegspunkt fuer run.py: loggen + auswerten + schreiben in
    einem Aufruf."""
    log_heute()
    fr, einzelfaelle = evaluate()
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hinweis": ("Forward-Test mit festen Fenstern (seit 2026-09-13): Leitindex-Kurs "
                    "am Tag der Regime-Einstufung (gruen/gelb/rot) vs. Schlusskurs genau "
                    "21/50/78 Kalendertage spaeter; jede Einstufung zaehlt in jedem "
                    "erreichten Horizont, ihr Wert bleibt danach fest. Unverzerrt "
                    "(Einstufung stand vor dem Ergebnis fest). Kein Retro-Modus (siehe "
                    "Docstring in regime_backtest.py) - die Stichprobe waechst nur um "
                    "zwei Eintraege pro Tag (ein Markt-Regime-Wert je Markt). Je Markt und Starttag zaehlt nur eine Einstufung "
                    "(seit 2026-10-04: Sa/So/Mo bzw. Feiertag + Folgetag waren vorher "
                    "Dubletten)."),
        "forward_realisiert": fr,
        "forward_einzelfaelle": einzelfaelle,
        "dubletten_gefiltert": DUBLETTEN,
    }
    _schreibe(out)
    if einzelfaelle:
        print(f"\n=== Markt-Regime Forward-Test ({len(einzelfaelle)} gereifte Einzelfaelle) ===")
        _druck_tabelle(fr)
    if DUBLETTEN:
        print(f"Dubletten herausgefiltert (gleicher Starttag): {DUBLETTEN}")
    print(f"Gespeichert: {pfade.REGIME_BACKTEST}")
    return fr, einzelfaelle


def main():
    args = sys.argv[1:]
    if "--log" in args:
        log_heute()
        return
    if "--evaluate" in args:
        log_und_evaluate()
        return
    print("Nutzung: regime_backtest.py --log | --evaluate")


if __name__ == "__main__":
    main()
