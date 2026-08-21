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
from datetime import datetime, timezone

import pfade

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
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
    bekannt = {(e["datum"], e["markt"]) for e in lb}
    cache = scorer.lade_cache()
    neu = 0
    for markt, sym in INDEX_SYMBOL.items():
        key = (heute, markt)
        if key in bekannt:
            continue
        r = regime.get(markt) or {}
        if r.get("ampel") not in AMPELN:
            continue
        d = scorer.hole_chart_cached(sym, cache)
        kurs = d.get("closes")[-1] if d and d.get("closes") else None
        if not kurs:
            continue
        lb.append({
            "datum": heute, "markt": markt, "ampel": r["ampel"],
            "index_symbol": sym, "index_kurs": kurs,
        })
        neu += 1
    scorer.speichere_cache(cache)
    lb = lb[-2000:]
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
    aktuell = {}
    for e in lb:
        try:
            tage = (heute_dt - datetime.strptime(e["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        bk = _bucket(tage)
        if not bk or e.get("ampel") not in eimer:
            continue
        sym = e.get("index_symbol")
        if sym not in aktuell:
            d = scorer.hole_chart_cached(sym, cache)
            aktuell[sym] = (d.get("closes")[-1] if d and d.get("closes") else None)
        kurs = aktuell[sym]
        if not kurs or not e.get("index_kurs"):
            continue
        ret = kurs / e["index_kurs"] - 1
        eimer[e["ampel"]][bk].append(ret)
        einzelfaelle.append({
            "markt": e["markt"], "ampel": e["ampel"], "datum": e["datum"],
            "index_symbol": sym, "index_kurs_signal": e["index_kurs"],
            "horizont": bk, "return_pct": round(ret * 100, 2),
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
        "hinweis": ("Forward-Test: Leitindex-Kurs am Tag der Regime-Einstufung "
                    "(gruen/gelb/rot) vs. aktueller Kurs, Kohorten nach Alter "
                    "(>=21/50/78 Kalendertage). Unverzerrt (Einstufung stand vor "
                    "dem Ergebnis fest). Kein Retro-Modus (siehe Docstring in "
                    "regime_backtest.py) - die Stichprobe wird erst ueber "
                    "Kalenderzeit aussagekraeftig, waechst aber nur um zwei "
                    "Eintraege pro Tag (ein Markt-Regime-Wert je Markt)."),
        "forward_realisiert": fr,
        "forward_einzelfaelle": einzelfaelle,
    }
    _schreibe(out)
    if einzelfaelle:
        print(f"\n=== Markt-Regime Forward-Test ({len(einzelfaelle)} gereifte Einzelfaelle) ===")
        _druck_tabelle(fr)
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
