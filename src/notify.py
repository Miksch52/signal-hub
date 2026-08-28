#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Push-Benachrichtigung fuer den Daily Signal Hub via ntfy.sh (kostenlos).

Liest data/signals.json, nimmt die Top-Treffer ab Mindest-Score und schickt sie
als Push an dein ntfy-Thema (App "ntfy" auf iPhone/iPad abonnieren).

Test:  python3 src/notify.py            # sendet aktuelle Top-Treffer
       python3 src/notify.py --test     # sendet eine Testnachricht
       python3 src/notify.py --morgens  # Morning-Brief-Format (Marktampel-Status
                                         # je Markt vorangestellt, auch ohne
                                         # Kandidaten nicht stumm) - wird von
                                         # run.py automatisch beim ersten
                                         # faelligen Slot des Tages gesetzt
"""

import json
import os
import ssl
import sys
import urllib.request

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade
PROJEKT = pfade.PROJEKT
DATA = pfade.DATA
CONFIG_PFAD = pfade.CONFIG

FLAGGE = {"USA": "US", "Europa": "EU"}

def lade_config():
    with open(CONFIG_PFAD, encoding="utf-8") as f:
        return json.load(f)

DASHBOARD_URL = "https://mts-hub.pages.dev/Signal-Hub/signal-hub.html"

def sende_ntfy(server, thema, titel, text, tags="chart_with_upwards_trend", prio="default", click=None):
    url = f"{server.rstrip('/')}/{thema}"
    daten = text.encode("utf-8")
    req = urllib.request.Request(url, data=daten, method="POST")
    req.add_header("Title", titel.encode("utf-8"))
    req.add_header("Tags", tags)
    req.add_header("Priority", prio)
    if click:
        # Tippen auf die Push-Nachricht oeffnet click direkt (ntfy-"Click"-
        # Header, https://docs.ntfy.sh/publish/#click-action) - spart das
        # Nachschlagen im Dashboard fuer den haeufigsten Fall (Signal
        # anschauen). Nur die Dashboard-Startseite, kein Tiefen-Link auf
        # EINE Aktie: die Nachricht listet meist mehrere Ticker, ein
        # Click-Ziel kann nur EINEN Ort oeffnen.
        req.add_header("Click", click)
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        return r.status

STATE = pfade.STATE

def _state_load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _state_save(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

AMPEL_ICON = {"gruen": "🟢", "gelb": "🟡", "rot": "🔴"}

def _pivot_treffer():
    """pivot.json::treffer, [] wenn die Datei fehlt/kaputt ist - reiner
    Best-Effort-Read wie der Rest dieser Datei, blockiert den Push nie."""
    if not os.path.exists(pfade.PIVOT_JSON):
        return []
    try:
        return json.load(open(pfade.PIVOT_JSON, encoding="utf-8")).get("treffer", [])
    except Exception:
        return []


def _staerkste_kandidaten_zeilen(qual_marktkarte, max_n=3):
    """Top-N nach Score unter den Kauf-Kandidaten, die zusaetzlich einen
    ARMED/BREAKOUT-Pivot haben - fuers Morning Brief, damit der erste Push
    des Tages nicht nur SAGT, wie viele Kandidaten es gibt, sondern die
    staerksten mit Pivot/Stop gleich mitliefert (kein Nachschlagen im
    Dashboard fuer die Handvoll, die ohnehin am ehesten dran sind).

    Bewusst eine LEICHTERE Version der Startseiten-"Top-Setups" (die zusaetzlich
    Price-Action-Hub-Score und Katalysator-Text kreuzen): pivot.json liegt zum
    Zeitpunkt dieses Pushs vor (laeuft im selben Signal-Hub-Job VOR notify.py),
    top_setups.json noch nicht (wird erst im NACHGELAGERTEN Price-Action-Hub-
    Job dieses Pipeline-Laufs berechnet) - siehe pipeline.yml. `qual_marktkarte`
    ist ticker->score, bereits um Markt-Regime-/Sentiment-Ausschluesse bereinigt
    (dieselbe Menge wie der Hauptteil des Pushs), damit hier kein Ticker aus
    einem roten Markt auftaucht, den der Rest der Nachricht gerade zurueckhaelt."""
    kandidaten = []
    for t in _pivot_treffer():
        ticker = t.get("ticker")
        if ticker not in qual_marktkarte or t.get("pivot_status") not in ("ARMED", "BREAKOUT"):
            continue
        kandidaten.append(t)
    kandidaten.sort(key=lambda t: qual_marktkarte.get(t.get("ticker"), 0), reverse=True)
    zeilen = []
    for t in kandidaten[:max_n]:
        status = "🚀" if t.get("pivot_status") == "BREAKOUT" else "🎯"
        pivot, stop = t.get("pivot"), t.get("stop")
        preisangabe = f" · Pivot {pivot:.2f} / Stop {stop:.2f}" if pivot and stop else ""
        zeilen.append(f"  {status} {t['ticker']}  {qual_marktkarte[t['ticker']]:.0f}{preisangabe}")
    return zeilen


def _earnings_depot_zeilen(warn_tage):
    """Offene Depot-Positionen (im_depot, Namensabgleich gegen mts_data.json -
    siehe pivot.json/scorer.py) mit Earnings innerhalb des Warnfensters. Reine
    Namens-/Terminwarnung, KEINE Betraege/Stueckzahlen (dieselbe Datengrenze
    wie SIGNALHUB_DEPOT_NAMEN) - laeuft deshalb unveraendert im Cloud- UND im
    lokalen Lauf. Frueheste Termine zuerst."""
    treffer = [t for t in _pivot_treffer()
               if t.get("im_depot") and t.get("earnings")
               and t["earnings"].get("status") == "termin"
               and t["earnings"].get("tage") is not None
               and 0 <= t["earnings"]["tage"] <= warn_tage]
    treffer.sort(key=lambda t: t["earnings"]["tage"])
    return [f"  📅 {t['ticker']}  in {t['earnings']['tage']}T" for t in treffer]

def _ampel_zeilen(d):
    """Eine Zeile je aktivem Markt mit Ampel-Icon + Klartext-Hinweis - Basis
    fuers Morning Brief, damit der erste Push des Tages immer die Marktlage
    zeigt statt sie (wie die anderen drei Slots) nur bei Unterdrueckung zu
    erwaehnen. Seit 2026-08-15 haengt zusaetzlich der Distribution-Days-/
    Follow-Through-Day-Sentiment-Hinweis an (scorer.py::markt_regime,
    sentiment_hinweis) - taucht auch auf, wenn der Trend selbst noch gruen
    ist (institutioneller Verkaufsdruck kann dem Trendbruch vorauslaufen,
    siehe Erlaeuterung unten bei baue_nachricht())."""
    zeilen = []
    for markt, r in (d.get("marktregime") or {}).items():
        icon = AMPEL_ICON.get(r.get("ampel"), "⚪")
        zeile = f"{icon} {markt}: {r.get('hinweis') or r.get('ampel') or '?'}"
        if r.get("sentiment_hinweis"):
            zeile += f" ⚠️ {r['sentiment_hinweis']}"
        zeilen.append(zeile)
    return zeilen


def baue_nachricht(cfg, morgens=False):
    bcfg = cfg["benachrichtigung"]
    ecfg = cfg.get("earnings", {})
    rcfg = cfg.get("marktregime", {})
    pfad = pfade.SIGNALS_JSON
    if not os.path.exists(pfad):
        return None, None
    d = json.load(open(pfad, encoding="utf-8"))
    minscore = bcfg.get("min_score_fuer_push", 70)
    maxn = bcfg.get("max_treffer_im_push", 8)
    warn = ecfg.get("warn_tage", 10)
    excl = ecfg.get("aktiv") and ecfg.get("push_ausschliessen")

    def earnings_bald(t):
        e = t.get("earnings")
        return bool(e and e.get("tage") is not None and 0 <= e["tage"] <= warn)

    qual = [t for t in d.get("treffer", [])
            if t["score"] >= minscore and not (excl and earnings_bald(t))]

    # Markt-Regime-Gate (Minervini: keine neuen Positionen im schwachen Markt):
    # Treffer aus Maerkten mit roter Ampel fliegen aus dem Push - im Dashboard
    # bleiben sie sichtbar (dort zeigt die Ampel den Kontext).
    rot = set()
    unterdrueckt_rot = 0
    # Sentiment-Gate (seit 2026-08-15, Richard S. Love/O'Neil): zusaetzlich
    # und UNABHAENGIG von der Trend-Ampel - ein Markt kann noch gruen sein
    # (Kurs > MA50/MA200), waehrend schon 5+ Distribution Days institutionellen
    # Verkaufsdruck zeigen (Fruehwarnung VOR dem Trendbruch), oder ein echter
    # Ruecksetzer (>=3%) noch keinen Follow-Through Day hatte (Bestaetigung
    # des neuen Aufwaertstrends steht laut Minervini/O'Neil noch aus). Nur der
    # harte Fall (state 0, sentiment_warnung) unterdrueckt den Push - die
    # weichere "3-4 Distribution Days, beobachten"-Stufe bleibt reiner
    # Klartext-Hinweis im Morning Brief (_ampel_zeilen), kein Filter.
    sentiment_warn = set()
    unterdrueckt_sentiment = 0
    if rcfg.get("aktiv", True):
        if rcfg.get("push_bei_rot_unterdruecken", True):
            rot = {m for m, r in (d.get("marktregime") or {}).items()
                   if r.get("ampel") == "rot"}
        if rcfg.get("push_bei_sentiment_warnung_unterdruecken", True):
            sentiment_warn = {m for m, r in (d.get("marktregime") or {}).items()
                              if r.get("sentiment_warnung") and m not in rot}
        if rot:
            vorher = len(qual)
            qual = [t for t in qual if t.get("markt") not in rot]
            unterdrueckt_rot = vorher - len(qual)
        if sentiment_warn:
            vorher = len(qual)
            qual = [t for t in qual if t.get("markt") not in sentiment_warn]
            unterdrueckt_sentiment = vorher - len(qual)
    unterdrueckt = unterdrueckt_rot + unterdrueckt_sentiment
    # ticker->score der bereits um Markt-Regime/Sentiment bereinigten Menge -
    # Basis fuer die Morning-Brief-Zusatzabschnitte unten (_staerkste_kandidaten_zeilen).
    qual_marktkarte = {t["ticker"]: t["score"] for t in qual}

    nur_neue = bcfg.get("nur_neue", False)
    if nur_neue:
        st = _state_load()
        gesehen = set(st.get("push_gesehen", []))
        liste = [t for t in qual if t["ticker"] not in gesehen]
        st["push_gesehen"] = [t["ticker"] for t in qual]   # Dropouts raus, Re-Entry = wieder neu
        _state_save(st)
    else:
        liste = qual
    liste = liste[:maxn]
    kopf = ("\n".join(_ampel_zeilen(d)) + "\n\n") if morgens else ""

    if not liste:
        if unterdrueckt:
            # Statt stiller Leere einmal ehrlich melden, WARUM nichts kommt -
            # Trend-Grund (rot) und Sentiment-Grund (Distribution Days/FTD)
            # getrennt benannt, weil es zwei unabhaengige Ampeln sind.
            gruende = []
            if unterdrueckt_rot:
                gruende.append(f"{unterdrueckt_rot} wegen roter Marktampel "
                                f"({', '.join(sorted(rot))}: Index unter MA200)")
            if unterdrueckt_sentiment:
                gruende.append(f"{unterdrueckt_sentiment} wegen Distribution-Days-/"
                                f"Follow-Through-Day-Warnung ({', '.join(sorted(sentiment_warn))}: "
                                f"institutioneller Verkaufsdruck bzw. unbestätigter Rücksetzer)")
            titel = "🔴 Markt-Warnung – Push-Signale unterdrückt" if rot else "⚠️ Sentiment-Warnung – Push-Signale zurückgehalten"
            return (titel,
                    kopf + f"{unterdrueckt} Kauf-Kandidat(en) zurückgehalten: " + "; ".join(gruende)
                    + ". Minervini/O'Neil: in einem schwachen bzw. unbestätigten Markt "
                    f"keine neuen Positionen eröffnen.")
        if morgens:
            # Morning Brief bleibt nicht stumm wie die anderen drei Slots -
            # ohne Kandidaten zeigt es wenigstens die Marktlage + faellige
            # Earnings in bestehenden Positionen (unabhaengig von neuen Ideen
            # relevant, deshalb auch hier noch angehaengt).
            text = kopf.rstrip() + "\nKeine Kauf-Kandidaten über der Schwelle."
            earn_zeilen = _earnings_depot_zeilen(warn)
            if earn_zeilen:
                text += f"\n\n📅 Earnings in offenen Positionen (≤{warn}T):\n" + "\n".join(earn_zeilen)
            return "☀️ Morning Brief", text
        return None, None

    titel = ("☀️ Morning Brief · " if morgens else "") + (
        f"📈 {len(liste)} NEUE Kauf-Kandidaten (≥ {minscore})" if nur_neue
        else f"📈 Signal Hub – {len(liste)} Top-Werte (≥ {minscore})")
    zeilen = []
    for t in liste:
        markt = FLAGGE.get(t["markt"], "")
        e = t.get("earnings")
        ew = f" ⚠️{e['tage']}T" if earnings_bald(t) else ""
        zeilen.append(f"{t['ticker']}  {t['score']:.0f}  {t['name'][:22]} ({markt},{t['quellen']['ausgaben']}Q){ew}")
    fuss = f"\n{len(qual)} Kauf-Kandidaten gesamt · {d.get('anzahl',0)} bewertet"
    if unterdrueckt_rot:
        fuss += f"\n🔴 {unterdrueckt_rot} Wert(e) aus {', '.join(sorted(rot))} unterdrückt (Markt rot)"
    if unterdrueckt_sentiment:
        fuss += (f"\n⚠️ {unterdrueckt_sentiment} Wert(e) aus {', '.join(sorted(sentiment_warn))} "
                 f"zurückgehalten (Distribution Days/Follow-Through-Day)")
    text = kopf + "\n".join(zeilen) + fuss
    if morgens:
        # Zwei Zusatzabschnitte NUR im ersten Push des Tages (sonst waere die
        # Nachricht an den drei anderen Slots unnoetig lang) - siehe
        # _staerkste_kandidaten_zeilen()/_earnings_depot_zeilen() oben fuer die
        # Datengrundlage und deren bewusste Grenzen.
        staerkste = _staerkste_kandidaten_zeilen(qual_marktkarte)
        if staerkste:
            text += "\n\n🎯 Stärkste mit Pivot heute:\n" + "\n".join(staerkste)
        earn_zeilen = _earnings_depot_zeilen(warn)
        if earn_zeilen:
            text += f"\n\n📅 Earnings in offenen Positionen (≤{warn}T):\n" + "\n".join(earn_zeilen)
    return titel, text

def main():
    cfg = lade_config()
    bcfg = cfg["benachrichtigung"]
    if not bcfg.get("aktiv"):
        print("Benachrichtigung in config deaktiviert.")
        return
    thema = bcfg.get("ntfy_thema", "")
    server = bcfg.get("ntfy_server", "https://ntfy.sh")
    if not thema or "NOCH" in thema.upper():
        sys.exit("Kein ntfy-Thema in config gesetzt.")

    if "--test" in sys.argv:
        sende_ntfy(server, thema, "✅ Signal Hub Test",
                   "Push funktioniert! Du bekommst ab jetzt Signale aufs Handy.")
        print(f"Testnachricht an {server}/{thema} gesendet.")
        return

    titel, text = baue_nachricht(cfg, morgens="--morgens" in sys.argv)
    if not text:
        if cfg["benachrichtigung"].get("nur_neue"):
            print("Keine NEUEN Kauf-Kandidaten seit letztem Push – nichts gesendet.")
        else:
            print("Keine Treffer ueber Mindest-Score – nichts gesendet.")
        return
    status = sende_ntfy(server, thema, titel, text, click=DASHBOARD_URL)
    print(f"Push gesendet (HTTP {status}) an {server}/{thema}:\n{titel}\n{text}")

if __name__ == "__main__":
    main()
