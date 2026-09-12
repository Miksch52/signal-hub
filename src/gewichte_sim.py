#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Was haetten ANDERE Score-Gewichte an Trennschaerfe geliefert? (seit 2026-09-12,
Systempruefung Punkt 1)

Ausgangsbefund: Die Gewichtung arbeitet messbar gegen die eigene Messung. Die
Korrelation zwischen Gewicht (config.json::score_gewichte) und gemessenem
Faktor-Edge (score_faktoren_backtest.json) betraegt -0,31 - stage2_trend hat
das hoechste Gewicht (20) und den schlechtesten Edge (-6,1), cmf den besten
Edge (+5,0) bei Gewicht 6. Passend dazu liegt Tier A in allen drei Horizonten
hinter Tier B, und dasselbe Muster zeigt sich unabhaengig bei Pivot
(ARMED q70+ schlechter als q<70) und Hebel-Ampel (gruen schlechter als gelb).

Dieses Skript beantwortet die Frage, bevor irgendetwas umgestellt wird: Waere
ein anderer Gewichtsvektor auf den bereits geloggten Episoden tatsaechlich
trennschaerfer gewesen - oder ist die Gewichtung gar nicht der Hebel?

MOEGLICH IST DAS NUR, WEIL DAS LOGBUCH DIE FAKTOR-ROHWERTE FUEHRT: scorer.py
speichert seit 2026-08-02 je Pick faktoren[name] = {wert, ampel, gewicht,
detail}. Da der Score eine reine Linearkombination ist
(scorer.py: score = sum(gew[k] * f[k]) / gew_summe * 100), laesst er sich mit
beliebigen Gewichten exakt nachrechnen - ohne einen einzigen Netzabruf.
Geprueft: Rekonstruktion trifft den geloggten Score auf +-0,07 Punkte
(Rundung der gespeicherten Werte, max 0,36).

ZWEI EHRLICHE EINSCHRAENKUNGEN, die jede Zahl hier begrenzen:

  1. institutional_trend und code33 (zusammen 32 der 126 Gewichtspunkte, also
     ein Viertel des Scores) sind in KEINER reifen Episode enthalten - sie
     wurden erst am 2026-09-06 zu Score-Faktoren, alle 671 reifen Episoden
     haben genau 11 Faktoren. Ihre Wirkung ist hier grundsaetzlich nicht
     messbar, nur die relative Gewichtung der uebrigen 11. Eine Variante, die
     diese beiden zurueckstuft, ist deshalb eine Vorsichtsmassnahme, kein
     gemessenes Ergebnis - und wird unten auch so ausgewiesen.
  2. Die Episoden stammen aus EINER Marktphase und ueberlappen sich zeitlich
     stark. Wer auf dieser Stichprobe das Optimum sucht, baut einen Kurvenfit.
     Deshalb wird jede Variante zusaetzlich out-of-sample geprueft (Zeit- und
     Markt-Schnitt), und die Kontrollvariante "gleichverteilt" laeuft immer
     mit: ist sie so gut wie die sortierten Varianten, traegt keiner der
     Faktoren und Umgewichten ist der falsche Hebel.

Aufruf:
  python3 src/gewichte_sim.py --sammeln   # teuer (Kurse je Episode), einmal
  python3 src/gewichte_sim.py             # rechnet Varianten gegen den Cache

--sammeln holt je Ticker den aktuellen Kurs (geteilter Tages-Cache mit dem
Scorer - direkt nach einem Lauf kostet das fast nichts) und legt Episoden samt
Forward-Return und Index-Vorsprung unter pfade.LOKAL ab. Danach ist jede
weitere Variantenrechnung eine Sache von Sekunden.

Reines Analyse-Werkzeug: schreibt NICHTS in die Pipeline, veraendert weder
config.json noch data/. Laeuft bewusst nicht in run.py mit.
"""

import argparse
import json
import os
from datetime import datetime, timezone

import index_vergleich
import pfade
import score_backtest as sb

CACHE = os.path.join(pfade.LOKAL, "gewichte_sim_episoden.json")

# Leitplanken (Nutzerentscheidung 2026-09-12): die Messung fuehrt, aber sie
# schreibt die Minervini-Methodik nicht um. Kein Faktor faellt auf 0, keiner
# steigt ueber das Doppelte seines heutigen Gewichts - eine einzelne
# Marktphase darf ein Kernkriterium daempfen, nicht abschaffen.
MIN_ANTEIL, MAX_ANTEIL = 0.25, 2.0

# Faktoren, die seit 2026-09-06 Score-Faktoren sind, aber in keiner reifen
# Episode stehen (siehe Docstring) - hier nicht simulierbar.
UNGEMESSEN = ("institutional_trend", "code33")


# ---------------------------------------------------------------------------
# Schritt 1: Episoden + Forward-Returns einsammeln (teuer, einmal)
# ---------------------------------------------------------------------------
def sammle():
    import scorer

    cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
    lb = sb.lade_logbuch()
    if not lb:
        print("Score-Logbuch leer/fehlt -> nichts zu sammeln.")
        return None

    picks = sb.episoden(lb, cfg["score_schwellen"])
    heute = datetime.now().date()
    cache = scorer.lade_cache()
    idx_charts = index_vergleich.lade_index_charts(scorer.hole_chart_cached, cache)

    kurs, raus = {}, []
    for p in picks:
        if not p.get("faktoren"):
            continue
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
        if not kurs[sym] or not p.get("preis"):
            continue
        ret = kurs[sym] / p["preis"] - 1
        raus.append({
            "ticker": sym, "datum": p["datum"], "markt": p.get("markt"),
            "horizont": bk, "tage": tage,
            "ret": round(ret, 6),
            "edge": (lambda e: round(e, 6) if e is not None else None)(
                index_vergleich.edge_fuer(idx_charts, p.get("markt"), p["datum"], tage, ret)),
            "score_geloggt": p.get("score"),
            # nur die Rohwerte - alles andere (Ampel, Gewicht, Detail) ist fuer
            # die Neuberechnung irrelevant und blaeht den Cache auf
            "werte": {k: v["wert"] for k, v in p["faktoren"].items()
                      if isinstance(v, dict) and v.get("wert") is not None},
        })
    scorer.speichere_cache(cache)

    out = {"erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "episoden": raus}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"{len(raus)} reife Episoden mit Faktoren gesammelt -> {CACHE}")
    return raus


def lade_episoden():
    if not os.path.exists(CACHE):
        return None
    try:
        return json.load(open(CACHE, encoding="utf-8"))["episoden"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Schritt 2: Score mit alternativen Gewichten
# ---------------------------------------------------------------------------
def score_mit(werte, gew):
    """Score einer Episode mit beliebigen Gewichten.

    Normiert bewusst ueber die im Pick TATSAECHLICH vorhandenen Faktoren: alte
    Episoden kennen institutional_trend/code33 nicht, wuerden mit der vollen
    Gewichtssumme im Nenner also systematisch zu niedrig ausfallen und die
    Schwelle 70 nie erreichen. So bleibt der Vergleich zwischen den Varianten
    fair; die Wirkung der fehlenden Faktoren selbst bleibt unmessbar."""
    aktiv = {k: g for k, g in gew.items() if k in werte and g}
    summe = sum(aktiv.values())
    if not summe:
        return None
    return sum(aktiv[k] * werte[k] for k in aktiv) / summe * 100


def _stats(rets):
    if not rets:
        return {"n": 0, "avg": None, "win": None}
    n = len(rets)
    return {"n": n,
            "avg": round(sum(rets) / n * 100, 2),
            "win": round(sum(1 for r in rets if r > 0) / n * 100, 1)}


def _t_wert(a_rets, b_rets):
    """Standardfehler und t-Wert der Trennschaerfe (Welch, ungleiche Varianzen).

    Ohne diese Zahl ist die ganze Tabelle wertlos: die Einzel-Episoden streuen
    mit rund 14 Prozentpunkten Standardabweichung, der Standardfehler der
    Differenz liegt damit bei 1-2 Pp. Ein Unterschied von "+1,05 Pp" ist dann
    KEIN Ergebnis, sondern Rauschen - und wer darauf umgewichtet, kalibriert
    auf Zufall. |t| > 1,96 entspricht dem ueblichen 5-%-Niveau."""
    if len(a_rets) < 2 or len(b_rets) < 2:
        return None, None
    ma, mb = sum(a_rets) / len(a_rets), sum(b_rets) / len(b_rets)
    va = sum((x - ma) ** 2 for x in a_rets) / (len(a_rets) - 1)
    vb = sum((x - mb) ** 2 for x in b_rets) / (len(b_rets) - 1)
    se = (va / len(a_rets) + vb / len(b_rets)) ** 0.5
    if not se:
        return None, None
    return round(se * 100, 2), round((ma - mb) / se, 2)


def bewerte(episoden, gew, schwelle):
    """-> {horizont: {A: stats, B: stats, trennschaerfe: Pp, se, t}} auf Basis
    des INDEX-BEREINIGTEN Returns (ohne ihn misst man die Marktphase, nicht den
    Score - siehe Systempruefung Punkt 5)."""
    eimer = {}
    for e in episoden:
        s = score_mit(e["werte"], gew)
        if s is None or e.get("edge") is None:
            continue
        tier = "A" if s >= schwelle else "B"
        eimer.setdefault(e["horizont"], {"A": [], "B": []})[tier].append(e["edge"])
    out = {}
    for h, tiers in eimer.items():
        a, b = _stats(tiers["A"]), _stats(tiers["B"])
        tr = (round(a["avg"] - b["avg"], 2)
              if a["n"] and b["n"] and a["avg"] is not None and b["avg"] is not None
              else None)
        se, t = _t_wert(tiers["A"], tiers["B"])
        out[h] = {"A": a, "B": b, "trennschaerfe": tr, "se": se, "t": t}
    return out


def schwellen_sweep(episoden, gew, schwellen=(55, 60, 65, 70, 75, 80, 85), horizont="4W"):
    """Nicht die Gewichte, sondern die SCHWELLE als Hebel: ab welchem Score
    trennt der Wert ueberhaupt? Eine hohe Schwelle macht Tier A kleiner und
    exklusiver - wenn der Score Information traegt, muss die Trennschaerfe mit
    der Schwelle steigen. Tut sie das nicht, liegt es nicht an der Grenze."""
    out = []
    for s in schwellen:
        r = (bewerte(episoden, gew, s) or {}).get(horizont) or {}
        out.append({"schwelle": s, "n_A": (r.get("A") or {}).get("n", 0),
                    "trennschaerfe": r.get("trennschaerfe"), "t": r.get("t")})
    return out


# ---------------------------------------------------------------------------
# Schritt 3: Kandidaten-Gewichtsvektoren
# ---------------------------------------------------------------------------
def _normiere(gew, ziel_summe):
    """Summe konstant halten - der Score ist zwar ohnehin normiert, aber so
    bleiben die Zahlen zwischen den Varianten direkt lesbar."""
    s = sum(gew.values()) or 1
    return {k: round(v * ziel_summe / s, 1) for k, v in gew.items()}


def _leitplanken(gew, basis, ziel_summe, runden=6):
    """Haelt jedes Gewicht zwischen MIN_ANTEIL und MAX_ANTEIL seines
    Ausgangswerts UND die Summe konstant.

    Beides zugleich geht nur iterativ: Wer gekappt wird, gibt Punkte ab, die
    auf die uebrigen verteilt werden - was diese wiederum ueber ihre Grenze
    heben kann. Verteilt deshalb proportional zum verbleibenden KOPFRAUM
    (nicht zum Gewicht) und wiederholt, bis nichts mehr umzuschichten ist.
    Ohne diesen Schritt hob die Umverteilung der freigewordenen Punkte aus
    UNGEMESSEN einzelne Faktoren auf das Vierfache statt aufs Doppelte -
    gefunden von tests/test_gewichte_sim.py."""
    g = dict(gew)
    for _ in range(runden):
        g = _normiere(g, ziel_summe)
        ueberschuss = 0.0
        for k, alt in basis.items():
            obergrenze, untergrenze = alt * MAX_ANTEIL, alt * MIN_ANTEIL
            if g[k] > obergrenze:
                ueberschuss += g[k] - obergrenze
                g[k] = obergrenze
            elif alt and g[k] < untergrenze:
                ueberschuss -= untergrenze - g[k]
                g[k] = untergrenze
        if abs(ueberschuss) < 0.05:
            break
        kopfraum = {k: max(0.0, basis[k] * MAX_ANTEIL - g[k]) for k in basis
                    if basis[k]}
        summe_kr = sum(kopfraum.values())
        if summe_kr <= 0:
            break
        anteil = min(1.0, ueberschuss / summe_kr) if ueberschuss > 0 else 0.0
        for k, kr in kopfraum.items():
            g[k] += kr * anteil
    return {k: round(v, 1) for k, v in g.items()}


def _edges(pfad=None):
    """edge_win je Faktor aus score_faktoren_backtest.json (4W - der einzige
    Horizont mit Daten). None fuer Faktoren ohne gereifte Kohorte."""
    pfad = pfad or pfade.SCORE_FAKTOREN_BACKTEST
    try:
        d = json.load(open(pfad, encoding="utf-8"))
    except Exception:
        return {}
    return {k: (v.get("4W") or {}).get("edge_win") for k, v in (d.get("ergebnis") or {}).items()}


def varianten(basis, edges):
    """Die Kandidaten. Jede behaelt die Gewichtssumme der Basis."""
    summe = sum(basis.values())
    v = {}

    v["aktuell"] = dict(basis)

    # Ungemessene zurueckstufen: was ein Viertel des Scores bestimmt, ohne je
    # gemessen worden zu sein, bekommt vorerst weniger Gewicht. Die frei
    # werdenden Punkte gehen proportional an die belegt positiven Faktoren.
    g = dict(basis)
    frei = 0
    for k in UNGEMESSEN:
        if g.get(k, 0) > 6:
            frei += g[k] - 6
            g[k] = 6
    positiv = {k: g[k] for k, e in edges.items() if e is not None and e > 0 and g.get(k)}
    if positiv and frei:
        p_summe = sum(positiv.values())
        for k in positiv:
            g[k] += frei * positiv[k] / p_summe
    v["ungemessen_zurueck"] = _leitplanken(g, basis, summe)

    # Edge-gefuehrt: Gewicht * (1 + edge/10), gekappt auf die Leitplanken.
    # +5 Pp Edge -> x1,5; -6 Pp -> x0,4 (auf 0,25 gekappt); ungemessen -> x1.
    g = {}
    for k, a in basis.items():
        e = edges.get(k)
        f = 1.0 if e is None else max(MIN_ANTEIL, min(MAX_ANTEIL, 1 + e / 10))
        g[k] = a * f
    v["edge_gefuehrt"] = _leitplanken(g, basis, summe)

    # Beides zusammen - die Variante, die inhaltlich am ehesten taugt
    g = dict(v["edge_gefuehrt"])
    frei = 0
    for k in UNGEMESSEN:
        if g.get(k, 0) > 6:
            frei += g[k] - 6
            g[k] = 6
    positiv = {k: g[k] for k, e in edges.items() if e is not None and e > 0 and g.get(k)}
    if positiv and frei:
        p_summe = sum(positiv.values())
        for k in positiv:
            g[k] += frei * positiv[k] / p_summe
    v["edge_und_ungemessen"] = _leitplanken(g, basis, summe)

    # KONTROLLE: alle gleich. Ist diese Variante so gut wie die sortierten,
    # traegt keiner der Faktoren und die Gewichte sind nicht der Hebel.
    v["gleichverteilt"] = _normiere({k: 1.0 for k in basis}, summe)

    # Extrempunkt: nur belegt positive Faktoren (bewusst ohne Leitplanken -
    # zeigt die Obergrenze dessen, was mit dieser Stichprobe erreichbar waere,
    # und ist genau deshalb der Kurvenfit-Kandidat).
    g = {k: (basis[k] if edges.get(k) is not None and edges[k] > 0 else 0.0) for k in basis}
    if sum(g.values()):
        v["nur_belegt_positiv"] = _normiere(g, summe)
    return v


# ---------------------------------------------------------------------------
# Schritt 4: Out-of-Sample-Schnitte
# ---------------------------------------------------------------------------
def schnitte(episoden):
    """-> {name: (kalibrier-Menge, pruef-Menge)}.

    Zeit-Schnitt: chronologisch 60/40. Markt-Schnitt: USA gegen Europa - bei
    stark ueberlappenden Kohorten der unabhaengigere der beiden, weil die
    Episoden zweier Maerkte nicht dieselben Kursbewegungen teilen."""
    nach_zeit = sorted(episoden, key=lambda e: e["datum"])
    grenze = int(len(nach_zeit) * 0.6)
    usa = [e for e in episoden if (e.get("markt") or "USA") == "USA"]
    eu = [e for e in episoden if (e.get("markt") or "USA") != "USA"]
    return {
        "Zeit (60/40)": (nach_zeit[:grenze], nach_zeit[grenze:]),
        "Markt (USA/EU)": (usa, eu),
    }


# ---------------------------------------------------------------------------
def _zeile(name, res, horizont):
    h = res.get(horizont) or {}
    a, b, tr = h.get("A") or {}, h.get("B") or {}, h.get("trennschaerfe")
    if not a.get("n") or not b.get("n"):
        return f"  {name:22}{'—  (zu wenig Daten)':>40}"
    t = h.get("t")
    urteil = "SIGNIFIKANT" if t is not None and abs(t) > 1.96 else "Rauschen"
    return (f"  {name:22}A: n={a['n']:4} {a['avg']:+7.2f} | "
            f"B: n={b['n']:4} {b['avg']:+7.2f} | "
            f"Delta {tr:+6.2f} +-{h.get('se') or 0:4.2f} Pp | "
            f"t={t if t is not None else 0:+5.2f}  {urteil}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sammeln", action="store_true",
                    help="Episoden + Kurse neu einsammeln (teuer, braucht Netz/Cache)")
    ap.add_argument("--horizont", default="4W", help="4W (Default), 8W oder 12W")
    args = ap.parse_args()

    episoden = sammle() if args.sammeln else lade_episoden()
    if episoden is None:
        print("Kein Episoden-Cache vorhanden -> erst 'python3 src/gewichte_sim.py --sammeln'.")
        return
    mit_edge = [e for e in episoden if e.get("edge") is not None]

    cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
    basis, schwelle = cfg["score_gewichte"], cfg["score_schwellen"]["kauf_kandidat"]
    edges = _edges()
    kand = varianten(basis, edges)

    fehlend = sorted({k for k in basis if not any(k in e["werte"] for e in episoden)})
    print(f"\n{len(episoden)} reife Episoden ({len(mit_edge)} mit Index-Bezug), "
          f"Schwelle Tier A: {schwelle}, Horizont: {args.horizont}")
    if fehlend:
        print(f"NICHT simulierbar (in keiner reifen Episode enthalten): {', '.join(fehlend)}"
              f" — zusammen {sum(basis[k] for k in fehlend)} von {sum(basis.values())} Gewichtspunkten.")

    print("\n=== Gewichtsvektoren ===")
    namen = sorted(basis, key=lambda k: -basis[k])
    kopf = "Faktor".ljust(22) + "".join(f"{n[:11]:>12}" for n in kand)
    print(kopf)
    for k in namen:
        e = edges.get(k)
        e_txt = f"{e:+.1f}" if e is not None else "—"
        print(f"{k:16}{e_txt:>6}" + "".join(f"{kand[n].get(k, 0):12.1f}" for n in kand))

    print(f"\n=== Trennschaerfe auf ALLEN reifen Episoden ({args.horizont}) ===")
    print("  (Tier A minus Tier B, index-bereinigt. Positiv = A laeuft besser als B.)")
    for n, g in kand.items():
        print(_zeile(n, bewerte(mit_edge, g, schwelle), args.horizont))

    print("\n=== Out-of-Sample ===")
    for s_name, (kal, pruef) in schnitte(mit_edge).items():
        print(f"\n  --- {s_name}: kalibriert auf {len(kal)}, geprueft auf {len(pruef)} Episoden")
        for n, g in kand.items():
            r_k = (bewerte(kal, g, schwelle).get(args.horizont) or {}).get("trennschaerfe")
            r_p = (bewerte(pruef, g, schwelle).get(args.horizont) or {}).get("trennschaerfe")
            f_k = f"{r_k:+6.2f}" if r_k is not None else "     —"
            f_p = f"{r_p:+6.2f}" if r_p is not None else "     —"
            robust = "  <- in beiden vorn" if (r_k is not None and r_p is not None
                                               and r_k > 0 and r_p > 0) else ""
            print(f"    {n:22} kalibriert {f_k} Pp | geprueft {f_p} Pp{robust}")

    print(f"\n=== Die SCHWELLE als Hebel (heutige Gewichte, {args.horizont}) ===")
    print("  Traegt der Score Information, muss die Trennschaerfe mit der Schwelle steigen.")
    print(f"  {'Schwelle':>9}{'n Tier A':>10}{'Trennschaerfe':>15}{'t':>8}")
    for r in schwellen_sweep(mit_edge, basis, horizont=args.horizont):
        tr = f"{r['trennschaerfe']:+.2f} Pp" if r["trennschaerfe"] is not None else "—"
        t = f"{r['t']:+.2f}" if r["t"] is not None else "—"
        marke = "  <-- heutige Einstellung" if r["schwelle"] == schwelle else ""
        print(f"  {r['schwelle']:>9}{r['n_A']:>10}{tr:>15}{t:>8}{marke}")

    print("\nHinweis: Eine Variante gilt nur als robust, wenn sie in BEIDEN Schnitten "
          "vorn liegt.\nLiegt 'gleichverteilt' gleichauf, sind die Gewichte nicht der Hebel.")
    print("WICHTIGER als jede Rangfolge ist die Spalte t: bei |t| < 1,96 ist der "
          "Unterschied\nRauschen, egal wie gross er aussieht - dann traegt die "
          "Stichprobe keine Umstellung.")


if __name__ == "__main__":
    main()
