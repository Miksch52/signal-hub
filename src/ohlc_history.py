#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Taeglicher Schlusskurs-Snapshot fuer das gescreente Universum (seit 2026-08-20,
Datierung seit 2026-09-11 grundlegend korrigiert).

Hintergrund (Top-Setups-Roadmap Punkt 4): Alle fuenf Screening-Engines
cachen Kursdaten bisher nur mit Tages-TTL auf dem jeweiligen Runner/Mac -
auf dem Cloud-Runner (GitHub Actions, pro Lauf frisch) heisst das: der
Yahoo-Cache faengt bei jedem der vier taeglichen Signal-Hub-Laeufe leer an
und wird am Lauf-Ende komplett verworfen. Es gibt bisher KEINE tatsaechlich
wachsende Kurshistorie im System - dieses Modul ist der erste Baustein
dafuer.

Kein zusaetzlicher Yahoo-Abruf: scorer.py hat pro Treffer bereits ein
126-Tage-Chartfenster in signals.json eingebettet (treffer[].chart.c/.v,
siehe scorer.py::_chartdaten) - hier wird nur der jeweils LETZTE Eintrag
je Ticker herausgezogen. Klein pro Tag (~15 KB fuer das gesamte Universum),
waechst kontrolliert - Grundlage fuer einen spaeteren Punkt-in-Zeit-Backtest
ohne den im RETRO-Modus von pivot_backtest.py dokumentierten Universums-Bias.

Datierung seit 2026-09-11 (Befund B10 der Systempruefung vom 2026-09-10):
Bis dahin bekam jede Datei das Datum des LAUFS (date.today()) und wurde von
jedem der vier taeglichen Laeufe ueberschrieben. Ein Abgleich gegen Yahoos
finale Schlusskurse zeigte drei Fehler: Laeufe zwischen 18:30 und 22:00 Uhr
schrieben fuer US-Titel Intraday-Kurse als "close" (4-28 % Treffer), Laeufe
nach Mitternacht legten den Vortagesschluss unter dem Folgedatum ab, und es
entstanden Dateien fuer Samstage und Sonntage. Jetzt gilt:
  - Jede Zeile bekommt das echte Datum ihres letzten Bars (chart.d, von
    scorer.py aus Yahoos Zeitstempeln durchgereicht) - nicht das Laufdatum.
  - Ein Bar, der beim ABRUF (chart.abg) noch zum laufenden Handelstag
    gehoerte, wird nie geschrieben: US bis 17:00 New York, Europa bis 18:00
    Berlin, deutsche Regionalboersen bis 22:30 Berlin - jeweils Boersenschluss
    plus Puffer fuer Nachmeldungen (ein Abruf um 22:02 traf nachweislich nur
    23-28 % der finalen US-Schlusskurse). Massgeblich ist die Abrufzeit, nicht
    die Laufzeit: der Tages-Cache kann einen Intraday-Bar in einen spaeteren
    Lauf weitertragen.
  - Nur anhaengen, nie ueberschreiben: gibt es fuer (Tag, Titel) schon eine
    Zeile, bleibt sie unveraendert - die erste als abgeschlossen belegte
    Aufnahme gilt.
Eine Tagesdatei enthaelt damit ausschliesslich Schlusskurse genau dieses
Handelstags; US und Europa koennen sich an Feiertagen unterscheiden (Labor Day
2026-09-07: nur Europa).

Aufruf: python3 src/ohlc_history.py (nach scorer.py, siehe run.py::pipeline).
Schreibt Signal-Hub/data/ohlc-history/JJJJ-MM-TT.csv und listet die in diesem
Lauf geaenderten Tage in data/ohlc-history/.geaendert (fuer den lokalen
Mac-mini-Upload, scripts/upload_ohlc_to_r2.sh). Die Cloud-Pipeline holt vorher
die juengsten Tage aus R2 zurueck, damit "anhaengen" auch auf dem frischen
Runner gegen den echten Bestand laeuft (pipeline.yml).
"""

import csv
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

import pfade

SIGNALS = pfade.SIGNALS_JSON
OUT_DIR = pfade.OHLC_HISTORY_DIR
GEAENDERT = ".geaendert"

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
# Deutsche Regionalboersen handeln bis 20:00 (Frankfurt) bzw. 22:00 Uhr
SPAETE_REGIONALBOERSEN = {"F", "HM", "DU", "MU", "SG", "BE", "HA"}


def handelsschluss_mit_puffer(ticker, markt, tag):
    if markt == "USA":
        return dt.datetime.combine(tag, dt.time(17, 0), tzinfo=NEW_YORK)
    suffix = ticker.rsplit(".", 1)[1] if "." in ticker else ""
    if suffix in SPAETE_REGIONALBOERSEN:
        return dt.datetime.combine(tag, dt.time(22, 30), tzinfo=BERLIN)
    return dt.datetime.combine(tag, dt.time(18, 0), tzinfo=BERLIN)


def _abrufzeit(chart, ersatz):
    abg = chart.get("abg")
    if not abg:
        return ersatz
    try:
        return dt.datetime.strptime(abg, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return ersatz


def zeilen_nach_handelstag(signals, jetzt):
    """-> ({'JJJJ-MM-TT': [(ticker, close, volume), ...]}, zaehler)"""
    tage, zaehler = {}, {"ohne_kurs": 0, "ohne_bar_datum": 0, "laufender_handelstag": 0}
    for t in signals.get("treffer") or []:
        chart = t.get("chart") or {}
        closes = chart.get("c") or []
        ticker = t.get("ticker")
        if not closes or not ticker:
            zaehler["ohne_kurs"] += 1
            continue
        if not chart.get("d"):  # signals.json von vor 2026-09-11 - Tag nicht belegbar
            zaehler["ohne_bar_datum"] += 1
            continue
        try:
            tag = dt.date.fromisoformat(chart["d"])
        except ValueError:
            zaehler["ohne_bar_datum"] += 1
            continue
        markt = t.get("markt") or ("Europa" if "." in ticker else "USA")
        if _abrufzeit(chart, jetzt) < handelsschluss_mit_puffer(ticker, markt, tag):
            zaehler["laufender_handelstag"] += 1
            continue
        vols = chart.get("v") or []
        tage.setdefault(chart["d"], []).append(
            (ticker, closes[-1], (vols[-1] * 1000) if vols else ""))
    return tage, zaehler


def schreibe(signals_pfad=None, out_dir=None, jetzt=None):
    signals_pfad = signals_pfad or SIGNALS
    out_dir = out_dir or OUT_DIR
    jetzt = jetzt or dt.datetime.now(tz=BERLIN)
    try:
        with open(signals_pfad, encoding="utf-8") as f:
            signals = json.load(f)
    except Exception as e:
        print(f"OHLC-Historie: signals.json nicht lesbar ({e}) - uebersprungen.")
        return False

    tage, zaehler = zeilen_nach_handelstag(signals, jetzt)
    os.makedirs(out_dir, exist_ok=True)
    geaendert, neu_gesamt = [], 0
    for tag, zeilen in sorted(tage.items()):
        pfad = os.path.join(out_dir, f"{tag}.csv")
        vorhanden = set()
        if os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as f:
                vorhanden = {r["ticker"] for r in csv.DictReader(f)}
        frisch = [z for z in zeilen if z[0] not in vorhanden]
        if not frisch:
            continue
        kopfzeile = not os.path.exists(pfad)
        with open(pfad, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if kopfzeile:
                w.writerow(["ticker", "close", "volume"])
            w.writerows(frisch)
        geaendert.append(f"{tag}.csv")
        neu_gesamt += len(frisch)

    with open(os.path.join(out_dir, GEAENDERT), "w", encoding="utf-8") as f:
        f.write("\n".join(geaendert))
    print(f"OHLC-Historie: {neu_gesamt} neue Schlusskurse in {len(geaendert)} Tagesdatei(en) "
          f"{geaendert or ''} - ausgelassen: {zaehler['laufender_handelstag']} laufender Handelstag, "
          f"{zaehler['ohne_bar_datum']} ohne Bar-Datum, {zaehler['ohne_kurs']} ohne Kurs.")
    return True


if __name__ == "__main__":
    schreibe()
