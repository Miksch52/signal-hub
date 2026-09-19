#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wochen-Dossier: verdichtet am Samstag die Ausgaben aller Engines zu einer
Kandidatenliste fuer die naechste Woche (Plan "Vorschlag A", 2026-09-20).

Warum: die Wochenend-Recherche bestand bisher darin, Signal-Hub, Pivot,
Price-Action-Hub, Rotation-Dashboard und Katalysator-Layer einzeln
durchzugehen. Dieses Modul legt fuer hoechstens MAX_KANDIDATEN Top-Setups
alles nebeneinander, was die Engines ohnehin schon liefern - plus die
Veraenderung zur Vorwoche.

Bewusst NICHT:
  - keine neuen Schwellen/Filter: Auswahl = top_setups.json (bestehende
    Cross-Validierung), Reihenfolge = Kern-Setup -> Konfluenz -> Abstand zum
    Pivot. Warnsymbole nutzen dieselben Grenzen wie der Pre-Trade-Pruefer
    (agenten/pre_trade_check.py) und MARKIEREN nur, sie sortieren nichts aus.
    Deshalb kein eigener Forward-Test noetig (siehe CLAUDE.md
    Backtest-Pflicht) - die datierte History-Kopie erlaubt ihn trotzdem.
  - keine Depotdaten: das Dossier liegt oeffentlich auf Pages, das Feld
    im_depot wird nie uebernommen.
  - keine KI: rein deterministisch; eine Text-Zusammenfassung waere Phase 2.
  - Katalysator-Texte sind FREMDINHALT (Perplexity) und werden nur als
    gekennzeichnetes Zitat mit Quelle durchgereicht.

Eingaben (unter --basis, Standard = Projekt-Root; fehlende Dateien werden
uebersprungen): Signal-Hub/data/{top_setups,signals,pivot,
top_setups_katalysator}.json, Price-Action-Hub/data/priceaction.json,
Rotation-Dashboard/data/rotation.json.

Aufruf:
  python3 src/wochen_dossier.py                     # schreibt data/wochen_dossier.json/.js
  python3 src/wochen_dossier.py --vorwoche alt.json # mit Vergleich zur Vorwoche
  python3 src/wochen_dossier.py --push              # zusaetzlich ntfy (NTFY_THEMA/NTFY_SERVER)
  python3 src/wochen_dossier.py --dry-run           # nur Zusammenfassung ausgeben
"""

import argparse
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HIER = os.path.dirname(os.path.abspath(__file__))       # .../Signal-Hub/src
PROJEKT = os.path.dirname(HIER)                           # .../Signal-Hub
ROOT = os.path.dirname(PROJEKT)                           # Projekt-Root
PAGES = "https://mts-hub.pages.dev"

MAX_KANDIDATEN = 8
TOP_THEMEN = 5
MAX_TEXT_LAENGE = 280     # Fremdinhalt (Katalysator), wie in katalysator.py
# Dieselben Grenzen wie agenten/pre_trade_check.py - dort geaendert, hier mitziehen.
STOP_WARN_PCT = 8.0
STOP_MAX_PCT = 10.0
PIVOT_MAX_UEBER_PCT = 5.0
DATEN_MAX_ALTER = timedelta(hours=26)
EARNINGS_WARN_TAGE_STANDARD = 7   # config.json earnings.warn_tage, wie pre_trade_check.py

EINGABEN = {
    "top_setups": "Signal-Hub/data/top_setups.json",
    "signals": "Signal-Hub/data/signals.json",
    "pivot": "Signal-Hub/data/pivot.json",
    "katalysator": "Signal-Hub/data/top_setups_katalysator.json",
    "priceaction": "Price-Action-Hub/data/priceaction.json",
    "rotation": "Rotation-Dashboard/data/rotation.json",
}

TT_NAMEN = {
    "stage2_trend": "Stage-2-Trend",
    "relative_staerke": "Relative Stärke",
    "naehe_52w_hoch": "Nähe 52W-Hoch",
    "basis_konsolidierung": "Basis/Konsolidierung",
    "volumen_bestaetigung": "Volumen",
}

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------- Eingaben

def lade(basis, rel):
    pfad = os.path.join(basis, rel)
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def lade_alle(basis):
    return {k: lade(basis, rel) for k, rel in EINGABEN.items()}


def earnings_warn_tage():
    try:
        with open(os.path.join(PROJEKT, "config.json"), encoding="utf-8") as f:
            return int(json.load(f).get("earnings", {}).get("warn_tage", EARNINGS_WARN_TAGE_STANDARD))
    except (OSError, ValueError, TypeError):
        return EARNINGS_WARN_TAGE_STANDARD


def nach_ticker(daten, liste="treffer"):
    return {t.get("ticker"): t for t in ((daten or {}).get(liste) or []) if t.get("ticker")}


def parse_zeit(s):
    """ISO-Zeit ('2026-09-19T19:54:46+00:00') oder Rotation-Format ('2026-09-19 21:54', Berlin)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # rotation.json 'generated' ist Berliner Ortszeit ohne Zone - fuer die
        # Altersanzeige reicht die Naeherung UTC+2 (hoechstens 1 h daneben).
        dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
    return dt


# ---------------------------------------------------------------- Bausteine

def marktlage(d):
    ts = d.get("top_setups") or {}
    maerkte = {}
    for markt, r in (ts.get("marktregime") or {}).items():
        if not isinstance(r, dict):
            continue
        maerkte[markt] = {
            "ampel": r.get("ampel"),
            "hinweis": r.get("hinweis"),
            "distribution_days": r.get("distribution_days"),
            "sentiment_hinweis": r.get("sentiment_hinweis") if r.get("sentiment_warnung") else None,
            "kaufpause": r.get("ampel") == "rot",
        }

    rot = d.get("rotation") or {}
    themen = sorted((s for s in rot.get("sectors") or [] if s.get("rs") is not None),
                    key=lambda s: -s["rs"])[:TOP_THEMEN]
    reg = rot.get("regime") or {}

    zaehler = (d.get("pivot") or {}).get("zaehler") or {}
    return {
        "maerkte": maerkte,
        "rotation": {
            "notiz": reg.get("note"),
            "breite": reg.get("breadth"),
            "themen": [{"theme": s.get("theme"), "etf": s.get("etf"), "rs": s.get("rs"),
                        "r1m": _runde(s.get("r1m")), "r3m": _runde(s.get("r3m"))} for s in themen],
        },
        "pivot_zaehler": {k: v for k, v in zaehler.items() if isinstance(v, (int, float))},
    }


def _runde(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else None


def pa_muster(pa):
    """Menschlich lesbare Liste der erkannten Price-Action-Muster (gleiche
    Kriterien wie die Chips in price-action-hub.html)."""
    m = (pa or {}).get("muster") or {}
    if m.get("status") != "ok":
        return []
    aus = []
    tb = m.get("trend_bar") or {}
    if tb.get("stark"):
        aus.append("starke Trendbar " + ("↑" if tb.get("richtung") == "bull" else "↓"))
    if (m.get("inside_bar") or {}).get("inside"):
        aus.append("Inside Bar")
    bo = m.get("breakout") or {}
    if bo.get("failed"):
        aus.append("Failed Breakout")
    elif bo.get("breakout_up"):
        aus.append("Breakout ↑")
    elif bo.get("breakout_down"):
        aus.append("Breakout ↓")
    gap = m.get("gap") or {}
    if gap.get("gap_up") or gap.get("gap_down"):
        pct = gap.get("gap_pct")
        txt = "Gap " + ("↑" if gap.get("gap_up") else "↓")
        if isinstance(pct, (int, float)):
            txt += f" {pct:.1f} %"
        if gap.get("gefuellt"):
            txt += " (gefüllt)"
        aus.append(txt)
    bc = m.get("bar_counting") or {}
    if bc.get("typ") and bc.get("zuverlaessig"):
        aus.append(bc["typ"])
    return aus


def warnungen(k, markt_ampel, warn_tage):
    """Markierungen nach den Regeln des Pre-Trade-Pruefers - nie ein Ausschluss."""
    w = []
    if markt_ampel == "rot":
        w.append({"stufe": "stop", "text": "Marktampel rot – laut eigener Regel kein Kauf"})
    stop_pct = k.get("stop_pct")
    if isinstance(stop_pct, (int, float)):
        if stop_pct > STOP_MAX_PCT:
            w.append({"stufe": "stop", "text": f"Stop {stop_pct:.1f} % entfernt – über {STOP_MAX_PCT:g} %"})
        elif stop_pct > STOP_WARN_PCT:
            w.append({"stufe": "warn", "text": f"Stop {stop_pct:.1f} % entfernt – über {STOP_WARN_PCT:g} %"})
    if k.get("stop_warnung"):
        w.append({"stufe": "warn", "text": "Stop-Warnung der Pivot-Engine"})
    dist = k.get("dist_pct")
    if isinstance(dist, (int, float)) and dist > PIVOT_MAX_UEBER_PCT:
        w.append({"stufe": "warn", "text": f"{dist:.1f} % über Pivot – extended"})
    et = k.get("earnings_tage")
    if isinstance(et, (int, float)) and 0 <= et <= warn_tage:
        w.append({"stufe": "warn", "text": f"Earnings in {int(et)} Tag(en) – Gap-Risiko"})
    return w


def sortierschluessel(s, pivot_map):
    dist = (pivot_map.get(s.get("ticker")) or {}).get("dist_pct")
    return (
        0 if s.get("kern_setup") else 1,
        -(s.get("quellen_unabhaengig") or 0),
        abs(dist) if isinstance(dist, (int, float)) else 999.0,
        s.get("ticker") or "",
    )


def kandidaten(d, warn_tage, max_n=MAX_KANDIDATEN):
    setups = list(((d.get("top_setups") or {}).get("setups")) or [])
    signals = nach_ticker(d.get("signals"))
    pivots = nach_ticker(d.get("pivot"))
    pas = nach_ticker(d.get("priceaction"))
    leaders = {l.get("ticker"): l for l in ((d.get("rotation") or {}).get("leaders") or [])}
    kats = ((d.get("katalysator") or {}).get("katalysatoren")) or {}
    regime = ((d.get("top_setups") or {}).get("marktregime")) or {}

    setups.sort(key=lambda s: sortierschluessel(s, pivots))
    aus = []
    for s in setups[:max_n]:
        tk = s.get("ticker")
        sig = signals.get(tk) or {}
        pv = pivots.get(tk) or {}
        pa = pas.get(tk)
        ld = leaders.get(tk)
        kat = kats.get(tk) or {}

        earnings = sig.get("earnings") or pv.get("earnings") or {}
        et = s.get("earnings_tage")
        if et is None and earnings.get("status") == "termin":
            et = earnings.get("tage")

        faktoren = sig.get("faktoren") or {}
        tt = [{"name": TT_NAMEN[k], "ampel": (faktoren.get(k) or {}).get("ampel") or (s.get("trend_template") or {}).get(k),
               "detail": (faktoren.get(k) or {}).get("detail")}
              for k in TT_NAMEN if k in faktoren or k in (s.get("trend_template") or {})]

        c33 = sig.get("code33") or {}
        k = {
            "ticker": tk,
            "name": s.get("name") or sig.get("name"),
            "markt": s.get("markt"),
            "sektor": sig.get("sektor") or pv.get("sektor"),
            "branche": sig.get("branche") or pv.get("branche"),
            "preis": s.get("preis"),
            "waehrung": sig.get("currency") or pv.get("currency"),
            "score": s.get("score"),
            "einstufung": sig.get("einstufung"),
            "pivot_status": s.get("pivot_status"),
            "pivot": s.get("pivot"),
            "stop": s.get("stop"),
            "stop_pct": pv.get("stop_pct"),
            "stop_warnung": bool(pv.get("stop_warnung")),
            "dist_pct": pv.get("dist_pct"),
            "chance_risiko": pv.get("chance_risiko"),
            "qualitaet": s.get("qualitaet"),
            "basis_wochen": pv.get("basis_wochen"),
            "pivot_detail": pv.get("detail"),
            "trend_template": tt,
            "tt_erfuellt": (sig.get("trend_template") or {}).get("erfuellt"),
            "rs_rating": (sig.get("trend_template") or {}).get("rs_rating") or sig.get("rs_rating"),
            "code33": {
                "ampel": c33.get("ampel"),
                "eps_yoy_pct": c33.get("eps_yoy_pct"),
                "umsatz_yoy_pct": c33.get("umsatz_yoy_pct"),
                "marge_delta_pp": c33.get("marge_delta_pp"),
                "quartal": c33.get("quartal"),
            } if c33.get("verfuegbar") else None,
            "earnings_tage": et,
            "earnings_datum": earnings.get("datum"),
            "konfluenz": s.get("quellen_unabhaengig"),
            "quellen": (sig.get("quellen") or {}).get("unabhaengig") or [],
            "kern_setup": bool(s.get("kern_setup")),
            "pa_score": s.get("pa_score"),
            "pa_muster": pa_muster(pa),
            "hebel_ampel": ((pa or {}).get("hebel_ampel") or {}).get("stufe"),
            "rotation_leader": {"theme": ld.get("theme"), "rs": ld.get("rs"),
                                "r1m": _runde(ld.get("r1m")), "r3m": _runde(ld.get("r3m"))} if ld else None,
            "katalysator": {
                "fremdinhalt": True,
                "klasse": kat.get("klasse"),
                "text": str(kat.get("text") or "")[:MAX_TEXT_LAENGE],
                "quellen": [q for q in (kat.get("quellen") or []) if isinstance(q, str) and q.startswith("https://")][:3],
                "stand": kat.get("stand"),
            } if kat.get("text") else None,
            "backtest": s.get("backtest"),
            "detail_link": f"{PAGES}/setup-detail.html?ticker={tk}",
        }
        markt_ampel = (regime.get(s.get("markt")) or {}).get("ampel")
        k["warnungen"] = warnungen(k, markt_ampel, warn_tage)
        aus.append(k)
    return aus, len(setups)


def vergleich(aktuell, vorwoche):
    if not vorwoche:
        return None
    alt = [k.get("ticker") for k in vorwoche.get("kandidaten") or []]
    neu = [k["ticker"] for k in aktuell]
    namen_alt = {k.get("ticker"): k.get("name") for k in vorwoche.get("kandidaten") or []}
    return {
        "vorwoche_kw": vorwoche.get("kw"),
        "neu": [t for t in neu if t not in alt],
        "geblieben": [t for t in neu if t in alt],
        "raus": [{"ticker": t, "name": namen_alt.get(t)} for t in alt if t not in neu],
    }


def datenstand(d, jetzt):
    felder = {"top_setups": "erstellt", "signals": "erstellt", "pivot": "erstellt",
              "katalysator": "erstellt", "priceaction": "erstellt", "rotation": "generated"}
    aus = {}
    for k, feld in felder.items():
        wert = (d.get(k) or {}).get(feld)
        dt = parse_zeit(wert)
        aus[k] = {
            "stand": wert,
            "fehlt": d.get(k) is None,
            "veraltet": bool(dt and jetzt - dt > DATEN_MAX_ALTER),
        }
    return aus


def kalenderwoche(d, jetzt):
    """KW des belegten Datenstands (top_setups.erstellt), nicht des Schreibdatums."""
    dt = parse_zeit((d.get("top_setups") or {}).get("erstellt")) or jetzt
    j, w, _ = dt.isocalendar()
    return f"{j}-KW{w:02d}"


def baue_dossier(d, vorwoche=None, jetzt=None, warn_tage=EARNINGS_WARN_TAGE_STANDARD):
    jetzt = jetzt or datetime.now(timezone.utc)
    kands, n_setups = kandidaten(d, warn_tage)
    return {
        "erstellt": jetzt.replace(microsecond=0).isoformat(),
        "kw": kalenderwoche(d, jetzt),
        "hinweis": "Faktenlage aus den MTS-Engines, keine Kauf- oder Verkaufsempfehlung.",
        "marktlage": marktlage(d),
        "kandidaten": kands,
        "anzahl_top_setups": n_setups,
        "vergleich": vergleich(kands, vorwoche),
        "datenstand": datenstand(d, jetzt),
    }


# ---------------------------------------------------------------- Ausgabe

def schreibe(dossier, pfad_json):
    os.makedirs(os.path.dirname(pfad_json), exist_ok=True)
    txt = json.dumps(dossier, ensure_ascii=False, indent=1)
    with open(pfad_json, "w", encoding="utf-8") as f:
        f.write(txt)
    # .js-Fallback fuer file://, wie bei den uebrigen Dashboard-Daten
    with open(os.path.splitext(pfad_json)[0] + ".js", "w", encoding="utf-8") as f:
        f.write("window.WOCHEN_DOSSIER_DATA = " + txt + ";\n")


def push_text(dossier):
    maerkte = dossier["marktlage"]["maerkte"]
    ampeln = " · ".join(f"{m} {v.get('ampel') or '?'}" for m, v in maerkte.items())
    k = dossier["kandidaten"]
    titel = f"📋 Wochen-Dossier {dossier['kw'].split('-')[1]}: {len(k)} Kandidaten"
    zeilen = [f"Ampel: {ampeln}" if ampeln else "Ampel: unbekannt"]
    if any(v.get("kaufpause") for v in maerkte.values()):
        zeilen.append("⛔ Kaufpause in mindestens einem Markt")
    v = dossier.get("vergleich")
    if v:
        zeilen.append(f"Neu: {', '.join(v['neu']) or '–'} · raus: {', '.join(r['ticker'] for r in v['raus']) or '–'}")
    zeilen.append(", ".join(x["ticker"] for x in k) or "keine Top-Setups")
    veraltet = [q for q, s in dossier["datenstand"].items() if s["fehlt"] or s["veraltet"]]
    if veraltet:
        zeilen.append("⚠️ Daten alt/fehlend: " + ", ".join(veraltet))
    return titel, "\n".join(zeilen)


def sende_ntfy(titel, text, klick=f"{PAGES}/Signal-Hub/wochen-dossier.html"):
    thema = (os.environ.get("NTFY_THEMA") or "").strip()
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip()
    if not thema or "NOCH" in thema.upper():
        print("Wochen-Dossier: kein ntfy-Thema konfiguriert -> kein Push.")
        return False
    req = urllib.request.Request(f"{server.rstrip('/')}/{thema}", data=text.encode("utf-8"), method="POST")
    req.add_header("Title", titel.encode("utf-8"))
    req.add_header("Tags", "clipboard")
    req.add_header("Click", klick)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"Wochen-Dossier: ntfy-Fehler: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--basis", default=ROOT, help="Ordner mit Signal-Hub/, Price-Action-Hub/, Rotation-Dashboard/")
    ap.add_argument("--vorwoche", help="wochen_dossier.json der Vorwoche (fuer neu/raus)")
    ap.add_argument("--aus", default=os.path.join(PROJEKT, "data", "wochen_dossier.json"))
    ap.add_argument("--push", action="store_true", help="ntfy-Push senden")
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben, nur Zusammenfassung")
    a = ap.parse_args()

    d = lade_alle(a.basis)
    if not d.get("top_setups"):
        print("Wochen-Dossier: top_setups.json fehlt - nichts zu tun.")
        return 1
    vorwoche = None
    if a.vorwoche:
        try:
            with open(a.vorwoche, encoding="utf-8") as f:
                vorwoche = json.load(f)
        except (OSError, ValueError):
            print(f"Wochen-Dossier: Vorwoche {a.vorwoche} nicht lesbar - ohne Vergleich.")

    dossier = baue_dossier(d, vorwoche, warn_tage=earnings_warn_tage())
    if vorwoche and vorwoche.get("kw") == dossier["kw"]:
        # Zweiter Lauf in derselben KW: Vergleich gegen sich selbst waere leer
        dossier["vergleich"] = vorwoche.get("vergleich")

    titel, text = push_text(dossier)
    print(titel)
    print(text)
    if a.dry_run:
        return 0
    schreibe(dossier, a.aus)
    print(f"geschrieben: {a.aus}")
    if a.push:
        sende_ntfy(titel, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
