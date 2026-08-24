#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forward-Test-Waechter: erkennt automatisch, wenn eine Backtest-Kohorte
belastbar wird oder sich eine Rangfolge dreht - statt darauf zu warten, dass
jemand monatlich von Hand nachschaut.

Warum (Systempruefung 2026-08-23, teuerster Einzelbefund): der Kommentar in
Price-Action-Hub/src/top_setups.py sagte selbst "Bei genug neuen Forward-
Daten erneut pruefen" - aber diese Anweisung existierte nur als Codekommentar,
nie als etwas, das tatsaechlich passiert ist. Die Stichprobe wuchs von n=83
auf n=1005 (zwoelffach), der Wert fiel von 71% auf 48,8%, und niemand hat es
gesehen, bis die Systempruefung von aussen draufschaute. Eine monatliche
Kalender-Erinnerung haette dasselbe Problem eine Ebene hoeher: sie haengt
wieder daran, dass ein Mensch sie ernst nimmt. Dieses Skript zieht den
Vergleich stattdessen selbst.

Deckt dieselben sechs Backtest-Engines ab, die die Systempruefung genannt hat
(pivot/score/regime aus Signal-Hub, muster/hebel aus Price-Action-Hub,
rotation aus Rotation-Dashboard - fuenf davon bilden auch backtest-
vergleich.html, regime kommt dazu, weil es im Bericht explizit als "null
realisierte Beobachtungen" auffiel).

Vier mechanische Ausloeser, keine Einzelfallentscheidung noetig:
  1. Eine Kohorte hat n=100 ueberschritten -> wird erstmals belastbar.
  2. Die Wilson-95%-Konfidenz-Untergrenze einer Kohorte hat 50% gekreuzt -
     in beide Richtungen (neu belegt ODER nicht mehr belegt).
  3. Bei Pivot: die Rangfolge ARMED vs. BREAKOUT (Win-Rate am selben
     Horizont) hat sich gedreht - genau der Fall aus dem Bericht.
  4. Eine Engine hat ueber einen ganzen Vergleichszeitraum (~1 Monat)
     hinweg weiterhin komplett 0 Beobachtungen - reift nicht.

Vergleicht gegen einen rollierenden Monats-Schnappschuss (siehe
SNAPSHOT_PFAD), der nur erneuert wird, wenn er aelter als
SNAPSHOT_MINDESTALTER_TAGE ist - dadurch vergleicht jeder Lauf effektiv
"heute gegen vor ~1 Monat", ohne eine eigene Cron-Schedule zu brauchen.

Aufruf: NTFY_THEMA=... NTFY_SERVER=... python3 forward_test_review.py \
        <pivot.json> <score.json> <regime.json> <muster.json> <hebel.json> \
        <rotation.json> <snapshot.json>
(ntfy-Ziel bewusst ueber Umgebungsvariablen statt einer eigenen config.json-
Datei - der deploy-Job hat, anders als der signal-hub-Job, keine geschriebene
config.json auf der Platte; gleiches Muster wie der Token-Waechter im
selben Job.)
(im deploy-Job von .github/workflows/pipeline.yml, nach dem R2-Upload der
frischen Ausgabedateien - braucht deren aktuellen Stand.)
"""

import json
import math
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

SNAPSHOT_MINDESTALTER_TAGE = 28   # rollierendes "~1 Monat"-Vergleichsfenster
REIFE_N_FUER_RANGFOLGE = 8         # Mindest-n je Seite fuer den ARMED/BREAKOUT-Vergleich (Punkt 3)


def _lade_json(pfad, default):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _speichere_json(pfad, obj):
    os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _wilson(win_pct, n, z=1.96):
    """95%-Konfidenzintervall einer Trefferquote nach Wilson (dieselbe Formel
    wie Price-Action-Hub/src/top_setups.py::_wilson - bewusst dupliziert statt
    importiert, andere Codebasis/anderer Job-Kontext)."""
    if not n:
        return None, None
    p = win_pct / 100.0
    nenner = 1 + z * z / n
    mitte = (p + z * z / (2 * n)) / nenner
    spanne = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / nenner
    return (round(max(0.0, mitte - spanne) * 100, 1),
            round(min(1.0, mitte + spanne) * 100, 1))


def _normalisiere(pfad, container_feld):
    """Liest eine Backtest-Ausgabedatei und liefert das gemeinsame Schema
    {kohorte: {horizont: {"n": int, "win": float|None}}} - unabhaengig davon,
    ob die Datei ihre Kohorten unter "forward_realisiert" (pivot/regime/
    muster/hebel/rotation) oder "ergebnis" (score, Tier A/B) fuehrt."""
    d = _lade_json(pfad, {})
    container = d.get(container_feld) or {}
    out = {}
    for kohorte, horizonte in container.items():
        if not isinstance(horizonte, dict):
            continue
        out[kohorte] = {}
        for horizont, werte in horizonte.items():
            if isinstance(werte, dict):
                out[kohorte][horizont] = {"n": werte.get("n") or 0, "win": werte.get("win")}
    return out


def _ntfy_settings():
    # Kommen als Umgebungsvariablen statt aus einer eigenen config.json-Datei
    # (der deploy-Job hat - anders als der signal-hub-Job - keine geschriebene
    # config.json auf der Platte; gleiches Muster wie der Token-Waechter im
    # selben Job, der SIGNALHUB_CONFIG_JSON per jq direkt aus dem Secret liest,
    # statt es erst als Datei anzulegen).
    thema = (os.environ.get("NTFY_THEMA") or "").strip()
    server = (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").strip()
    if thema and "NOCH" not in thema.upper():
        return server, thema
    return None, None


def _sende_ntfy(titel, text, tags="chart_with_upwards_trend", prio="default"):
    server, thema = _ntfy_settings()
    if not thema:
        print("Forward-Test-Waechter: kein ntfy-Thema konfiguriert -> kein Push.")
        return False
    url = f"{server.rstrip('/')}/{thema}"
    req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST")
    req.add_header("Title", titel.encode("utf-8"))
    req.add_header("Tags", tags)
    req.add_header("Priority", prio)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"Forward-Test-Waechter: ntfy-Fehler: {e}")
        return False


def _n_summe(kohorten):
    return sum((z.get("n") or 0) for h in kohorten.values() for z in h.values())


def vergleiche(engine, aktuell, basis):
    """Liefert eine Liste von Meldungstexten fuer eine einzelne Engine -
    Bedingungen 1/2/4 gelten fuer alle Engines gleich, Bedingung 3 (Rangfolge)
    ist pivot-spezifisch und wird separat in main() behandelt."""
    meldungen = []

    if basis is not None and _n_summe(aktuell) == 0 and _n_summe(basis) == 0:
        meldungen.append(f"⏳ {engine}: seit dem letzten Vergleich (~1 Monat) weiterhin 0 Beobachtungen — reift nicht.")

    for kohorte, horizonte in aktuell.items():
        basis_horizonte = (basis or {}).get(kohorte, {})
        for horizont, akt in horizonte.items():
            n_akt, win_akt = akt.get("n") or 0, akt.get("win")
            bas = basis_horizonte.get(horizont, {})
            n_bas, win_bas = bas.get("n") or 0, bas.get("win")

            # 1) n hat 100 ueberschritten
            if n_bas < 100 <= n_akt:
                meldungen.append(f"📈 {engine}/{kohorte}/{horizont}: n={n_akt} (≥100) — erstmals belastbare Stichprobe.")

            # 2) Wilson-Untergrenze hat 50% gekreuzt (nur wenn beide Seiten
            # ueberhaupt eine Win-Rate haben, sonst kein sinnvoller Vergleich)
            ci_low_akt = _wilson(win_akt, n_akt)[0] if win_akt is not None and n_akt > 0 else None
            ci_low_bas = _wilson(win_bas, n_bas)[0] if win_bas is not None and n_bas > 0 else None
            if ci_low_akt is not None and ci_low_bas is not None and (ci_low_bas <= 50) != (ci_low_akt <= 50):
                richtung = "über" if ci_low_akt > 50 else "unter"
                meldungen.append(
                    f"🔀 {engine}/{kohorte}/{horizont}: Konfidenz-Untergrenze jetzt {richtung} 50 % "
                    f"({ci_low_bas:.1f} % → {ci_low_akt:.1f} %, n={n_akt}) — Beleglage hat sich gedreht."
                )

    return meldungen


def rangfolge_pivot(aktuell_pivot, basis_pivot):
    """Bedingung 3: Win-Rate-Rangfolge ARMED vs. BREAKOUT am selben Horizont
    gedreht - der konkrete Fall, an dem dieses Skript aufgehaengt ist
    (71 % bei n=83 -> 48,8 % bei n=1005, ARMED blieb zufaellig vorn, haette
    aber genauso gut kippen koennen)."""
    meldungen = []
    for horizont in ("4W", "8W", "12W"):
        a_akt = aktuell_pivot.get("ARMED", {}).get(horizont, {})
        b_akt = aktuell_pivot.get("BREAKOUT", {}).get(horizont, {})
        a_bas = (basis_pivot or {}).get("ARMED", {}).get(horizont, {})
        b_bas = (basis_pivot or {}).get("BREAKOUT", {}).get(horizont, {})
        seiten = (a_akt, b_akt, a_bas, b_bas)
        if not all(s.get("win") is not None and (s.get("n") or 0) >= REIFE_N_FUER_RANGFOLGE for s in seiten):
            continue
        rang_akt = a_akt["win"] > b_akt["win"]
        rang_bas = a_bas["win"] > b_bas["win"]
        if rang_akt != rang_bas:
            vorn = "ARMED" if rang_akt else "BREAKOUT"
            meldungen.append(
                f"🔄 pivot/{horizont}: Rangfolge gedreht — jetzt {vorn} vorn "
                f"(ARMED {a_akt['win']} % · n={a_akt['n']} vs. BREAKOUT {b_akt['win']} % · n={b_akt['n']}, "
                f"vor ~1 Monat noch umgekehrt)."
            )
    return meldungen


def main():
    if len(sys.argv) != 8:
        print("Aufruf: forward_test_review.py <pivot> <score> <regime> <muster> <hebel> "
              "<rotation> <snapshot>  (ntfy-Ziel ueber NTFY_THEMA/NTFY_SERVER-Umgebungsvariablen)")
        return 1
    pivot_pfad, score_pfad, regime_pfad, muster_pfad, hebel_pfad, rotation_pfad, snapshot_pfad = sys.argv[1:]

    aktuell = {
        "pivot":    _normalisiere(pivot_pfad, "forward_realisiert"),
        "score":    _normalisiere(score_pfad, "ergebnis"),
        "regime":   _normalisiere(regime_pfad, "forward_realisiert"),
        "muster":   _normalisiere(muster_pfad, "forward_realisiert"),
        "hebel":    _normalisiere(hebel_pfad, "forward_realisiert"),
        "rotation": _normalisiere(rotation_pfad, "forward_realisiert"),
    }

    snapshot = _lade_json(snapshot_pfad, None)
    basis = (snapshot or {}).get("engines")
    stand = (snapshot or {}).get("stand")
    alt_genug = stand is None or (
        datetime.now() - datetime.strptime(stand, "%Y-%m-%d")
    ) >= timedelta(days=SNAPSHOT_MINDESTALTER_TAGE)

    # Vergleich und Schnappschuss-Erneuerung sind EIN atomares monatliches
    # Ereignis, kein Vergleich bei jedem Pipeline-Lauf: der Vormonats-Stand
    # bleibt sonst bis zu 28 Tage lang unveraendert die Basis, wuerde dieselbe
    # Meldung also bei JEDEM der 4x/Tag-Laeufe erneut pushen (echter Push-Spam,
    # anders als bei den anderen Waechtern, deren State-Datei genau das schon
    # separat verhindert). Also: an den meisten Tagen passiert hier nichts,
    # nur wenn der Schnappschuss faellig ist, wird verglichen UND erneuert.
    if not alt_genug:
        faellig_ab = (datetime.strptime(stand, "%Y-%m-%d") + timedelta(days=SNAPSHOT_MINDESTALTER_TAGE)).strftime("%Y-%m-%d")
        print(f"Forward-Test-Waechter: Schnappschuss vom {stand} noch aktuell genug "
              f"(naechster Vergleich ab {faellig_ab}) — heute kein Vergleich, kein Push.")
        return 0

    meldungen = []
    if basis is not None:   # kein Vergleich am allerersten Lauf (keine Basis)
        for engine, kohorten in aktuell.items():
            meldungen.extend(vergleiche(engine, kohorten, basis.get(engine)))
        meldungen.extend(rangfolge_pivot(aktuell["pivot"], basis.get("pivot")))

    _speichere_json(snapshot_pfad, {"stand": datetime.now().strftime("%Y-%m-%d"), "engines": aktuell})
    print(f"Forward-Test-Waechter: Schnappschuss erneuert (Basis war {stand or 'nie'}).")

    if meldungen:
        _sende_ntfy(
            f"📐 Forward-Test: {len(meldungen)} Veränderung(en) seit ~1 Monat",
            "\n".join(meldungen),
        )
        print("Forward-Test-Waechter:\n  " + "\n  ".join(meldungen))
    else:
        print("Forward-Test-Waechter: keine Veraenderung gegenueber dem letzten Schnappschuss.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
