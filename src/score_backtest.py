#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trefferquoten-Auswertung fuer den Momentum-Score (Tier A/B).

Der Scorer haelt seit Anfang taeglich alle Treffer ab "Beobachten"-Schwelle
im Logbuch fest (pfade.LOGBUCH, Feld tier seit 2026-07). Dieses Skript ist
das Gegenstueck zu pivot_backtest.py --evaluate, nur fuer den Score selbst:
es beantwortet, OB Tier A (Kauf-Kandidat) nach 4/8/12 Wochen tatsaechlich
besser laeuft als Tier B (Beobachten) - und ob beide ueberhaupt besser
laufen als ihr Markt-Index im selben Zeitraum (edge_idx). Erst mit dieser
Rueckkopplung lassen sich die score_gewichte datengetrieben statt nach
Plausibilitaet setzen.

Methodik:
  - Episoden-Dedupe: ein Ticker steht taeglich im Logbuch, solange er
    Kandidat bleibt -> nur die ERSTE Nennung je (Ticker, Tier)-Episode wird
    gewertet; erst nach EPISODE_LUECKE Tagen Pause zaehlt ein Wiederauftauchen
    als neue Episode (sonst misst man denselben Trade hundertfach).
  - Forward-Return: Kurs bei Erstnennung (im Logbuch gespeichert) gegen den
    aktuellen Yahoo-Kurs, Kohorten nach Alter (>=21/50/78 Kalendertage).
    Unverzerrt, weil die Auswahl VOR dem Ergebnis feststand.
  - edge_idx: Differenz zum Markt-Index (^GSPC/^STOXX) im selben Zeitraum
    (Handelstage-Offset ~ Kalendertage * 5/7 - Naeherung, fuer Kohorten-
    Statistik ausreichend). Ohne diesen Abzug saehe in einem Bullenmarkt
    JEDER Score gut aus.

Teilt den Tages-Yahoo-Cache mit dem Scorer -> direkt nach einem Scorer-Lauf
kosten fast alle Kursabrufe nichts. Laeuft deshalb taeglich in run.py mit.

Ausgabe: data/score_backtest.json (Dashboard-Panel "Trefferquoten") + Tabelle.

Test:  python3 src/score_backtest.py
"""

import json
import os
import sys
from datetime import datetime, timezone

import pfade

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
EPISODE_LUECKE = 30       # Tage ohne Nennung, ab denen ein Ticker als neue Episode zaehlt
INDEX = {"USA": "^GSPC", "Europa": "^STOXX"}


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
    for label, mindest in reversed(HORIZONTE):
        if elapsed_tage >= mindest:
            return label
    return None


def lade_logbuch():
    if not os.path.exists(pfade.LOGBUCH):
        return []
    try:
        return json.load(open(pfade.LOGBUCH, encoding="utf-8"))
    except Exception:
        return []


def episoden(lb, schwellen):
    """Erste Nennung je (Ticker, Tier)-Episode, chronologisch."""
    picks = []
    zuletzt = {}    # (ticker, tier) -> date der letzten Nennung
    for tag in sorted(lb, key=lambda e: e.get("datum") or ""):
        try:
            datum = datetime.strptime(tag["datum"], "%Y-%m-%d").date()
        except Exception:
            continue
        for p in tag.get("treffer", []):
            if not p.get("ticker") or not p.get("preis"):
                continue
            # Aeltere Logbuch-Eintraege (vor 2026-07) haben noch kein tier-Feld
            # -> aus dem gespeicherten Score + aktuellen Schwellen ableiten.
            tier = p.get("tier") or ("A" if p["score"] >= schwellen["kauf_kandidat"] else "B")
            key = (p["ticker"], tier)
            last = zuletzt.get(key)
            if last is None or (datum - last).days > EPISODE_LUECKE:
                picks.append({"ticker": p["ticker"], "tier": tier,
                              "datum": tag["datum"], "preis": p["preis"],
                              "markt": p.get("markt"),
                              # seit 2026-08-02 im Logbuch vorhanden (aeltere Eintraege: leer) -
                              # Basis fuer eine kuenftige Pro-Faktor-Auswertung, siehe Docstring oben.
                              "faktoren": p.get("faktoren")})
            zuletzt[key] = datum
    return picks


def evaluiere(picks):
    import scorer
    cache = scorer.lade_cache()
    heute = datetime.now().date()

    # Index-Charts einmal holen (fuer edge_idx)
    idx_closes = {}
    for markt, sym in INDEX.items():
        d = scorer.hole_chart_cached(sym, cache)
        idx_closes[markt] = d["closes"] if d else []

    kurs = {}       # symbol -> aktueller Schlusskurs (oder None)
    eimer = {t: {h: [] for h, _ in HORIZONTE} for t in ("A", "B")}
    eimer_edge = {t: {h: [] for h, _ in HORIZONTE} for t in ("A", "B")}
    gewertet = 0
    for p in picks:
        try:
            tage = (heute - datetime.strptime(p["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        bk = _bucket(tage)
        if not bk or p["tier"] not in eimer:
            continue
        sym = p["ticker"]
        if sym not in kurs:
            d = scorer.hole_chart_cached(sym, cache)
            kurs[sym] = (d["closes"][-1] if d and d.get("closes") else None)
        if not kurs[sym]:
            continue
        ret = kurs[sym] / p["preis"] - 1
        eimer[p["tier"]][bk].append(ret)
        gewertet += 1
        # Index-Return im selben Zeitraum (Handelstage ~ Kalendertage * 5/7)
        ic = idx_closes.get(p.get("markt")) or idx_closes.get("USA") or []
        offset = max(1, round(tage * 5 / 7))
        if len(ic) > offset and ic[-1 - offset]:
            idx_ret = ic[-1] / ic[-1 - offset] - 1
            eimer_edge[p["tier"]][bk].append(ret - idx_ret)

    scorer.speichere_cache(cache)

    ergebnis = {}
    for tier in eimer:
        ergebnis[tier] = {}
        for label, _ in HORIZONTE:
            s = _stats(eimer[tier][label])
            e = _stats(eimer_edge[tier][label])
            if e["n"]:
                s["edge_idx_avg"] = e["avg"]        # Ø-Vorsprung vs. Markt-Index
                s["edge_idx_win"] = e["win"]        # % der Picks, die den Index schlagen
            ergebnis[tier][label] = s
    return ergebnis, gewertet


def main():
    cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
    schwellen = cfg["score_schwellen"]
    lb = lade_logbuch()
    if not lb:
        # Cloud-Lauf oder frische Maschine: Logbuch lebt lokal (pfade.LOKAL)
        # und baut sich erst ueber Kalenderzeit auf - kein Fehler.
        print("Score-Logbuch leer/fehlt -> nichts auszuwerten (reift ueber Zeit).")
        return

    picks = episoden(lb, schwellen)
    reif = [p for p in picks
            if _bucket((datetime.now().date()
                        - datetime.strptime(p["datum"], "%Y-%m-%d").date()).days)]
    print(f"Logbuch: {len(lb)} Tage, {len(picks)} Episoden, davon {len(reif)} reif (>=21 Tage).")
    if not reif:
        print("Noch keine Episode alt genug -> keine Auswertung.")
        return

    ergebnis, gewertet = evaluiere(picks)

    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "logbuch_tage": len(lb),
        "episoden": len(picks),
        "gewertet": gewertet,
        "horizonte_kalendertage": {h: t for h, t in HORIZONTE},
        "hinweis": ("Forward-Test: Kurs bei ERSTER Logbuch-Nennung je Episode vs. "
                    "aktueller Kurs, Kohorten nach Alter. Unverzerrt (Auswahl stand "
                    "vor dem Ergebnis fest). edge_idx_* = Vergleich zum Markt-Index "
                    "im selben Zeitraum - nur ein positiver Index-Vorsprung belegt "
                    "echten Selektions-Edge, nicht bloss einen steigenden Markt."),
        "ergebnis": ergebnis,
    }
    with open(pfade.SCORE_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"\n=== Trefferquoten Momentum-Score (Forward, {gewertet} gewertete Episoden) ===")
    print(f"{'Tier':6s}{'Hor':5s}{'n':>5s}{'Win%':>7s}{'Ø%':>8s}{'Med%':>8s}{'ØvsIdx':>9s}{'>Idx%':>7s}")
    for tier in ("A", "B"):
        for label, _ in HORIZONTE:
            s = ergebnis[tier][label]
            if not s["n"]:
                continue
            print(f"{tier:6s}{label:5s}{s['n']:5d}{(s['win'] or 0):7.1f}"
                  f"{(s['avg'] or 0):8.2f}{(s['median'] or 0):8.2f}"
                  f"{(s.get('edge_idx_avg') if s.get('edge_idx_avg') is not None else 0):+9.2f}"
                  f"{(s.get('edge_idx_win') if s.get('edge_idx_win') is not None else 0):7.1f}")
    print(f"\nGespeichert: {pfade.SCORE_BACKTEST}")


if __name__ == "__main__":
    main()
