#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pivot-Armed-Detektor fuer den Daily Signal Hub.

Reine Detektionslogik (keine I/O). Setzt NICHT auf den nachlaufenden
Stage-2-Score auf, sondern beleuchtet die ENTSTEHUNG des Ausbruchs:
die finale VCP-Verengung mit Volumen-Austrocknung kurz unter dem Pivot.

Hintergrund: `scorer.f_stage2` & die Finviz-Filter (highlow52w_a70h/nh)
bestaetigen Trends, die bereits ausgebrochen SIND. Dieser Detektor sucht
das Gegenteil — Werte, die noch zusammenrollen und kurz davor stehen.

Eingabe je Ticker: der 126-Tage-Chart aus signals.json
  chart = {"c": [...], "v": [...], "s50": [...], "s150": [...], "s200": [...]}
  (c = Schlusskurse, v = Volumen in Tausend, s* = gleitende Durchschnitte)

Ausgabe: dict mit status / armed_score / Kennzahlen / Detailtext.

Zustaende:
  BREAKOUT  Kurs raeumt heute frisch den Pivot auf Volumenschub  -> der Move startet
  ARMED     enge Endkontraktion + Dry-up + dicht unter Pivot     -> scharf, Pre-Breakout
  CHEAT     frueher Ausbruch VOR dem vollen Stage-2-Template     -> klein positionieren
  WATCH     Aufwaertstrend, Basis bildet sich, noch nicht scharf
  -         kein Setup (Abwaertstrend / zu weit / keine Basis)

Zusatzfelder:
  megaphone      True, wenn die rechte Seite ueber mehrere Wellen eher expandiert
                 als sich beruhigt (V-foermig) statt sauber zuzuziehen.
                 Informational - der Retro-Backtest (2026-07-24) fand KEINE
                 Trennung zu sauberen Basen, deshalb bewusst kein Gate.
  supply_score   0..1, Anteil des juengsten Handelsvolumens UNTER dem Pivot
                 (1.0 = kaum Angebot oberhalb = guenstig, 0.0 = viel Angebot).
                 Seit 2026-07-24 Score-Bestandteil (20% im armed_score) UND
                 ARMED-Gate (SUPPLY_MIN) - Backtest-Begruendung an den
                 jeweiligen Konstanten.

Test:  python3 -c "import pivot, json; \
        d=json.load(open('../data/signals.json')); \
        print(pivot.klassifiziere(d['treffer'][0]['chart']))"
"""

# --- Parameter (bewusst zentral, leicht justierbar) -------------------------
BASIS_FENSTER   = 25     # Handelstage, in denen Pivot/Basis gesucht wird (~5 Wochen)
ENG_FENSTER     = 15     # finale Verengung wird hierauf gemessen (~3 Wochen)
DRYUP_FENSTER   = 10     # juengstes Volumen-Fenster fuer Dry-up
VOL_BASIS       = 50     # Referenz-Volumenschnitt (50T)

ENG_MAX         = 0.12   # Verengung "eng" ab <=12 % Range (1.0 bei <=4 %)
ENG_MIN         = 0.04
DRYUP_MAX       = 1.00   # Dry-up zaehlt ab Vol < 100 % (1.0 bei <=60 %)
DRYUP_MIN       = 0.60
NAH_PIVOT       = 0.08   # ARMED nur wenn Kurs <=8 % unter Pivot
WATCH_PIVOT     = 0.15   # WATCH bis 15 % unter Pivot
BREAKOUT_VOL    = 1.40   # Volumenschub am Ausbruchstag (>=1.4x 50T)
BREAKOUT_DEHN   = 0.04   # nur FRISCHE Ausbrueche (Kurs <=4 % ueber Pivot).
                         # 2026-07-24 von 8% gesenkt: Retro-Backtest zeigte
                         # >4% ueber Pivot deutlich schlechter (4W Win 57% vs
                         # 63%, Delta-R -0.89 vs -0.55), Forward-Log Win 26% -
                         # die alten BREAKOUTs wurden zu spaet gekauft.
BREAKOUT_LOOKBACK = 3    # Durchbruch muss in den letzten N Tagen passiert sein
BASIS_RANGE_MAX = 0.20   # die Basis vor dem Ausbruch darf max. so weit sein

SUPPLY_MIN      = 0.70   # ARMED-Gate: unter dieser Schwelle liegt zu viel
                         # juengstes Handelsvolumen UEBER dem Pivot (Overhead-
                         # Angebot). 2026-07-24 vom Info-Feld zum Gate erhoben:
                         # Retro-Backtest trennte scharf (ARMED mit Supply>=0.7:
                         # 4W Win 67%, Delta-R bis +0.50; darunter: Win 48%,
                         # Delta-R ~-0.5 ueber alle Horizonte) -> solche Tage
                         # werden zu WATCH degradiert statt scharf gemeldet.

CHEAT_ENG_FENSTER = 8    # kurzes Basis-Fenster fuer Cheat-Pivots (~1.5 Wochen)
CHEAT_BAND      = 0.10   # Kurs darf max. 10% ueber/unter der 50-Tage-Linie liegen
CHEAT_VOL       = 1.30   # niedrigere Volumenschwelle als BREAKOUT_VOL (frueh, kleinere Bewegung)

MEGAPHONE_TOL   = 1.15   # 15% Toleranz: spaetere Welle darf so viel breiter sein,
                         # ohne schon als "Megaphone" (expandierend) zu gelten

SUPPLY_FENSTER  = 100    # deutlich laenger als BASIS_FENSTER: der Pivot IST per
                         # Definition das Hoch von BASIS_FENSTER, ein gleich
                         # langes Supply-Fenster faende darueber fast nie Volumen
                         # (trivial ~1.0). Erst der Blick weiter zurueck (vor der
                         # aktuellen Basis) macht echtes Overhead-Angebot sichtbar.


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _stop_info(price, stop_preis):
    """Stop knapp unter dem letzten Basis-Tief (Minervini: enger, definierter
    Stop statt fixem Kursziel - es gibt bewusst kein Reward/Ziel dazu, die
    Methode traden ohne festes Kursziel, sondern trailen/verkaufen in Staerke)."""
    if not stop_preis or stop_preis <= 0 or not price:
        return {}
    pct = (price - stop_preis) / price * 100
    return {"stop": round(stop_preis, 2), "stop_pct": round(pct, 1)}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def _range(xs):
    """relative Spanne (max-min)/min eines Fensters; 0 wenn leer/0."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return 0.0
    lo = min(xs)
    return (max(xs) - lo) / lo if lo else 0.0


def _uptrend_gate(price, s50, s150, s200):
    """Stage-2-Kontext: nur Pullbacks/Basen IM Aufwaertstrend, kein fallendes
    Messer. Fehlende SMA (zu wenig Historie) = Gate nicht erfuellt."""
    if None in (s50, s150, s200):
        return False
    return price > s150 and price > s200 and s50 > s200


def _megaphone(c, fenster=BASIS_FENSTER, tol=MEGAPHONE_TOL):
    """Prueft, ob sich die rechte Seite ueber mehrere Wellen tatsaechlich
    beruhigt (mehrwellige Verengung) statt nur im letzten Fenster eng zu
    wirken. Teilt die letzten `fenster` Tage in 3 aufeinanderfolgende
    Drittel und verlangt, dass die Range je Drittel nicht wieder zunimmt
    (kleine Toleranz gegen Sub-Fenster-Rauschen). Erkennt V-foermige/
    expandierende rechte Seiten (Megaphone), die trotz enger letzter Kerze
    noch nicht "in Ruhe" gegangen sind. Rein informational, kein Gating."""
    teil = c[-fenster:]
    n = len(teil) // 3
    if n < 2:
        return False
    w1, w2, w3 = teil[:n], teil[n:2 * n], teil[2 * n:3 * n]
    r1, r2, r3 = _range(w1), _range(w2), _range(w3)
    return r2 > r1 * tol or r3 > r2 * tol


def _supply_score(c, v, pivot, fenster=SUPPLY_FENSTER):
    """Grobe Naeherung fuer Overhead-Angebot: Anteil des Handelsvolumens der
    letzten `fenster` Tage, der bei einem Schlusskurs UEBER dem Pivot
    stattfand (Minervini: Linie durch die Basis, wo lag der Grossteil der
    Kursaktivitaet). Viel Volumen oberhalb des Pivots bedeutet mehr
    Angebot, das ein Ausbruch erst "verdauen" muss. Bewusst ein deutlich
    laengeres Fenster als BASIS_FENSTER (siehe SUPPLY_FENSTER) - sonst
    waere der Pivot selbst (das Hoch des Basis-Fensters) trivial fast nie
    ueberschritten. Rueckgabe 0..1, 1.0 = kaum Angebot oberhalb (guenstig),
    0.0 = fast alles Angebot oben. None, wenn keine belastbare Berechnung
    moeglich ist."""
    if not pivot:
        return None
    cs, vs = c[-fenster:], v[-fenster:]
    vol_gesamt = sum(vol for vol in vs if vol)
    if vol_gesamt <= 0:
        return None
    vol_ueber = sum(vol for close, vol in zip(cs, vs) if close and vol and close > pivot)
    return round(1.0 - (vol_ueber / vol_gesamt), 2)


def _cheat_pivot(c, v, price, s50):
    """Frueher Ausbruch VOR dem vollen Stage-2-Trend-Template (Minervini:
    "cheat entry"). Wird nur versucht, wenn der normale Uptrend-Gate NICHT
    greift (sonst haette ARMED/BREAKOUT/WATCH schon gefeuert) - kein
    Ersatz, sondern ein separates, bewusst kleineres Signal fuer eine frueh
    beginnende Bewegung, typischerweise noch nahe/unter der 50-Tage-Linie.
    Minervini: Position klein halten, bis sich eine saubere rechte Seite
    entwickelt. Eigene, einfache Qualitaetsberechnung aus Enge + Volumen-
    Schub - der Standard-Pivot/BASIS_FENSTER-Kontext gilt hier nicht."""
    if not s50 or s50 <= 0:
        return None
    if abs(price - s50) / s50 > CHEAT_BAND:
        return None
    basis = c[-CHEAT_ENG_FENSTER - 1:-1]
    if len(basis) < CHEAT_ENG_FENSTER:
        return None
    cheat_pivot_preis = max(basis)
    if price <= cheat_pivot_preis:
        return None
    vol_basis = _mean(v[-VOL_BASIS:])
    vol_surge = (v[-1] / vol_basis) if vol_basis else 0.0
    if vol_surge < CHEAT_VOL:
        return None
    eng = _range(basis)
    s_eng = clamp((0.15 - eng) / (0.15 - 0.03))
    s_vol = clamp((vol_surge - CHEAT_VOL) / (3.0 - CHEAT_VOL))
    qual = round(100 * (0.6 * s_eng + 0.4 * s_vol), 1)
    return {
        "status": "CHEAT", "qualitaet": qual, "armed_score": qual,
        "pivot": round(cheat_pivot_preis, 2),
        "dist_pct": round((price / cheat_pivot_preis - 1) * 100, 1),
        "eng_pct": round(eng * 100, 1), "vol_surge": round(vol_surge, 2),
        "detail": (f"Fruehe Cheat-Pivot ueber {cheat_pivot_preis:.2f} auf {vol_surge:.1f}x Volumen "
                   f"(noch kein volles Stage-2-Template, klein positionieren)"),
    }


def klassifiziere(chart, preis=None, cheat_aktiv=True):
    """Bewertet einen Chart-Block. Liefert immer ein dict (status '-' wenn kein Setup)."""
    c = [x for x in (chart.get("c") or []) if x is not None]
    v = chart.get("v") or []
    if len(c) < 60 or len(v) < VOL_BASIS:
        return {"status": "-", "grund": "zu wenig Historie"}

    price = preis if preis else c[-1]
    s50 = (chart.get("s50") or [None])[-1]
    s150 = (chart.get("s150") or [None])[-1]
    s200 = (chart.get("s200") or [None])[-1]

    if not _uptrend_gate(price, s50, s150, s200):
        if cheat_aktiv:
            cheat = _cheat_pivot(c, v, price, s50)
            if cheat:
                return cheat
        return {"status": "-", "grund": "kein Aufwaertstrend (Stage 2)"}

    # --- Pivot / Basis ------------------------------------------------------
    # Overhead-Pivot: hoechster Schluss des Basisfensters (ohne heute) -> der
    # Widerstand, unter dem ARMED/WATCH-Werte noch zusammenrollen.
    basis = c[-BASIS_FENSTER:]
    pivot = max(basis[:-1]) if len(basis) > 1 else max(basis)
    dist = (pivot - price) / pivot if pivot else 1.0            # >0 = unter Pivot

    # Breakout-Pivot: Widerstand der Basis VOR den letzten Tagen. So zaehlt nur
    # ein FRISCHER Durchbruch (Kreuzung in den letzten BREAKOUT_LOOKBACK Tagen),
    # nicht jeder laengst ausgebrochene, ausgedehnte Trend.
    basis_brk = c[-BASIS_FENSTER:-BREAKOUT_LOOKBACK] or basis[:-1]
    pivot_brk = max(basis_brk)
    basis_brk_range = _range(basis_brk)
    vor_durchbruch = c[-1 - BREAKOUT_LOOKBACK] if len(c) > BREAKOUT_LOOKBACK else c[0]
    frisch_gekreuzt = price > pivot_brk and vor_durchbruch <= pivot_brk

    eng = _range(c[-ENG_FENSTER:])                              # finale Verengung
    davor = _range(c[-(ENG_FENSTER + 20):-ENG_FENSTER])        # Basis davor
    contraction = davor / eng if eng else 1.0                  # >1 = es zieht sich zu

    vol_basis = _mean(v[-VOL_BASIS:])
    dryup = _mean(v[-DRYUP_FENSTER:]) / vol_basis if vol_basis else 1.0
    vol_surge = (v[-1] / vol_basis) if vol_basis else 1.0

    # Megaphone bleibt informational (Retro-Backtest 2026-07-24: keine
    # Trennung megaphone vs. sauber, teils sogar leicht besser - kein Gate).
    # Supply ist seit 2026-07-24 Score-Bestandteil + ARMED-Gate (SUPPLY_MIN).
    megaphone = _megaphone(c)
    supply_score = _supply_score(c, v, pivot)

    # --- Teil-Scores 0..1 ---------------------------------------------------
    s_eng = clamp((ENG_MAX - eng) / (ENG_MAX - ENG_MIN))
    s_dry = clamp((DRYUP_MAX - dryup) / (DRYUP_MAX - DRYUP_MIN))
    s_prox = clamp((NAH_PIVOT - dist) / NAH_PIVOT) if dist >= 0 else 0.0
    s_contr = clamp((contraction - 1.0) / (2.0 - 1.0))
    # Supply seit 2026-07-24 Score-Bestandteil (Backtest: trennt die ARMED-
    # Spreu, siehe SUPPLY_MIN). Fehlt er, werden die Gewichte renormalisiert.
    teile = [(0.30, s_eng), (0.25, s_dry), (0.15, s_prox), (0.10, s_contr)]
    if supply_score is not None:
        teile.append((0.20, supply_score))
    armed_score = round(100 * sum(w * s for w, s in teile) / sum(w for w, _ in teile), 1)

    kennz = {
        "pivot": round(pivot, 2),
        "dist_pct": round(dist * 100, 1),       # % unter Pivot (neg = darueber)
        "eng_pct": round(eng * 100, 1),         # Range der letzten 3 Wochen
        "contraction": round(contraction, 2),   # Verengung ggue. Basis davor
        "dryup": round(dryup, 2),               # Vol 10T / 50T  (<1 = Austrocknung)
        "vol_surge": round(vol_surge, 2),       # Vol heute / 50T
        "megaphone": megaphone,                 # informational, siehe Docstring
        "supply_score": supply_score,           # informational, siehe Docstring
    }

    # --- Zustands-Logik (Reihenfolge = Prioritaet) --------------------------
    # BREAKOUT: frische Kreuzung des Basis-Pivots (letzte Tage) aus einer ECHTEN,
    # engen Basis, mit Volumenschub und noch nicht ueberdehnt.
    if (frisch_gekreuzt
            and vol_surge >= BREAKOUT_VOL
            and basis_brk_range <= BASIS_RANGE_MAX
            and price <= pivot_brk * (1 + BREAKOUT_DEHN)):
        # Ausbruchsqualitaet: enge Basis + kraeftiger Volumenschub (NICHT dry-up,
        # das ist am Ausbruchstag per Definition vorbei) + seit 2026-07-24
        # Naehe zum Pivot (Backtest: je weiter ueber dem Pivot gekauft, desto
        # schlechter das R - naeher = besser, siehe BREAKOUT_DEHN).
        s_basis = clamp((BASIS_RANGE_MAX - basis_brk_range) / (BASIS_RANGE_MAX - 0.05))
        s_surge = clamp((vol_surge - BREAKOUT_VOL) / (3.0 - BREAKOUT_VOL))
        s_naehe = clamp((BREAKOUT_DEHN - max(0.0, price / pivot_brk - 1)) / BREAKOUT_DEHN)
        qual = round(100 * (0.5 * s_basis + 0.3 * s_surge + 0.2 * s_naehe), 1)
        stop = _stop_info(price, min(basis_brk) if basis_brk else None)
        stop_txt = f", Stop {stop['stop']:.2f} (-{stop['stop_pct']:.1f}%)" if stop else ""
        return {"status": "BREAKOUT", "qualitaet": qual, "armed_score": armed_score,
                "pivot": round(pivot_brk, 2), "dist_pct": round((price / pivot_brk - 1) * 100, 1),
                "eng_pct": kennz["eng_pct"], "contraction": kennz["contraction"],
                "dryup": kennz["dryup"], "vol_surge": kennz["vol_surge"],
                "megaphone": megaphone, "supply_score": _supply_score(c, v, pivot_brk), **stop,
                "detail": (f"Frischer Ausbruch ueber {pivot_brk:.2f} auf {vol_surge:.1f}x Volumen "
                           f"(Basis {basis_brk_range*100:.0f}% Range){stop_txt}")}

    # ARMED: eng + ausgetrocknet + dicht unter Pivot = der Pre-Breakout-Punkt
    if (0 <= dist <= NAH_PIVOT and eng <= ENG_MAX and dryup <= DRYUP_MAX
            and s_eng > 0 and s_dry > 0):
        # Supply-Gate (siehe SUPPLY_MIN): zu viel Angebot ueber dem Pivot ->
        # kein scharfes Signal, nur WATCH mit klarem Grund.
        if supply_score is not None and supply_score < SUPPLY_MIN:
            return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                    "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, eng+trocken, aber "
                               f"Supply-Score {supply_score:.2f} < {SUPPLY_MIN:.2f} "
                               f"(zu viel Angebot ueber dem Pivot - Backtest: Win 48% statt 67%)")}
        stop = _stop_info(price, min(c[-ENG_FENSTER:]))
        stop_txt = f", Stop {stop['stop']:.2f} (-{stop['stop_pct']:.1f}%)" if stop else ""
        return {"status": "ARMED", "qualitaet": armed_score, "armed_score": armed_score, **kennz, **stop,
                "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, Range {eng*100:.0f}%, "
                           f"Vol-Dry-up {dryup:.2f}{stop_txt}")}

    # WATCH: Trend da, Basis bildet sich, noch nicht scharf
    if 0 <= dist <= WATCH_PIVOT:
        return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, Basis Range {eng*100:.0f}% "
                           f"(noch nicht eng/trocken genug)")}

    return {"status": "-", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
            "grund": f"{dist*100:.0f}% unter Pivot (zu weit)"}
