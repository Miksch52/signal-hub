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

Earnings-Fenster-Validierung (seit 2026-08-21): prueft, ob Minervinis Regel
"nicht kurz vor Earnings kaufen" tatsaechlich einen messbaren Unterschied
macht, statt reine Heuristik zu bleiben - mit derselben unverzerrten
Forward-Methodik wie der Rest dieser Datei. --log haengt ab jetzt zusaetzlich
earnings_tage (aus signals.json, dieselbe Extraktion wie top_setups.py) an
jeden neuen Logbuch-Eintrag; --evaluate splittet ARMED/BREAKOUT je zusaetzlich
in "_earnings_nah" (0..warn_tage Tage bis zum naechsten Termin, Config
earnings.warn_tage, Default 10) und "_earnings_fern" (alles andere,
einschliesslich unbekannt). WICHTIG: bereits vor diesem Umbau geloggte
Eintraege haben kein earnings_tage und zaehlen bis zu ihrer natuerlichen
Reife weiterhin als "_earnings_fern" (kein rueckwirkendes Backfill moeglich -
Yahoo/Earnings-Kalender kennen keine historischen Terminabstaende) - die
_earnings_nah-Kohorte waechst deshalb NUR aus ab jetzt neu geloggten Picks
und braucht wie jede neue Kohorte mehrere Wochen, bevor SCHWELLE_PUSH erreicht
und die Aussage belastbar wird.

Exit-Regel-Backtest (seit 2026-08-21): bislang testen alle Backtests im
System nur den EINSTIEG (welches Setup kaufen) - simuliert jetzt zusaetzlich
die Minervini-Ausstiegsstaffel aus dem Trade-Planner (Maick's Trading
System.html: T1 +8%/50%, T2 +20%/25%, T3 +40%/25%, siehe _simulate_exit())
Tag fuer Tag gegen den ECHTEN historischen Kursverlauf (Hoch/Tief, nicht nur
Schluss - ein Stop/Target kann intraday ausgeloest werden) und vergleicht das
Ergebnis mit einfachem Halten bis zum selben 12-Wochen-Zeitpunkt (derselbe
Schlusskurs, damit beide Zahlen exakt denselben Stichtag meinen statt wie
sonst im System "jetzt, wann auch immer --evaluate laeuft"). KEINE
zusaetzliche Yahoo-Anfrage noetig: scorer.py::yahoo_chart() holt bei jedem
Aufruf ohnehin schon 2 Jahre komplette Tageshistorie (seit 2026-08-21
zusaetzlich mit Datums-Array) - evaluate() nutzte davon bisher nur den
letzten Schlusskurs, jetzt zusaetzlich highs/lows/dates fuer dieselben schon
abgerufenen Ticker. Ergebnis wird pro Logbuch-Eintrag EINMALIG unter
"exit_sim" berechnet und dauerhaft gecacht (wie "realisiert") - kein erneutes
Nachrechnen bei spaeteren --evaluate-Laeufen, die Kosten wachsen also nur mit
der Zahl neu gereifter Picks, nicht kumulativ mit der Zeit. Nur fuer ab
2026-08-21 neu geloggte Eintraege moeglich (aeltere haben kein "stop"-Feld,
siehe log_heute()) und erst auswertbar, sobald der Chart tatsaechlich 78
Kalendertage nach dem Signal erreicht (frueher waere der Hold-Vergleich
unfair fruehzeitig abgeschnitten).

Ausgabe: data/pivot_backtest.json (+ Konsolentabelle).
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pfade
import pivot

HORIZONTE = [("4W", 20), ("8W", 40), ("12W", 60)]   # Forward-Fenster in Handelstagen
RETRO_STEP = 2                                       # jeden 2. Tag (weniger Autokorrelation)
GATE_GRUENDE = {"kein Aufwaertstrend (Stage 2)", "zu wenig Historie"}

# Exit-Regel-Backtest (seit 2026-08-21, siehe Modul-Docstring): dieselbe
# Staffel wie Maick's Trading System.html Trade-Planner ("Minervini T1/T2/T3").
T1_PCT, T1_ANTEIL = 0.08, 0.50
T2_PCT, T2_ANTEIL = 0.20, 0.25
T3_PCT, T3_ANTEIL = 0.40, 0.25
EXIT_HORIZONT_TAGE = 78   # deckt sich mit dem 12W-Bucket oben (Kalendertage)


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


def _earnings_tage_map():
    """Ticker -> Tage bis zum naechsten Earnings-Termin (None = kein Termin im
    Pruef-Fenster oder Quelle nicht erreichbar) - dieselbe Extraktion wie
    top_setups.py, hier separat gehalten (Entflechtung, kein Cross-Import)."""
    try:
        signale = json.load(open(pfade.SIGNALS_JSON, encoding="utf-8")).get("treffer", [])
    except Exception:
        return {}
    out = {}
    for e in signale:
        earn = e.get("earnings") or {}
        if earn.get("status") == "termin":
            out[e.get("ticker")] = earn.get("tage")
    return out


def _trend_template_map():
    """Ticker -> Trend-Template bestanden (True/False/None).

    Liegt in signals.json, nicht in pivot.json (der Pivot-Detektor kennt das
    Template nicht) - deshalb wie _earnings_tage_map() separat extrahiert,
    ohne Cross-Import. None = nicht bewertbar (kein RS-Rating, siehe
    scorer.f_trend_template)."""
    try:
        signale = json.load(open(pfade.SIGNALS_JSON, encoding="utf-8")).get("treffer", [])
    except Exception:
        return {}
    return {e.get("ticker"): (e.get("trend_template") or {}).get("pass")
            for e in signale if e.get("trend_template")}


def log_heute():
    if not os.path.exists(pfade.PIVOT_JSON):
        print("Keine pivot.json -> nichts zu loggen.")
        return
    daten = json.load(open(pfade.PIVOT_JSON, encoding="utf-8"))
    earn_map = _earnings_tage_map()
    tt_map = _trend_template_map()
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
            "stop": e.get("stop"),   # seit 2026-08-21, Basis fuer den Exit-Regel-Backtest (_simulate_exit)
            "earnings_tage": earn_map.get(e.get("ticker")),   # seit 2026-08-21, siehe Modul-Docstring
            # SEPA-Abgleich 2026-08-29: vier neue Kriterien, die bislang nicht
            # gemessen wurden. Alle vier sind im Live-Betrieb noch OHNE Wirkung
            # (pivot.GATES / config trend_template.strikt sind aus) - hier wird
            # zunaechst nur die unverzerrte Forward-Kohorte aufgebaut, damit
            # spaeter belegbar ist, ob ein Gate den Ertrag verbessert.
            # Wie beim Earnings-Split gilt: aeltere Logbuch-Eintraege haben
            # diese Felder nicht und bleiben bis zu ihrer Reife in der
            # jeweiligen "unbekannt"-Kohorte (kein rueckwirkendes Backfill).
            "kontraktionen": e.get("kontraktionen"),
            "basis_wochen": e.get("basis_wochen"),
            "follow_through_vol": e.get("follow_through_vol"),
            "tt_pass": tt_map.get(e.get("ticker")),
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


def _finde_start_index(dates, entry_datum):
    """Erster Chart-Index mit dates[i] >= entry_datum - der Entry gilt zum
    Signalkurs am Signaltag, die Simulation beobachtet ab diesem Tag."""
    for i, d in enumerate(dates):
        if d and d >= entry_datum:
            return i
    return None


def _simulate_exit(chart, entry_datum, entry_preis, stop):
    """Simuliert die Minervini-Ausstiegsstaffel (T1/T2/T3, siehe Modul-
    Docstring) Tag fuer Tag gegen den echten Kursverlauf. Nutzt Hoch/Tief je
    Tag statt nur Schluss - ein Stop/Target kann intraday ausgeloest werden,
    ohne dass der Schlusskurs das zeigt. Gibt None zurueck, wenn der
    Signaltag im Chart fehlt, kein plausibler Stop vorliegt, oder der
    12-Wochen-Horizont (EXIT_HORIZONT_TAGE) im verfuegbaren Chart noch nicht
    erreicht ist (sonst waere der Hold-Vergleich unfair fruehzeitig
    abgeschnitten - ein spaeterer Lauf mit mehr Kalenderzeit holt das nach)."""
    dates = chart.get("dates") or []
    highs, lows, closes = chart.get("highs") or [], chart.get("lows") or [], chart.get("closes") or []
    if not dates or entry_preis is None or stop is None or stop >= entry_preis:
        return None
    start = _finde_start_index(dates, entry_datum)
    if start is None:
        return None
    try:
        grenze = (datetime.strptime(entry_datum, "%Y-%m-%d")
                  + timedelta(days=EXIT_HORIZONT_TAGE)).strftime("%Y-%m-%d")
    except ValueError:
        return None
    horizont_idx = None
    for i in range(start, len(dates)):
        if dates[i] and dates[i] >= grenze:
            horizont_idx = i
            break
    if horizont_idx is None:
        return None   # Chart deckt den 78-Tage-Horizont noch nicht ab
    hold_close = closes[horizont_idx]

    t1_preis = entry_preis * (1 + T1_PCT)
    t2_preis = entry_preis * (1 + T2_PCT)
    t3_preis = entry_preis * (1 + T3_PCT)
    rest, erloese = 1.0, 0.0
    t1_ok = t2_ok = t3_ok = gestoppt = False
    for i in range(start, horizont_idx + 1):
        if rest <= 0:
            break
        if lows[i] <= stop:
            erloese += rest * stop
            rest = 0.0
            gestoppt = True
            break
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
        erloese += rest * hold_close   # zum Horizont noch offene Restposition, mark-to-market

    blended = erloese / entry_preis - 1.0
    hold = hold_close / entry_preis - 1.0
    return {"blended_return": round(blended * 100, 2), "hold_return": round(hold * 100, 2),
            "t1": t1_ok, "t2": t2_ok, "t3": t3_ok, "gestoppt": gestoppt}


def _warn_tage():
    try:
        cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
        return (cfg.get("earnings") or {}).get("warn_tage", 10)
    except Exception:
        return 10


def evaluate():
    try:
        import scorer
    except Exception as ex:
        print(f"  ! scorer-Import fehlgeschlagen ({ex}) -> kein Yahoo-Abruf moeglich.")
        return {}, []
    lb = _logbuch_load()
    if not lb:
        print("Forward-Logbuch leer -> erst --log sammeln lassen.")
        return {}, []
    cache = scorer.lade_cache()
    heute = datetime.now().date()
    warn = _warn_tage()
    # Qualitaets-Split (seit 2026-07-24): Basis fuer die Kalibrierung von
    # config.pivot.armed_schwelle - zeigt, ob hohe qualitaet-Werte forward
    # tatsaechlich besser laufen (erst ab ~8 Picks je Kohorte aussagekraeftig).
    basis_stati = ("BREAKOUT", "ARMED")
    qual_stati = ("ARMED_q70+", "ARMED_q<70")
    # Earnings-Fenster-Split (seit 2026-08-21, siehe Modul-Docstring): prueft
    # je Pivot-Status getrennt (sonst wuerde der ARMED/BREAKOUT-Unterschied den
    # Earnings-Effekt ueberdecken), ob ein Kauf nahe an Earnings tatsaechlich
    # schlechter ausgeht als einer mit sicherem Abstand.
    earn_stati = ("ARMED_earnings_nah", "ARMED_earnings_fern",
                  "BREAKOUT_earnings_nah", "BREAKOUT_earnings_fern")
    # Exit-Regel-Backtest (seit 2026-08-21, siehe Modul-Docstring): eigene
    # Kohorten je Status fuer "Staffel" (simulierter T1/T2/T3-Ausstieg) vs.
    # "Hold" (einfach bis zum selben 78-Tage-Stichtag halten) - beide nur im
    # "12W"-Slot befuellt, da die Simulation inhaerent ein fixer 78-Tage-Test
    # ist (keine 4W/8W-Zwischenstaende).
    exit_stati = ("ARMED_exit_staffel", "ARMED_exit_hold",
                  "BREAKOUT_exit_staffel", "BREAKOUT_exit_hold")
    # SEPA-Abgleich 2026-08-29: je Kriterium eine Ja/Nein-Kohorte, getrennt
    # nach Pivot-Status (sonst ueberdeckt der ARMED/BREAKOUT-Unterschied den
    # gesuchten Effekt - dieselbe Logik wie beim Earnings-Split). Erst wenn
    # eine Kohorte SCHWELLE_PUSH erreicht UND besser laeuft, darf das
    # zugehoerige Gate in pivot.GATES scharf geschaltet werden.
    sepa_stati = tuple(
        f"{st}_{k}" for st in basis_stati for k in (
            "vcp_ge2", "vcp_lt2",              # >= KONTRAKTION_MIN Kontraktionen?
            "basis_ge7w", "basis_lt7w",        # >= BASIS_MIN_WOCHEN Konsolidierung?
            "tt_pass", "tt_fail",              # Trend Template 8/8 bestanden?
        )
    ) + ("BREAKOUT_ft_ok", "BREAKOUT_ft_schwach")   # Folgevolumen nur bei Ausbruechen sinnvoll
    eimer = {s: {h: [] for h, _ in HORIZONTE}
             for s in basis_stati + qual_stati + earn_stati + exit_stati + sepa_stati}
    # Einzelfaelle (seit 2026-08): dieselben Belege wie eimer, aber ticker-
    # scharf statt aggregiert - Basis fuer die "Fundstellen"-Ansicht im
    # Backtest-Report (dashboard.html hat sonst keinen Zugriff auf das lokale,
    # nie synchronisierte Forward-Logbuch in ~/Library/Application Support).
    einzelfaelle = []
    aktuell = {}
    charts = {}   # sym -> voller Chart-Dict (closes/highs/lows/dates), fuer _simulate_exit
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
            charts[sym] = d
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
        et = e.get("earnings_tage")
        nah = et is not None and 0 <= et <= warn
        eimer[f"{e['status']}_earnings_{'nah' if nah else 'fern'}"][bk].append(ret)

        # --- SEPA-Kriterien (2026-08-29) --------------------------------
        # Fehlt ein Feld (Eintrag von vor diesem Umbau), wird NICHT einsortiert
        # - lieber eine kleinere, saubere Kohorte als eine, in der "unbekannt"
        # stillschweigend als "nein" zaehlt und das Ergebnis verfaelscht.
        # Schwellen kommen aus pivot.py, NICHT als Zahlen hierher kopiert: die
        # Kohorte muss exakt dort teilen, wo das spaetere Gate greift. Zwei
        # unabhaengig gepflegte Zahlen wuerden sonst still auseinanderlaufen
        # und man wuerde ein Gate anhand einer Kohorte belegen, die eine
        # andere Frage beantwortet.
        kt = e.get("kontraktionen")
        if kt is not None:
            eng = kt >= pivot.KONTRAKTION_MIN
            eimer[f"{e['status']}_vcp_{'ge2' if eng else 'lt2'}"][bk].append(ret)
        bw = e.get("basis_wochen")
        if bw is not None:
            lang = bw >= pivot.BASIS_MIN_WOCHEN
            eimer[f"{e['status']}_basis_{'ge7w' if lang else 'lt7w'}"][bk].append(ret)
        tt = e.get("tt_pass")
        if tt is not None:
            eimer[f"{e['status']}_tt_{'pass' if tt else 'fail'}"][bk].append(ret)
        ft = e.get("follow_through_vol")
        if ft is not None and e["status"] == "BREAKOUT":
            eimer["BREAKOUT_ft_ok" if ft >= pivot.FT_VOL_MIN else "BREAKOUT_ft_schwach"][bk].append(ret)

        # Exit-Regel-Backtest: einmalig berechnen und dauerhaft im Logbuch-
        # Eintrag cachen (wie "realisiert") - kein erneutes Nachrechnen bei
        # spaeteren Laeufen, siehe Modul-Docstring.
        if e.get("exit_sim") is None and e.get("stop") is not None:
            sim = _simulate_exit(charts.get(sym) or {}, e["datum"], e["preis_signal"], e["stop"])
            if sim is not None:
                e["exit_sim"] = sim
        if e.get("exit_sim") is not None:
            sim = e["exit_sim"]
            eimer[f"{e['status']}_exit_staffel"]["12W"].append(sim["blended_return"] / 100)
            eimer[f"{e['status']}_exit_hold"]["12W"].append(sim["hold_return"] / 100)

        einzelfaelle.append({
            "ticker": e["ticker"], "yahoo_symbol": sym, "status": e["status"],
            "qualitaet": e.get("qualitaet"), "datum": e["datum"],
            "preis_signal": e["preis_signal"], "horizont": bk,
            "return_pct": round(ret * 100, 2),
            "earnings_tage": et,
            "exit_sim": e.get("exit_sim"),
            "kontraktionen": kt, "basis_wochen": bw,
            "follow_through_vol": e.get("follow_through_vol"), "tt_pass": tt,
        })
    scorer.speichere_cache(cache)
    _logbuch_save(lb)
    fr = {st: {h: _stats(eimer[st][h]) for h, _ in HORIZONTE} for st in eimer}
    return fr, einzelfaelle


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
def _schreibe_backtest(out):
    """JSON + window.PIVOT_BACKTEST_DATA-Fallback (file://-Zugriff ohne Server),
    analog pivot_screener.py::schreibe()."""
    with open(pfade.PIVOT_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(pfade.PIVOT_BACKTEST_JS, "w", encoding="utf-8") as f:
        f.write("window.PIVOT_BACKTEST_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")


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
        fr, einzelfaelle = evaluate()
        if fr and "--nopush" not in args:
            push_reife(fr)
        bestand = {}
        if os.path.exists(pfade.PIVOT_BACKTEST):
            try:
                bestand = json.load(open(pfade.PIVOT_BACKTEST, encoding="utf-8"))
            except Exception:
                bestand = {}
        bestand["forward_realisiert"] = fr
        bestand["forward_einzelfaelle"] = einzelfaelle
        bestand["forward_stand"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _schreibe_backtest(bestand)
        if fr:
            _druck_tabelle("=== FORWARD realisiert (unverzerrt, reift ueber Zeit) ===", fr)
        print(f"\nGespeichert: {pfade.PIVOT_BACKTEST} ({len(einzelfaelle)} Einzelfaelle)")
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
    # forward_realisiert/-einzelfaelle stammen aus --evaluate - beim RETRO-Lauf
    # (der taeglich VOR --evaluate laeuft, siehe run.py) nicht ueberschreiben,
    # falls schon vorhanden.
    if os.path.exists(pfade.PIVOT_BACKTEST):
        try:
            alt = json.load(open(pfade.PIVOT_BACKTEST, encoding="utf-8"))
            for k in ("forward_realisiert", "forward_einzelfaelle", "forward_stand"):
                if k in alt:
                    out[k] = alt[k]
        except Exception:
            pass
    _schreibe_backtest(out)
    _druck_tabelle(f"=== RETRO Walk-Forward ueber {n} Charts (Bias: s. Hinweis) ===",
                   ergebnis, mit_edge=True)
    print(f"\nGespeichert: {pfade.PIVOT_BACKTEST}")


if __name__ == "__main__":
    main()
