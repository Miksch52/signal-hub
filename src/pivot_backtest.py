#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest / Evaluations-Loop fuer den Pivot-Armed-Detektor.

Misst, OB die Zustaende ARMED/BREAKOUT ueberhaupt Forward-Return bringen — sonst
automatisiert man nur eine plausible, aber unbewiesene Heuristik.

Drei Modi:

  python3 src/pivot_backtest.py            # RETRO: Walk-Forward ueber die
        126-Tage-Charts in signals.json. Liefert SOFORT ein Urteil, ist aber
        durch die Vorauswahl des Universums verzerrt (siehe Bias-Hinweis).
        Deshalb immer GEGEN eine Baseline (zufaelliger Uptrend-Tag derselben
        Aktien): nur der DELTA ARMED-minus-Baseline ist der eigentliche Edge.

  python3 src/pivot_backtest.py --log      # haengt die heutigen ARMED/BREAKOUT
        aus data/pivot.json mit Datum+Kurs ans Forward-Logbuch (lokal). Wird
        vom taeglichen Lauf automatisch aufgerufen -> baut die UNVERZERRTE
        Stichprobe ueber echte Kalenderzeit auf.

  python3 src/pivot_backtest.py --evaluate # bewertet die gereiften Logbuch-Picks
        gegen die aktuellen Yahoo-Kurse (echter Forward-Test, reift ueber Wochen).

Ausgabe: data/pivot_backtest.json (+ Konsolentabelle).
"""

import json
import os
import sys
from datetime import datetime, timezone

import pfade
import pivot

HORIZONTE = [("4W", 20), ("8W", 40), ("12W", 60)]   # Forward-Fenster in Handelstagen
RETRO_STEP = 2                                       # jeden 2. Tag (weniger Autokorrelation)
GATE_GRUENDE = {"kein Aufwaertstrend (Stage 2)", "zu wenig Historie"}


# ---------------------------------------------------------------------------
# Statistik-Helfer
# ---------------------------------------------------------------------------
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


def _ist_uptrend_tag(r):
    """True, wenn der Tag das Stage-2-Gate passiert (egal welcher Pivot-Status)."""
    if r.get("status") in ("BREAKOUT", "ARMED", "WATCH"):
        return True
    return r.get("status") == "-" and r.get("grund") not in GATE_GRUENDE


# ---------------------------------------------------------------------------
# RETRO: Walk-Forward ueber die Charts in signals.json
# ---------------------------------------------------------------------------
def _truncate(chart, t):
    """Chart-Block bis einschliesslich Tag t (SMA bleiben trailing -> korrekt)."""
    out = {"c": chart["c"][:t + 1], "v": (chart.get("v") or [])[:t + 1]}
    for k in ("s50", "s150", "s200"):
        if chart.get(k):
            out[k] = chart[k][:t + 1]
    return out


STOP_LOOKBACK = 10        # Stop = tiefster Schluss der letzten N Tage (Swing-Low)
RISK_MIN = 0.005          # entartete (zu enge) Stops ignorieren


def _r_multiple(c, t, h, stop):
    """Terminale R-Vielfache mit Stop-Logik: -1 wenn der Pfad den Stop reisst,
    sonst (Schluss nach h Tagen - Einstieg) / Anfangsrisiko."""
    entry = c[t]
    risiko = entry - stop
    if risiko <= 0 or risiko / entry < RISK_MIN:
        return None
    pfad = c[t + 1:t + h + 1]
    if any(x <= stop for x in pfad):     # zwischendurch ausgestoppt
        return -1.0
    return (c[t + h] - entry) / risiko


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


# Tuning-Splits (seit 2026-07-24): beantworten die drei offenen Fragen aus der
# ersten Retro-Messung, BEVOR pivot.py-Parameter angefasst werden.
#   BREAKOUT nah/weit  -> ist der Einstieg >4% ueber Pivot der Grund fuers
#                         negative BREAKOUT-R? (Basis fuer BREAKOUT_DEHN)
#   ARMED supply +/-   -> trennt der Supply-Score (Overhead-Angebot) die Spreu?
#   ARMED megaphone    -> sind expandierende rechte Seiten wirklich schlechter?
SPLIT_BREAKOUT_NAH = 4.0     # % ueber Pivot
SPLIT_SUPPLY_GUT = 0.7       # Supply-Score-Schwelle (1.0 = kaum Angebot oben)

SPLIT_STATI = ("BREAKOUT_nah", "BREAKOUT_weit",
               "ARMED_supply_gut", "ARMED_supply_schwach",
               "ARMED_megaphone", "ARMED_sauber")


def _split_stati(r):
    """Zusatz-Kohorten fuer einen klassifizierten Tag (Liste von Labels)."""
    st = r.get("status")
    out = []
    if st == "BREAKOUT" and r.get("dist_pct") is not None:
        out.append("BREAKOUT_nah" if r["dist_pct"] <= SPLIT_BREAKOUT_NAH else "BREAKOUT_weit")
    if st == "ARMED":
        sup = r.get("supply_score")
        if sup is not None:
            out.append("ARMED_supply_gut" if sup >= SPLIT_SUPPLY_GUT else "ARMED_supply_schwach")
        if r.get("megaphone") is not None:
            out.append("ARMED_megaphone" if r["megaphone"] else "ARMED_sauber")
    return out


def retro():
    with open(pfade.SIGNALS_JSON, encoding="utf-8") as f:
        treffer = json.load(f).get("treffer", [])

    # Sammeleimer: status -> horizon-label -> [returns] und parallel [R-Vielfache]
    stati = ("BREAKOUT", "ARMED", "WATCH") + SPLIT_STATI
    eimer = {s: {h: [] for h, _ in HORIZONTE} for s in stati}
    eimer_R = {s: {h: [] for h, _ in HORIZONTE} for s in stati}
    baseline = {h: [] for h, _ in HORIZONTE}
    baseline_R = {h: [] for h, _ in HORIZONTE}
    n_charts = 0

    for tr in treffer:
        ch = tr.get("chart") or {}
        c = ch.get("c") or []
        if len(c) < 60 + HORIZONTE[0][1]:
            continue
        n_charts += 1
        for t in range(59, len(c) - HORIZONTE[0][1], RETRO_STEP):
            r = pivot.klassifiziere(_truncate(ch, t))
            uptrend = _ist_uptrend_tag(r)
            st = r.get("status")
            ziele = ([st] if st in eimer else []) + _split_stati(r)
            stop = min(c[max(0, t - STOP_LOOKBACK + 1):t + 1])   # einheitlicher Stop
            for label, h in HORIZONTE:
                if t + h >= len(c) or not c[t]:
                    continue
                fwd = c[t + h] / c[t] - 1
                rr = _r_multiple(c, t, h, stop)
                if uptrend:
                    baseline[label].append(fwd)
                    if rr is not None:
                        baseline_R[label].append(rr)
                for z in ziele:
                    eimer[z][label].append(fwd)
                    if rr is not None:
                        eimer_R[z][label].append(rr)

    ergebnis = {"baseline": {h: _stats(baseline[h]) for h, _ in HORIZONTE}}
    for label, _ in HORIZONTE:
        ergebnis["baseline"][label]["avg_R"] = _mean(baseline_R[label])
    for st in eimer:
        ergebnis[st] = {}
        for label, _ in HORIZONTE:
            s = _stats(eimer[st][label])
            s["avg_R"] = _mean(eimer_R[st][label])
            b = ergebnis["baseline"][label]
            if s["n"] and b["win"] is not None:
                s["edge_win"] = round(s["win"] - b["win"], 1)
                s["edge_avg"] = round(s["avg"] - b["avg"], 2)
                if s["avg_R"] is not None and b["avg_R"] is not None:
                    s["edge_R"] = round(s["avg_R"] - b["avg_R"], 2)   # der eigentliche Test
            ergebnis[st][label] = s
    return ergebnis, n_charts


# ---------------------------------------------------------------------------
# FORWARD-LOG: heutige Picks festhalten
# ---------------------------------------------------------------------------
def _logbuch_load():
    if os.path.exists(pfade.PIVOT_LOGBUCH):
        try:
            return json.load(open(pfade.PIVOT_LOGBUCH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _logbuch_save(lb):
    with open(pfade.PIVOT_LOGBUCH, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)


def log_heute():
    if not os.path.exists(pfade.PIVOT_JSON):
        print("Keine pivot.json -> nichts zu loggen.")
        return
    daten = json.load(open(pfade.PIVOT_JSON, encoding="utf-8"))
    heute = datetime.now().strftime("%Y-%m-%d")
    lb = _logbuch_load()
    bekannt = {(e["datum"], e["ticker"], e["status"]) for e in lb}
    neu = 0
    for e in daten.get("treffer", []):
        if e.get("pivot_status") not in ("ARMED", "BREAKOUT"):
            continue
        key = (heute, e.get("ticker"), e.get("pivot_status"))
        if key in bekannt:
            continue
        lb.append({
            "datum": heute, "ticker": e.get("ticker"),
            "yahoo_symbol": e.get("yahoo_symbol"), "markt": e.get("markt"),
            "status": e.get("pivot_status"), "qualitaet": e.get("qualitaet"),
            "preis_signal": e.get("preis"), "pivot": e.get("pivot"),
            "realisiert": None,        # wird von --evaluate gefuellt
        })
        neu += 1
    lb = lb[-5000:]
    _logbuch_save(lb)
    print(f"Forward-Logbuch: {neu} neue Picks ergaenzt (gesamt {len(lb)}).")


# ---------------------------------------------------------------------------
# EVALUATE: gereifte Logbuch-Picks gegen aktuelle Kurse
# ---------------------------------------------------------------------------
def _bucket(elapsed_tage):
    """Kalendertage -> Horizont-Label (oder None, wenn noch nicht reif)."""
    if elapsed_tage >= 78:
        return "12W"
    if elapsed_tage >= 50:
        return "8W"
    if elapsed_tage >= 21:
        return "4W"
    return None


def evaluate():
    try:
        import scorer
    except Exception as ex:
        print(f"  ! scorer-Import fehlgeschlagen ({ex}) -> kein Yahoo-Abruf moeglich.")
        return {}
    lb = _logbuch_load()
    if not lb:
        print("Forward-Logbuch leer -> erst --log sammeln lassen.")
        return {}
    cache = scorer.lade_cache()
    heute = datetime.now().date()
    # Qualitaets-Split (seit 2026-07-24): Basis fuer die Kalibrierung von
    # config.pivot.armed_schwelle - zeigt, ob hohe qualitaet-Werte forward
    # tatsaechlich besser laufen (erst ab ~8 Picks je Kohorte aussagekraeftig).
    basis_stati = ("BREAKOUT", "ARMED")
    qual_stati = ("ARMED_q70+", "ARMED_q<70")
    eimer = {s: {h: [] for h, _ in HORIZONTE} for s in basis_stati + qual_stati}
    aktuell = {}
    for e in lb:
        try:
            tage = (heute - datetime.strptime(e["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        bk = _bucket(tage)
        if not bk or e.get("status") not in basis_stati:
            continue
        sym = e.get("yahoo_symbol") or e.get("ticker")
        if sym not in aktuell:
            d = scorer.hole_chart_cached(sym, cache)
            aktuell[sym] = (d.get("closes")[-1] if d and d.get("closes") else None)
        kurs = aktuell[sym]
        if not kurs or not e.get("preis_signal"):
            continue
        ret = kurs / e["preis_signal"] - 1
        e["realisiert"] = {"horizont": bk, "return_pct": round(ret * 100, 2),
                           "stand": heute.strftime("%Y-%m-%d")}
        eimer[e["status"]][bk].append(ret)
        if e["status"] == "ARMED" and e.get("qualitaet") is not None:
            eimer["ARMED_q70+" if e["qualitaet"] >= 70 else "ARMED_q<70"][bk].append(ret)
    scorer.speichere_cache(cache)
    _logbuch_save(lb)
    return {st: {h: _stats(eimer[st][h]) for h, _ in HORIZONTE} for st in eimer}


SCHWELLE_PUSH = 8        # ab so vielen gereiften Picks je Kohorte gilt der Test als "reif"


def _eval_state_load():
    if os.path.exists(pfade.PIVOT_EVAL_STATE):
        try:
            return json.load(open(pfade.PIVOT_EVAL_STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def push_reife(fr):
    """Push, sobald eine Kohorte (Status×Horizont) genug gereifte Picks hat —
    so meldet sich der Forward-Test nach ~6-8 Wochen von selbst. Anti-Spam:
    pro Kohorte nur beim ersten Erreichen der Schwelle bzw. wenn n sich ~verdoppelt."""
    state = _eval_state_load()
    pushed = state.get("gepusht", {})    # key 'STATUS_HOR' -> zuletzt gepushtes n
    zeilen, neu = [], False
    for st in fr:
        for label, _ in HORIZONTE:
            s = fr[st][label]
            n = s.get("n") or 0
            if n < SCHWELLE_PUSH:
                continue
            key = f"{st}_{label}"
            last = pushed.get(key, 0)
            if last == 0 or n >= 1.5 * last:      # erstmals reif oder deutlich gewachsen
                zeilen.append(f"{'🎯' if st=='ARMED' else '🚀'} {st} {label}: "
                              f"n={n} Win {s['win']}% Ø{s['avg']:+}% ØR {s.get('avg_R')}")
                pushed[key] = n
                neu = True
    if not neu:
        print("Forward-Test: keine neue Reife-Schwelle erreicht -> kein Push.")
        return
    try:
        import notify
        c = notify.lade_config()
        b = c.get("benachrichtigung", {})
        thema, server = b.get("ntfy_thema", ""), b.get("ntfy_server", "https://ntfy.sh")
        if thema and "NOCH" not in thema.upper():
            notify.sende_ntfy(server, thema, "📊 Pivot-Forward-Test reif",
                              "\n".join(zeilen) + "\n(realisiert, unverzerrt — jetzt aussagekraeftig)",
                              tags="bar_chart", prio="default")
            print("Push gesendet:\n  " + "\n  ".join(zeilen))
    except Exception as ex:
        print(f"  ! Push fehlgeschlagen: {ex}")
    state["gepusht"] = pushed
    state["stand"] = datetime.now().strftime("%Y-%m-%d")
    with open(pfade.PIVOT_EVAL_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
def _druck_tabelle(titel, block, mit_edge=False):
    print(f"\n{titel}")
    print(f"{'Status':10s}{'Hor':5s}{'n':>6s}{'Win%':>7s}{'Ø%':>7s}{'ØR':>6s}"
          + ("   ΔWin   ΔØ%    ΔR" if mit_edge else ""))
    for st in block:
        if st == "baseline":
            continue
        for label, _ in HORIZONTE:
            s = block[st][label]
            if not s["n"]:
                continue
            zeile = (f"{st:10s}{label:5s}{s['n']:6d}{(s['win'] or 0):7.1f}"
                     f"{(s['avg'] or 0):7.2f}{(s.get('avg_R') or 0):6.2f}")
            if mit_edge and "edge_win" in s:
                zeile += (f"  {s['edge_win']:+6.1f}{s['edge_avg']:+7.2f}"
                          f"{(s.get('edge_R') or 0):+6.2f}")
            print(zeile)
    if "baseline" in block:
        print("  Baseline (alle Uptrend-Tage):")
        for label, _ in HORIZONTE:
            b = block["baseline"][label]
            if b["n"]:
                print(f"  {'(Base)':10s}{label:5s}{b['n']:6d}{(b['win'] or 0):7.1f}"
                      f"{(b['avg'] or 0):7.2f}{(b.get('avg_R') or 0):6.2f}")


def main():
    args = sys.argv[1:]

    if "--log" in args:
        log_heute()
        return

    if "--evaluate" in args:
        fr = evaluate()
        if fr and "--nopush" not in args:
            push_reife(fr)
        bestand = {}
        if os.path.exists(pfade.PIVOT_BACKTEST):
            try:
                bestand = json.load(open(pfade.PIVOT_BACKTEST, encoding="utf-8"))
            except Exception:
                bestand = {}
        bestand["forward_realisiert"] = fr
        bestand["forward_stand"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(pfade.PIVOT_BACKTEST, "w", encoding="utf-8") as f:
            json.dump(bestand, f, ensure_ascii=False, indent=1)
        if fr:
            _druck_tabelle("=== FORWARD realisiert (unverzerrt, reift ueber Zeit) ===", fr)
        print(f"\nGespeichert: {pfade.PIVOT_BACKTEST}")
        return

    # Default: RETRO
    ergebnis, n = retro()
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modus": "retrospektiv_walkforward",
        "bias_hinweis": ("Universum = heutige Momentum-Auswahl (signals.json). Diese "
                         "Aktien sind hier, WEIL sie zuletzt liefen -> absolute "
                         "Win-Raten sind nach oben verzerrt. Aussagekraeftig ist nur "
                         "der DELTA gegen die Baseline (zufaelliger Uptrend-Tag "
                         "derselben Aktien)."),
        "n_charts": n,
        "schritt_tage": RETRO_STEP,
        "ergebnis": ergebnis,
    }
    with open(pfade.PIVOT_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    _druck_tabelle(f"=== RETRO Walk-Forward ueber {n} Charts (Bias: s. Hinweis) ===",
                   ergebnis, mit_edge=True)
    print(f"\nGespeichert: {pfade.PIVOT_BACKTEST}")


if __name__ == "__main__":
    main()
