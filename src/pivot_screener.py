#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pivot-Armed-Screener fuer den Daily Signal Hub.

Liest die bereits validierten Treffer aus data/signals.json (der Scorer hat
Aufloesung + Yahoo-Validierung schon erledigt -> kein neuer Netz-Abruf,
echte Entflechtung) und legt mit pivot.py die Pre-Breakout-Linse darueber:
welche Werte stehen GERADE VOR dem Ausbruch (ARMED) oder brechen heute frisch
aus (BREAKOUT) — im Gegensatz zum nachlaufenden Stage-2-Score.

Ausgabe:
  data/pivot.json   - alle Werte mit Status BREAKOUT/ARMED/WATCH (sortiert)
  data/pivot.js     - window.PIVOT_DATA = {...};  (file://-Fallback fuers Dashboard)

Zustands-Uebergaenge (neu ARMED / neu BREAKOUT) werden in
~/Library/Application Support/SignalHub/pivot_state.json gemerkt, damit ein
Push (--notify) nur bei ECHTEN Neuzugaengen feuert (Anti-Spam, wie mts_alarms).

  python3 src/pivot_screener.py              # auswerten + schreiben
  python3 src/pivot_screener.py --notify     # zusaetzlich Push bei Neuzugaengen
  python3 src/pivot_screener.py --limit 60   # nur erste 60 Treffer (Test)
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pfade
import pivot

RANG = {"BREAKOUT": 0, "ARMED": 1, "WATCH": 2, "CHEAT": 3}
HISTORY_TAGE = 15   # Kalendertage, ueber die pivot_history.json zurueckschaut


def lade_config():
    try:
        with open(pfade.CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def lade_signals():
    with open(pfade.SIGNALS_JSON, encoding="utf-8") as f:
        return json.load(f)


def state_load():
    if os.path.exists(pfade.PIVOT_STATE):
        try:
            return json.load(open(pfade.PIVOT_STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def state_save(s):
    with open(pfade.PIVOT_STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def auswerten(limit=None, armed_schwelle=0, cheat_aktiv=True):
    sig = lade_signals()
    treffer = sig.get("treffer", [])
    if limit:
        treffer = treffer[:limit]

    out = []
    for t in treffer:
        chart = t.get("chart") or {}
        if not chart.get("c"):
            continue
        p = pivot.klassifiziere(chart, t.get("preis"), cheat_aktiv=cheat_aktiv)
        st = p.get("status")
        if st not in RANG:
            continue
        if st in ("ARMED", "WATCH", "CHEAT") and p.get("armed_score", 0) < armed_schwelle:
            continue
        out.append({
            "ticker": t.get("ticker"),
            "yahoo_symbol": t.get("yahoo_symbol"),
            "name": t.get("name"),
            "markt": t.get("markt"),
            "sektor": t.get("sektor"),
            "branche": t.get("branche"),
            "preis": t.get("preis"),
            "currency": t.get("currency"),
            "score": t.get("score"),           # bestehender Stage-2-Score (Referenz)
            "im_depot": t.get("im_depot"),
            "earnings": t.get("earnings"),
            "pivot_status": st,
            **{k: p[k] for k in p if k not in ("status",)},
        })

    # Sortierung: erst Zustand (Breakout>Armed>Watch), dann Qualitaet
    out.sort(key=lambda e: (RANG[e["pivot_status"]], -(e.get("qualitaet") or 0)))
    return out, sig.get("erstellt")


def schreibe(out, basis_erstellt):
    zaehler = {s: sum(1 for e in out if e["pivot_status"] == s)
               for s in ("BREAKOUT", "ARMED", "WATCH", "CHEAT")}
    daten = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "basis_signals": basis_erstellt,
        "anzahl": len(out),
        "zaehler": zaehler,
        "parameter": {
            "eng_max_pct": pivot.ENG_MAX * 100, "dryup_max": pivot.DRYUP_MAX,
            "nah_pivot_pct": pivot.NAH_PIVOT * 100, "breakout_vol": pivot.BREAKOUT_VOL,
        },
        "treffer": out,
    }
    with open(pfade.PIVOT_JSON, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, separators=(",", ":"))
    with open(pfade.PIVOT_JS, "w", encoding="utf-8") as f:
        f.write("window.PIVOT_DATA = ")
        json.dump(daten, f, ensure_ascii=False)
        f.write(";")
    return zaehler


def aktualisiere_history(out):
    """Kompakte Tages-Historie fuer BREAKOUT/ARMED/CHEAT-Treffer (Leadership-
    Breadth, Score-Sparkline, Drop-Liste im Dashboard). WATCH bewusst
    ausgeschlossen - zu breit, nicht aussagekraeftig fuer "Leadership". Ein
    Eintrag pro Kalendertag (mehrere Laeufe am selben Tag ueberschreiben
    denselben Schluessel, kein Reihenfolge-Problem bei den taeglichen
    Cron-Slots); auf die letzten HISTORY_TAGE Kalendertage zusammengeschnitten,
    leere Ticker-Eintraege werden entfernt (begrenzt das Datenwachstum)."""
    heute = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(pfade.PIVOT_HISTORY, encoding="utf-8") as f:
            hist = json.load(f).get("ticker", {})
    except Exception:
        hist = {}

    aktive = {e["ticker"]: e for e in out if e.get("pivot_status") in ("BREAKOUT", "ARMED", "CHEAT")}
    for ticker, e in aktive.items():
        if not ticker:
            continue
        eintrag = hist.setdefault(ticker, {"name": e.get("name", ""), "markt": e.get("markt", ""), "tage": {}})
        eintrag["name"] = e.get("name") or eintrag.get("name", "")
        eintrag["markt"] = e.get("markt") or eintrag.get("markt", "")
        eintrag["tage"][heute] = e["pivot_status"]

    grenze = (datetime.now() - timedelta(days=HISTORY_TAGE)).strftime("%Y-%m-%d")
    for ticker in list(hist.keys()):
        tage = {d: s for d, s in hist[ticker].get("tage", {}).items() if d >= grenze}
        if not tage:
            del hist[ticker]
        else:
            hist[ticker]["tage"] = tage

    daten = {"erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ticker": hist}
    with open(pfade.PIVOT_HISTORY, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, separators=(",", ":"))
    with open(pfade.PIVOT_HISTORY_JS, "w", encoding="utf-8") as f:
        f.write("window.PIVOT_HISTORY_DATA = ")
        json.dump(daten, f, ensure_ascii=False)
        f.write(";")
    return hist


def neuzugaenge(out, state):
    """Liefert Liste neuer ARMED/BREAKOUT-Ticker (Uebergang seit letztem Lauf)."""
    heute = datetime.now().strftime("%Y-%m-%d")
    alt = state.get("zustand", {})
    neu_zustand, neu_eintraege = {}, []
    for e in out:
        if e["pivot_status"] in ("ARMED", "BREAKOUT"):
            key = e["ticker"]
            neu_zustand[key] = e["pivot_status"]
            if alt.get(key) != e["pivot_status"]:   # neu oder Status gewechselt
                neu_eintraege.append(e)
    state["zustand"] = neu_zustand
    state["stand"] = heute
    return neu_eintraege


def push(neu):
    """Push der Neuzugaenge ueber denselben ntfy-Kanal wie der Signal-Hub."""
    if not neu:
        print("Keine neuen ARMED/BREAKOUT -> kein Push.")
        return
    try:
        import notify
        c = notify.lade_config()
        b = c.get("benachrichtigung", {})
        thema = b.get("ntfy_thema", "")
        server = b.get("ntfy_server", "https://ntfy.sh")
        if not thema or "NOCH" in thema.upper():
            print("Kein ntfy-Thema gesetzt -> kein Push.")
            return
        brk = [e for e in neu if e["pivot_status"] == "BREAKOUT"]
        arm = [e for e in neu if e["pivot_status"] == "ARMED"]
        zeilen = []
        for e in brk:
            zeilen.append(f"🚀 {e['ticker']} Ausbruch ({e.get('vol_surge')}x Vol)")
        for e in arm[:8]:
            zeilen.append(f"🎯 {e['ticker']} scharf ({e.get('dist_pct')}% unter Pivot)")
        titel = f"Pivot: {len(brk)} Ausbruch / {len(arm)} scharf"
        notify.sende_ntfy(server, thema, titel, "\n".join(zeilen),
                          tags="dart", prio="default")
        print(f"Push gesendet: {titel}")
    except Exception as ex:
        print(f"  ! Push fehlgeschlagen: {ex}")


def main():
    args = sys.argv[1:]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    c = lade_config()
    schwelle = c.get("pivot", {}).get("armed_schwelle", 0)
    cheat_aktiv = c.get("pivot", {}).get("cheat_aktiv", True)

    out, basis = auswerten(limit=limit, armed_schwelle=schwelle, cheat_aktiv=cheat_aktiv)
    zaehler = schreibe(out, basis)
    aktualisiere_history(out)

    state = state_load()
    neu = neuzugaenge(out, state)
    state_save(state)

    if "--notify" in args or "--push" in args:
        push(neu)

    print(f"=== Pivot: {zaehler['BREAKOUT']} Ausbruch · {zaehler['ARMED']} scharf · "
          f"{zaehler['CHEAT']} Cheat · {zaehler['WATCH']} Beobachtung (von {len(out)}) ===")
    print(f"{'Status':9s}{'Ticker':8s}{'Qual':>5s}  {'%uPivot':>8s}  Detail")
    for e in out[:25]:
        print(f"{e['pivot_status']:9s}{e['ticker'] or '?':8s}"
              f"{e.get('qualitaet', 0):5.0f}  {e.get('dist_pct', 0):8.1f}  {e.get('detail', '')}")
    print(f"\nGespeichert: {pfade.PIVOT_JSON}")


if __name__ == "__main__":
    main()
