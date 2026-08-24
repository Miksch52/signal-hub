#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plausibilitaetswaechter fuer die fuenf Rohsignal-Quellen (PDF/Mail/Finviz/
Markets-360/Trend-Screener).

Warum (Systempruefung 2026-08-23, siehe CLAUDE.md "Datenfusion Markets 360 +
Trend-Screener"): faellt eine Quelle aus - abgelaufenes IMAP-Passwort, kaputter
R2-Sync, ein leer gescrapter Finviz-Screener - wirft KEINES der fuenf
*_screener.py-Module einen Fehler. Jedes schreibt unconditional eine (dann
leere) Liste nach seiner RAW_*-Datei und beendet sich sauber mit Code 0
(siehe z.B. markets360_screener.py: "Fehlt die Datei ... wird sauber mit
0 Signalen beendet statt einen Fehler zu werfen"). run.py::lauf() sieht also
einen normalen, gruenen Lauf - der Ausfall ist unsichtbar, bis jemand zufaellig
nachschaut. Genau dieser Ausfallmodus stand im Bericht der Systempruefung:
"fuehrt zu leisem Datenverlust (0 Signale statt Fehler), nicht zu einem
Absturz".

Ansatz: rein mechanisch, keine Einzelfallentscheidung noetig. Fuer jede AKTIVE
Quelle wird taeglich (ein Eintrag pro Kalendertag, mehrere Laeufe am selben
Tag ueberschreiben ihn) die Trefferzahl aus der jeweiligen RAW_*-Datei in
einer kleinen Historie mitgeschrieben. Meldet eine Quelle heute 0 Treffer,
obwohl sie an mindestens MIN_TAGE_MIT_TREFFERN der letzten TAGE_HISTORIE Tage
etwas gefunden hat, ist das verdaechtig genug fuer einen Push - unabhaengig
davon, WARUM die Quelle gerade leer ist. Quellen, die schon vorher meist leer
waren (z.B. PDF an Tagen ohne neues Magazin), loesen dadurch bewusst KEINEN
Alarm aus: die Schwelle bezieht sich auf die eigene Historie der Quelle, nicht
auf einen festen Erwartungswert.

Aufruf: python3 src/quellen_watchdog.py
(in run.py::pipeline() direkt nach den fuenf *_screener.py-Laeufen, vor
scorer.py - unabhaengig von dessen Erfolg, denn scorer.py liest ohnehin
dieselben RAW_*-Dateien und wuerde denselben stillen Ausfall nur nochmal,
aber unauffaelliger, in signals.json durchreichen.)
"""

import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta

import pfade

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# Quelle -> (Rohdatei, config.quellen.<Schluessel>.aktiv oder None wenn immer
# aktiv). markets360/trendscreener haben in run.py::pipeline() keine
# aktiv-Pruefung (laufen immer, ueberspringen intern) - genau deshalb sind sie
# hier bewusst mit dabei statt ausgenommen.
QUELLEN = {
    "PDF":            (pfade.RAW_PDF, "pdf"),
    "Mail":           (pfade.RAW_MAIL, "email"),
    "Finviz":         (pfade.RAW_FINVIZ, "finviz"),
    "Markets 360":    (pfade.RAW_MARKETS360, None),
    "Trend-Screener": (pfade.RAW_TRENDSCREENER, None),
}

TAGE_HISTORIE = 14           # Rueckblickfenster fuer die Verlaesslichkeits-Pruefung
MIN_TAGE_MIT_TREFFERN = 5    # ab so vielen "hatte etwas gefunden"-Tagen im Fenster gilt eine Quelle als verlaesslich


def _lade_json(pfad, default):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _speichere_json(pfad, obj):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _zaehle_treffer(raw_pfad):
    daten = _lade_json(raw_pfad, None)
    if not isinstance(daten, list):
        return None   # Datei fehlt/kaputt != 0 Treffer - siehe main()
    return len(daten)


def _ist_aktiv(cfg_schluessel):
    if cfg_schluessel is None:
        return True
    try:
        cfg = json.load(open(pfade.CONFIG, encoding="utf-8"))
        return bool((cfg.get("quellen") or {}).get(cfg_schluessel, {}).get("aktiv"))
    except Exception:
        return True   # im Zweifel pruefen statt eine Quelle stumm auszulassen


def _ntfy_settings():
    try:
        with open(pfade.CONFIG, encoding="utf-8") as f:
            b = json.load(f).get("benachrichtigung", {})
        thema = (b.get("ntfy_thema") or "").strip()
        server = (b.get("ntfy_server") or "https://ntfy.sh").strip()
        if thema and "NOCH" not in thema.upper():
            return server, thema
    except Exception:
        pass
    return None, None


def _sende_ntfy(titel, text, tags="loudspeaker", prio="high"):
    server, thema = _ntfy_settings()
    if not thema:
        print("Quellen-Waechter: kein ntfy-Thema konfiguriert -> kein Push.")
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
        print(f"Quellen-Waechter: ntfy-Fehler: {e}")
        return False


def main():
    heute = datetime.now().strftime("%Y-%m-%d")
    historie = _lade_json(pfade.QUELLEN_HISTORIE, {})    # {quelle: {"JJJJ-MM-TT": anzahl}}
    state = _lade_json(pfade.QUELLEN_WATCHDOG_STATE, {})  # {"quelle_JJJJ-MM-TT": true}  (Anti-Spam: 1x/Tag/Quelle)

    verdaechtig = []
    for quelle, (raw_pfad, cfg_schluessel) in QUELLEN.items():
        if not _ist_aktiv(cfg_schluessel):
            continue

        anzahl = _zaehle_treffer(raw_pfad)
        if anzahl is None:
            # Datei fehlt/kaputt: kein "0 Treffer"-Fall (das melden die fuenf
            # Screener-Skripte bereits selbst ueber ihren Exit-Code an
            # run.py::lauf()) - hier nur echte, geschriebene Nullen zaehlen.
            continue

        h = historie.setdefault(quelle, {})
        h[heute] = anzahl
        for alter_tag in sorted(h)[:-TAGE_HISTORIE]:
            del h[alter_tag]

        if anzahl == 0:
            vergangene_tage = [v for tag, v in h.items() if tag != heute]
            tage_mit_treffern = sum(1 for v in vergangene_tage if v > 0)
            if tage_mit_treffern >= MIN_TAGE_MIT_TREFFERN:
                schluessel = f"{quelle}_{heute}"
                if not state.get(schluessel):
                    verdaechtig.append((quelle, tage_mit_treffern, len(vergangene_tage)))
                state[schluessel] = True

    _speichere_json(pfade.QUELLEN_HISTORIE, historie)
    # State nur auf dasselbe Rueckblickfenster begrenzen wie die Historie,
    # sonst waechst er unbegrenzt (ein Eintrag pro Quelle+Tag mit gemeldetem
    # Verdacht - typischerweise wenige pro Monat, aber ohne Deckel
    # theoretisch grenzenlos). Schluessel-Format ist "<Quelle>_JJJJ-MM-TT" -
    # rsplit("_", 1) trennt zuverlaessig ab, auch wenn eine Quelle selbst
    # einen Unterstrich im Namen haette.
    aeltestes_datum = (datetime.now() - timedelta(days=TAGE_HISTORIE)).strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if k.rsplit("_", 1)[-1] >= aeltestes_datum}
    _speichere_json(pfade.QUELLEN_WATCHDOG_STATE, state)

    if verdaechtig:
        zeilen = [f"⚠️ {q}: 0 Treffer heute, obwohl an {t}/{n} der letzten Tage etwas gefunden wurde"
                  for q, t, n in verdaechtig]
        _sende_ntfy(
            f"🔇 {len(verdaechtig)} Quelle(n) heute ohne Treffer",
            "\n".join(zeilen) + "\n\nMoeglicher stiller Ausfall (Zugangsdaten/API/Sync) statt "
            "echtem Nullergebnis - Signal-Hub/config.json und die jeweilige Quelle pruefen.",
        )
        print("Quellen-Waechter: " + "; ".join(f"{q} 0 Treffer trotz Historie" for q, _, _ in verdaechtig))
        return False

    stand = ", ".join(
        f"{q}={historie.get(q, {}).get(heute, '?')}"
        for q, (_, cfg_schluessel) in QUELLEN.items() if _ist_aktiv(cfg_schluessel)
    )
    print(f"Quellen-Waechter: alle aktiven Quellen plausibel ({stand}).")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
