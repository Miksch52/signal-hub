#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pro-Faktor-Erfolgsanalyse fuer den Momentum-Score (Signal-Hub/src/scorer.py).

Ergaenzt score_backtest.py (das nur Tier A/B vergleicht) um die eigentlich
interessante Frage: WELCHE der 11 Score-Faktoren liefern tatsaechlich einen
Forward-Return-Edge, welche tragen aktuell nichts (oder sogar negativ) zum
Score bei? Nutzt dieselben Logbuch-Episoden wie score_backtest.py (Modul
wiederverwendet, siehe Import unten), bucketet aber statt nach Tier nach
Faktor-Ampel (gruen vs. gelb/rot) je einzelnem Faktor.

Braucht das faktoren-Feld, das der Scorer seit 2026-08-02 pro Logbuch-Pick
mitspeichert (Performance-/Scoring-Review) - aeltere Episoden ohne dieses
Feld werden uebersprungen. Da Episoden erst ab 21 Tagen als "reif" gelten,
liefert dieses Skript realistisch erst ab ca. Mitte September 2026 (6 Wochen
nach Einfuehrung des Faktor-Loggings) eine erste Auswertung - bis dahin meldet
es "noch keine Episode mit Faktoren alt genug" und tut sonst nichts (kein
Fehler, reift ueber Zeit wie score_backtest.py/pivot_backtest.py).

Methodik: je Faktor werden die Episoden in "gruen" vs. "nicht gruen"
(gelb+rot) gesplittet, pro Horizont der Forward-Return verglichen. Ein
Faktor mit echtem Vorhersagewert sollte im "gruen"-Eimer klar bessere
Win-Rate/Ø-Return zeigen als im "nicht-gruen"-Eimer (edge_win). Faktoren
nahe 0 oder negativ tragen aktuell nicht zum Score bei - Kandidaten fuer
Gewicht-Reduktion in config.json::score_gewichte.

Laeuft taeglich mit (run.py, analog score_backtest.py) - kein gesondertes
Scheduling noetig, es reift einfach mit den Logbuch-Daten.

Ausgabe: data/score_faktoren_backtest.json (+ Konsolentabelle).

Test: python3 src/score_faktoren_backtest.py
"""
import json
import os
import sys
from datetime import datetime, timezone

import pfade
import score_backtest as sb   # episoden()/HORIZONTE/_bucket/_stats wiederverwenden

FAKTOR_NAMEN = [
    "stage2_trend", "relative_staerke", "naehe_52w_hoch", "basis_konsolidierung",
    "volumen_bestaetigung", "quellen_konsens", "smart_money", "cmf",
    "sektor_staerke", "minervini_5080", "fundamental",
]


def evaluiere(picks):
    """picks: Episoden MIT faktoren-Feld (aeltere werden vom Aufrufer schon
    rausgefiltert). Holt je Ticker EINMAL den aktuellen Kurs (Cache geteilt
    mit dem Scorer), rechnet dann pro Faktor die gruen/nicht-gruen-Kohorte."""
    import scorer
    cache = scorer.lade_cache()
    heute = datetime.now().date()

    kurs = {}
    eimer = {f: {"gruen": {h: [] for h, _ in sb.HORIZONTE},
                  "nicht_gruen": {h: [] for h, _ in sb.HORIZONTE}}
             for f in FAKTOR_NAMEN}
    gewertet = 0
    for p in picks:
        try:
            tage = (heute - datetime.strptime(p["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        bk = sb._bucket(tage)
        if not bk:
            continue
        sym = p["ticker"]
        if sym not in kurs:
            d = scorer.hole_chart_cached(sym, cache)
            kurs[sym] = (d["closes"][-1] if d and d.get("closes") else None)
        if not kurs[sym]:
            continue
        ret = kurs[sym] / p["preis"] - 1
        gewertet += 1
        for fname in FAKTOR_NAMEN:
            info = (p.get("faktoren") or {}).get(fname)
            if not info:
                continue
            kohorte = "gruen" if info.get("ampel") == "gruen" else "nicht_gruen"
            eimer[fname][kohorte][bk].append(ret)

    scorer.speichere_cache(cache)

    ergebnis = {}
    for fname in FAKTOR_NAMEN:
        ergebnis[fname] = {}
        for label, _ in sb.HORIZONTE:
            g = sb._stats(eimer[fname]["gruen"][label])
            n = sb._stats(eimer[fname]["nicht_gruen"][label])
            edge = None
            if g["n"] and n["n"] and g["win"] is not None and n["win"] is not None:
                edge = round(g["win"] - n["win"], 1)
            ergebnis[fname][label] = {"gruen": g, "nicht_gruen": n, "edge_win": edge}
    return ergebnis, gewertet


def main():
    cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
    schwellen = cfg["score_schwellen"]
    lb = sb.lade_logbuch()
    if not lb:
        print("Score-Logbuch leer/fehlt -> nichts auszuwerten (reift ueber Zeit).")
        return

    picks = sb.episoden(lb, schwellen)
    mit_faktoren = [p for p in picks if p.get("faktoren")]
    reif = [p for p in mit_faktoren
            if sb._bucket((datetime.now().date()
                          - datetime.strptime(p["datum"], "%Y-%m-%d").date()).days)]
    print(f"Logbuch: {len(lb)} Tage, {len(picks)} Episoden, davon {len(mit_faktoren)} mit "
          f"Faktoren-Snapshot, davon {len(reif)} reif (>=21 Tage).")
    if not reif:
        print("Noch keine Episode mit Faktoren alt genug -> keine Auswertung "
              "(braucht ca. 6 Wochen ab 2026-08-02, wenn das Faktor-Logging startete).")
        return

    ergebnis, gewertet = evaluiere(mit_faktoren)

    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "logbuch_tage": len(lb),
        "episoden_mit_faktoren": len(mit_faktoren),
        "gewertet": gewertet,
        "hinweis": ("Pro Faktor: Forward-Return-Vergleich Episoden mit gruener Ampel "
                    "vs. gelb/rot zum Zeitpunkt der Nennung. edge_win = Win%-Differenz "
                    "(gruen minus nicht-gruen) - deutlich positiv heisst, der Faktor hat "
                    "echten Vorhersagewert; nahe 0 oder negativ heisst, er traegt aktuell "
                    "nicht (oder nicht mehr) zum Score bei und ist Kandidat fuer eine "
                    "Gewicht-Reduktion in config.json::score_gewichte."),
        "ergebnis": ergebnis,
    }
    with open(pfade.SCORE_FAKTOREN_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"\n=== Pro-Faktor-Erfolgsanalyse ({gewertet} gewertete Episoden) ===")
    print(f"{'Faktor':22s}{'Hor':5s}{'nGr':>5s}{'WinGr':>7s}{'nNGr':>6s}{'WinNGr':>7s}{'ΔWin':>7s}")
    for fname in FAKTOR_NAMEN:
        for label, _ in sb.HORIZONTE:
            s = ergebnis[fname][label]
            g, n = s["gruen"], s["nicht_gruen"]
            if not g["n"] and not n["n"]:
                continue
            print(f"{fname:22s}{label:5s}{g['n']:5d}{(g['win'] or 0):7.1f}"
                  f"{n['n']:6d}{(n['win'] or 0):7.1f}"
                  f"{(s['edge_win'] if s['edge_win'] is not None else 0):+7.1f}")
    print(f"\nGespeichert: {pfade.SCORE_FAKTOREN_BACKTEST}")


if __name__ == "__main__":
    main()
