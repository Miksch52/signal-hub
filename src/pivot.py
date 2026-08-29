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


# --- VCP-Feinmessung (Minervini/SEPA-Abgleich 2026-08-29) ------------------
# Drei Kennzahlen, die der Detektor bis dahin NICHT gemessen hat, obwohl
# Minervini sie ausdruecklich verlangt:
#   1. Anzahl der Kontraktionen (er nennt 3-4; Markets 360 misst das laengst
#      ueber vcp.py, der Signal-Hub bislang gar nicht - hier nachgezogen).
#   2. Dauer der Basis (Faustregel >= 7 Wochen; die Hauptapp prueft das schon
#      als Checkbox, der automatische Detektor bisher nicht).
#   3. Follow-Through-Volumen NACH dem Ausbruch (er verlangt 2-5 Tage mit
#      deutlich ueberdurchschnittlichem Volumen; bislang wurde nur der
#      Ausbruchstag selbst geprueft).
#
# ALLE DREI SIND ZUNAECHST REIN MESSEND (siehe GATES): sie erscheinen in der
# Ausgabe und im Forward-Logbuch, aendern aber KEINE Status-Entscheidung.
# Grund ist die Backtest-Pflicht aus CLAUDE.md - ein Gate ohne unverzerrte
# Forward-Kohorte waere eine unbelegte Verschaerfung, und die bestehenden
# Schwellen (ENG_MAX/SUPPLY_MIN) sind ihrerseits durch Backtests begruendet.
# Sobald pivot_backtest.py --evaluate genug gereifte Picks hat, laesst sich
# jedes Gate durch Umlegen EINES Schalters scharf schalten.
# Kontraktionen werden INNERHALB der erkannten Basis gezaehlt, nicht ueber ein
# festes 6-Monats-Fenster wie bei Markets360 vcp.py. Grund (empirisch am
# 2026-08-29 ueber 400 echte Charts aus signals.json gemessen): mit festem
# 130-Tage-Fenster und 1%-Tiefenschwelle landeten 397 von 400 Werten am
# Maximum von 6 - die Kennzahl haette null Trennschaerfe gehabt. Minervini
# zaehlt die Kontraktionen einer BASIS, nicht jede Welle eines halben Jahres.
# Mit Basisfenster + 3%-Schwelle ist die Verteilung brauchbar gestreut
# (0..6: 44/68/52/66/54/41/40), Modus bei 1-3.
KONTRAKTION_ORDER   = 3     # +/- Bars fuer ein lokales Extrem (auf Schlusskursen)
KONTRAKTION_MIN     = 2     # wie Markets360 vcp.min_contractions
KONTRAKTION_MAX     = 6     # wie Markets360 vcp.max_contractions
KONTRAKTION_MIN_TIEFE = 0.03  # 3%: tief genug gegen Rauschen, flach genug, um
                              # Minervinis finale 3-5%-Kontraktion noch zu sehen
KONTRAKTION_MIN_BASIS_TAGE = 15  # darunter ist keine sinnvolle Zaehlung moeglich
KONTRAKTION_TOLERANZ  = 1.05  # spaetere Kontraktion darf 5% tiefer sein und gilt noch als "enger"

KONSOLIDIERUNG_CAP       = 0.30  # 1:1 aus "Maick's Trading System.html" (_sepaPattern)
KONSOLIDIERUNG_MAX_TAGE  = 90    # dito - damit App und Detektor dieselbe Wochenzahl nennen
BASIS_MIN_WOCHEN         = 7     # Minervini-Faustregel, dito zur Checkbox sc3_2

FT_VOL_FENSTER = 5     # Minervini: 2-5 Tage Follow-Through nach dem Ausbruch
FT_VOL_MIN     = 1.30  # Ø-Volumen der Folgetage / Ø50 - darunter fehlt die Bestaetigung

# Schalter fuer die drei neuen Kriterien. Default AUS = reine Messung.
# Erst nach belegter Forward-Kohorte einzeln auf True setzen.
GATES = {
    "kontraktionen": False,   # ARMED nur mit >= KONTRAKTION_MIN Kontraktionen
    "basis_wochen": False,    # ARMED/BREAKOUT nur ab BASIS_MIN_WOCHEN
    "follow_through": False,  # BREAKOUT nur mit bestaetigtem Folgevolumen
}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


STOP_WARN_PCT = 8.0   # Minervini: Stop moeglichst eng (5-8%). Darueber nur ein
                       # Warnhinweis (informativ, kein Gate/Score-Abzug).


def _stop_info(price, pivot, stop_preis):
    """Stop knapp unter dem letzten Basis-Tief (Minervini: enger, definierter
    Stop statt fixem Kursziel - die Methode tradet ohne festes Kursziel,
    sondern trailt/verkauft in Staerke). Ziel & Chance/Risiko sind deshalb
    bewusst rein INFORMATIV: eine klassische Measured-Move-Projektion
    (Basis-Hoehe ab Pivot nochmal aufgesetzt), keine Kauf-/Verkaufsregel und
    ohne Einfluss auf Score/Status. stop_warnung markiert nur einen fuer ein
    enges VCP-Setup untypisch weiten Stop."""
    if not stop_preis or stop_preis <= 0 or not price:
        return {}
    pct = (price - stop_preis) / price * 100
    info = {"stop": round(stop_preis, 2), "stop_pct": round(pct, 1),
            "stop_warnung": pct > STOP_WARN_PCT}
    risiko = price - stop_preis
    if pivot and pivot > stop_preis and risiko > 0:
        ziel = pivot + (pivot - stop_preis)
        info["ziel"] = round(ziel, 2)
        info["chance_risiko"] = round((ziel - price) / risiko, 1)
    return info


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


def _swing_extrema(werte, order=KONTRAKTION_ORDER):
    """Indizes lokaler Hochs/Tiefs (Extrem im +/-order-Fenster).

    Arbeitet auf SCHLUSSKURSEN, nicht auf High/Low wie Markets360 vcp.py -
    der Chart-Block in signals.json enthaelt bewusst nur c/v/s50/s150/s200
    (Groesse: signals.js liegt schon bei ~12 MB). Die Kontraktionstiefen
    fallen dadurch systematisch etwas flacher aus als bei einer High/Low-
    Messung; fuer den VERGLEICH der Kontraktionen untereinander (wird es
    enger?) ist das unerheblich, fuer absolute Tiefenangaben nicht - deshalb
    werden die Tiefen als Naeherung ausgewiesen, nicht als exakte Werte."""
    hochs, tiefs = [], []
    n = len(werte)
    for i in range(order, n - order):
        fenster = werte[i - order:i + order + 1]
        if werte[i] == max(fenster) and werte[i] > werte[i - 1]:
            hochs.append(i)
        if werte[i] == min(fenster) and werte[i] < werte[i - 1]:
            tiefs.append(i)
    return hochs, tiefs


def _kontraktionen(c, basis_tage):
    """Chronologische Kontraktionen (Hoch -> folgendes Tief) INNERHALB der
    aktuellen Basis.

    Portierung von MinerviniMarkets360 scoring/vcp.py::_contractions auf die
    hier verfuegbaren Schlusskurse, mit zwei bewussten Abweichungen:
      * Fenster = die tatsaechlich erkannte Basis (siehe _basis_tage), nicht
        fixe 130 Tage - sonst saettigt die Zaehlung (siehe Konstanten-Kommentar).
      * Tiefenschwelle 3% statt 1% - auf Schlusskursen ohne High/Low erzeugt
        1% fast nur Rauschen.
    Rueckgabe: Liste von Tiefen (0..1), juengste zuletzt, auf die letzten
    KONTRAKTION_MAX begrenzt. Leere Liste, wenn die Basis zu kurz ist."""
    if basis_tage < KONTRAKTION_MIN_BASIS_TAGE:
        return []
    teil = c[-basis_tage:]
    if len(teil) < 3 * KONTRAKTION_ORDER + 2:
        return []
    hochs, tiefs = _swing_extrema(teil)
    punkte = sorted([(i, "H") for i in hochs] + [(i, "L") for i in tiefs])
    tiefen = []
    letztes_hoch = None
    for idx, typ in punkte:
        if typ == "H":
            # hoeheres Hoch ersetzt ein noch unbestaetigtes Hoch
            if letztes_hoch is None or teil[idx] >= teil[letztes_hoch]:
                letztes_hoch = idx
        elif letztes_hoch is not None:
            hoch = teil[letztes_hoch]
            if hoch:
                tiefe = (hoch - teil[idx]) / hoch
                if tiefe > KONTRAKTION_MIN_TIEFE:
                    tiefen.append(tiefe)
            letztes_hoch = None
    return tiefen[-KONTRAKTION_MAX:]


def _verengung_stufen(tiefen):
    """Wie viele Uebergaenge tatsaechlich enger werden (T(i+1) <= T(i)*Toleranz).
    Minervinis Kernbild ist die FORTSCHREITENDE Verengung, nicht die blosse
    Anzahl. Rueckgabe (erfuellt, moeglich) - z.B. (2, 3) = 2 von 3 Uebergaengen."""
    if len(tiefen) < 2:
        return 0, 0
    moeglich = len(tiefen) - 1
    erfuellt = sum(1 for i in range(1, len(tiefen))
                   if tiefen[i] <= tiefen[i - 1] * KONTRAKTION_TOLERANZ)
    return erfuellt, moeglich


def _basis_tage(c):
    """Laenge der aktuellen Konsolidierung in Handelstagen: laengstes
    zurueckreichendes Fenster, in dem die Spanne <= KONSOLIDIERUNG_CAP bleibt.

    Bewusst identisch zu _sepaPattern() in "Maick's Trading System.html"
    (gleiche Konstanten, gleiche Schleife) - sonst nennt die SEPA-Checkliste
    der Hauptapp eine andere Wochenzahl als der automatische Detektor fuer
    dieselbe Aktie."""
    n = len(c)
    if n < 10:
        return 0
    tage = 0
    for w in range(10, min(n, KONSOLIDIERUNG_MAX_TAGE) + 1):
        if _range(c[-w:]) <= KONSOLIDIERUNG_CAP:
            tage = w
        else:
            break
    return tage


def _basis_wochen(c):
    """Dieselbe Konsolidierung in Wochen (Anzeige-/Vergleichsformat, wie die
    SEPA-Checkliste der Hauptapp sie nennt)."""
    return round(_basis_tage(c) / 5.0, 1)


def _follow_through_vol(v, tage_seit_ausbruch, vol_basis):
    """Ø-Volumen der Tage NACH dem Ausbruch relativ zum Ø50.

    Minervini verlangt nicht nur einen Volumenschub am Ausbruchstag, sondern
    2-5 Folgetage mit ueberdurchschnittlichem Volumen - ein Ausbruch, dem das
    Folgevolumen fehlt, ist der klassische Fehlausbruch. Rueckgabe None, wenn
    der Ausbruch HEUTE war (dann gibt es noch keine Folgetage zu messen) -
    None heisst ausdruecklich "noch nicht bewertbar", nicht "schlecht".

    WICHTIGE EINSCHRAENKUNG (2026-08-29 an echten Daten gemessen): der JUENGSTE
    Balken ist haeufig eine ANGEFANGENE Sitzung - zwei der vier taeglichen
    Pipeline-Slots (15:00/17:00 Berlin) laufen mitten im US-Handel. Sein
    Volumen ist dann naturgemaess nur ein Bruchteil eines vollen Tages
    (beobachtet: 0.28x statt ~1x). Der Mittelwert hier wird dadurch
    systematisch nach unten gezogen; gemessene 0.07-0.75 sind ueberwiegend
    dieser Effekt, kein schwaches Folgevolumen. Dasselbe gilt seit jeher fuer
    vol_surge (v[-1]/Ø50). Genau deshalb ist GATES["follow_through"] AUS: erst
    muss die Forward-Kohorte zeigen, wie die Zahl bei abgeschlossenen
    Sitzungen (Slot 21:30 / 07:30) verteilt ist, bevor daraus ein Filter
    werden darf. Die Schwelle FT_VOL_MIN ist bis dahin ein Platzhalter aus der
    Literatur (+30-40%), keine gemessene Groesse."""
    if not vol_basis or tage_seit_ausbruch < 1:
        return None
    n = min(tage_seit_ausbruch, FT_VOL_FENSTER)
    folge = [x for x in v[-n:] if x]
    if not folge:
        return None
    return round((sum(folge) / len(folge)) / vol_basis, 2)


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

    # Neue VCP-Feinmessung (rein messend, siehe GATES oben)
    basis_tage = _basis_tage(c)
    basis_wochen = round(basis_tage / 5.0, 1)
    tiefen = _kontraktionen(c, basis_tage)
    verengt, verengt_moeglich = _verengung_stufen(tiefen)
    # Follow-Through-Volumen NUR bei einem echten frischen Durchbruch messen.
    # Sonst waere die Zahl bedeutungslos: ein ARMED-Wert notiert unter seinem
    # Pivot, kann aber ueber dem aelteren pivot_brk liegen - "Tage seit
    # Ausbruch" haette dort keinen Bezugspunkt (erste Fassung lieferte so
    # Werte wie 0.36 fuer Aktien, die gar nicht ausgebrochen sind).
    ft_vol = None
    if frisch_gekreuzt:
        tage_ueber = 0
        for x in reversed(c[-(BREAKOUT_LOOKBACK + 1):]):
            if x > pivot_brk:
                tage_ueber += 1
            else:
                break
        ft_vol = _follow_through_vol(v, max(0, tage_ueber - 1), vol_basis)

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
        # --- VCP-Feinmessung (2026-08-29), noch ohne Status-Einfluss --------
        "kontraktionen": len(tiefen),           # Minervini nennt 3-4 als Ideal
        "kontraktion_tiefen": [round(t * 100, 1) for t in tiefen],  # Naeherung, s. _swing_extrema
        "verengung_stufen": (f"{verengt}/{verengt_moeglich}" if verengt_moeglich else None),
        "basis_wochen": basis_wochen,           # Faustregel >= BASIS_MIN_WOCHEN
        "follow_through_vol": ft_vol,           # None = heute ausgebrochen, noch nicht bewertbar
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
        stop = _stop_info(price, pivot_brk, min(basis_brk) if basis_brk else None)
        stop_txt = ""
        if stop:
            warn = " ⚠️weiter Stop" if stop.get("stop_warnung") else ""
            stop_txt = f", Stop {stop['stop']:.2f} (-{stop['stop_pct']:.1f}%){warn}"
        # Optionale Gates (Default AUS, siehe GATES): erst nach belegter
        # Forward-Kohorte scharf schalten.
        if GATES["basis_wochen"] and basis_wochen < BASIS_MIN_WOCHEN:
            return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                    "detail": (f"Ausbruch ueber {pivot_brk:.2f}, aber Basis erst {basis_wochen} Wochen "
                               f"(< {BASIS_MIN_WOCHEN} - Minervini: zu junge Basis)")}
        if GATES["follow_through"] and ft_vol is not None and ft_vol < FT_VOL_MIN:
            return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                    "detail": (f"Ausbruch ueber {pivot_brk:.2f}, aber Folgevolumen nur {ft_vol:.2f}x "
                               f"(< {FT_VOL_MIN} - Bestaetigung fehlt)")}
        ft_txt = ""
        if ft_vol is not None:
            ft_txt = (f", Folgevolumen {ft_vol:.2f}x"
                      + ("" if ft_vol >= FT_VOL_MIN else " ⚠️schwach"))
        return {"status": "BREAKOUT", "qualitaet": qual, "armed_score": armed_score,
                "pivot": round(pivot_brk, 2), "dist_pct": round((price / pivot_brk - 1) * 100, 1),
                "eng_pct": kennz["eng_pct"], "contraction": kennz["contraction"],
                "dryup": kennz["dryup"], "vol_surge": kennz["vol_surge"],
                "megaphone": megaphone, "supply_score": _supply_score(c, v, pivot_brk),
                "kontraktionen": kennz["kontraktionen"],
                "kontraktion_tiefen": kennz["kontraktion_tiefen"],
                "verengung_stufen": kennz["verengung_stufen"],
                "basis_wochen": basis_wochen, "follow_through_vol": ft_vol, **stop,
                "detail": (f"Frischer Ausbruch ueber {pivot_brk:.2f} auf {vol_surge:.1f}x Volumen "
                           f"(Basis {basis_brk_range*100:.0f}% Range, {basis_wochen} Wochen, "
                           f"{len(tiefen)} Kontraktionen){ft_txt}{stop_txt}")}

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
        if GATES["kontraktionen"] and len(tiefen) < KONTRAKTION_MIN:
            return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                    "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, aber nur {len(tiefen)} "
                               f"Kontraktion(en) (< {KONTRAKTION_MIN} - kein echtes VCP)")}
        if GATES["basis_wochen"] and basis_wochen < BASIS_MIN_WOCHEN:
            return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                    "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, aber Basis erst "
                               f"{basis_wochen} Wochen (< {BASIS_MIN_WOCHEN})")}
        stop = _stop_info(price, pivot, min(c[-ENG_FENSTER:]))
        stop_txt = ""
        if stop:
            warn = " ⚠️weiter Stop" if stop.get("stop_warnung") else ""
            stop_txt = f", Stop {stop['stop']:.2f} (-{stop['stop_pct']:.1f}%){warn}"
        vcp_txt = f", {len(tiefen)} Kontraktionen"
        if verengt_moeglich:
            vcp_txt += f" ({verengt}/{verengt_moeglich} enger werdend)"
        vcp_txt += f", Basis {basis_wochen} Wochen"
        return {"status": "ARMED", "qualitaet": armed_score, "armed_score": armed_score, **kennz, **stop,
                "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, Range {eng*100:.0f}%, "
                           f"Vol-Dry-up {dryup:.2f}{vcp_txt}{stop_txt}")}

    # WATCH: Trend da, Basis bildet sich, noch nicht scharf
    if 0 <= dist <= WATCH_PIVOT:
        return {"status": "WATCH", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
                "detail": (f"{dist*100:.0f}% unter Pivot {pivot:.2f}, Basis Range {eng*100:.0f}% "
                           f"(noch nicht eng/trocken genug)")}

    return {"status": "-", "qualitaet": armed_score, "armed_score": armed_score, **kennz,
            "grund": f"{dist*100:.0f}% unter Pivot (zu weit)"}
