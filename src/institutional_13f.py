#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Institutioneller Besitz aus SEC-Form-13F - Quartalstrend nach Minervini.

Schliesst die letzte offene Luecke aus dem SEPA-Abgleich (2026-08-30):
Minervini verlangt institutionellen Besitz, der UEBER QUARTALE STEIGT.
scorer.py::f_institutional liefert per Yahoo nur den aktuellen Stand ohne
Historie - die Richtung war damit nicht messbar.

Warum SEC und nicht Finnhub/Business Quant fuer den automatischen Screener-
Lauf (am 2026-08-30 beide getestet):
  * Finnhub: stock/ownership und stock/fund-ownership antworten mit 403
    ("You don't have access"), stock/institutional-ownership liefert die
    Upgrade-Seite. Im vorhandenen Tarif nicht enthalten.
  * Business Quant: data.businessquant.com/13f antwortete beim ERSTEN Test
    ohne registrierten Key mit 401 "API key is missing" - das wurde hier
    faelschlich als "kostenpflichtiges Abo" eingestuft. Mit echtem, kostenlosem
    Free-Plan-Key (registriert 2026-08-30) liefern 13f/form4/estimates alle
    drei anstandslos (live gegen AAPL verifiziert) - der Free-Plan deckt sogar
    Insider-Transaktionen (Form 4) und Analysten-Schaetzungen mit ab, die SEC
    EDGAR so nicht liefert. Limitiert ist er nur auf 30 Requests/Tag insgesamt
    (data.businessquant.com/pricing) - fuer einen automatischen Lauf ueber
    hunderte Ticker ungeeignet, fuer einen manuellen Einzelticker-Abruf aber
    genau richtig: siehe den "🏦 Institutionelle Daten, Insider & Analysten"-
    Button in Signal-Hub/setup-detail.html (Worker-Route /businessquant in
    cloudflare-worker/mts-cors-proxy.js). Loest trotzdem NICHT die Europa-
    Luecke fuer den automatischen Lauf, denn 13F ist 13F, egal wer es verpackt.
  * SEC: kostenlos, kein Tageslimit, autoritativ, deckt 446 der 548 damaligen
    Kandidaten ab (81 %). Die fehlenden sind fast ausschliesslich europaeische
    Werte - 13F-meldepflichtig sind nur US-Institutionelle ab 100 Mio. USD.

DER ENTSCHEIDENDE PUNKT - NORMIERUNG:
Die ROHE Summe der gemeldeten Aktien ist als Trendsignal WERTLOS. Gemessen
ueber Q3/25 -> Q4/25 -> Q1/26 zeigten sechs voellig unabhaengige Werte
(AAPL/MSFT/NVDA/TSLA/EBAY/CSGP) exakt dasselbe Muster: rund -3 %, dann
+15..+25 %. Ursache ist nicht Akkumulation, sondern die Zahl der Melder
selbst (3,27 -> 3,47 -> 3,82 Mio. Meldezeilen). Roh gerechnet meldet die
Kennzahl fuer JEDE Aktie "steigend".
Deshalb wird je Quartalsuebergang der QUERMARKT-MEDIAN abgezogen. Erst
danach bleibt Streuung uebrig, die etwas trennt (gemessen: 10 %-Quantil
-11,4 %, Median 0, 90 %-Quantil +16,0 %).

ZWEITER PUNKT - NIVEAU vs. RICHTUNG:
Die absolute Hoehe ist unbrauchbar (13F zaehlt doppelt: Mutter- und
Tochtergesellschaften melden dieselben Positionen, verliehene Aktien
ebenfalls - AAPL kam auf 84 % statt der real ~66 %). Dieses Modul liefert
deshalb AUSSCHLIESSLICH die Richtung, kein Niveau. Fuer das Niveau bleibt
Yahoos heldPercentInstitutions zustaendig (scorer.py::f_institutional),
das seinerseits als reine Kontextzahl gefuehrt wird.

DRITTER PUNKT - BREITE ("UNDEROWNED", seit 2026-09-15):
Minervinis Besitz-Kriterium hat neben der Richtung eine zweite Haelfte, die
bis dahin nirgends gemessen wurde: er will Werte, die noch NICHT bei jeder
Adresse im Depot liegen ("underowned"), bei denen aber gerade neue Adressen
einsteigen. Die dafuer belastbare Zahl aus 13F ist NICHT der Anteil, sondern
die ZAHL DER MELDER je Titel (distinct ACCESSION_NUMBER in der INFOTABLE):
  * Sie ist ein Stueckzahl-freies Abzaehlen und damit von der
    Doppelzaehlung (Konzernstrukturen, Wertpapierleihe) weit weniger
    betroffen als die Aktiensumme - ein Melder bleibt ein Melder.
  * Sie trennt zwei Faelle, die die Aktiensumme vermischt: bestehende Halter
    kaufen nach (Summe steigt, Melderzahl flach) gegen NEUE Adressen steigen
    ein (Melderzahl steigt) - letzteres ist das, was Minervini sucht.
Auch hier gilt die Normierung von oben: die Zahl der Meldezeilen am Markt
waechst quartalsweise mit, deshalb wird je Uebergang ebenfalls der
Quermarkt-Median abgezogen (melder_delta_pct).
Fuer das NIVEAU wird bewusst KEINE absolute Grenze erfunden ("unter X
Meldern = underowned") - dafuer fehlt die Grundlage, genau wie bei den
30-70 % in scorer.py::f_institutional. Stattdessen liefert die Tabelle das
PERZENTIL der Melderzahl im 13F-Universum (melder_pctl, 100 = am breitesten
gehalten). Eine Einordnung ist damit immer eine Aussage "im Vergleich zu",
nie ein erfundener Schwellenwert.

Laeuft NICHT im taeglichen Zyklus: 13F-Daten aendern sich quartalsweise.
Ein eigener Workflow (.github/workflows/institutional-13f.yml) baut die
Tabelle viermal im Jahr; der Signal-Hub liest nur die fertige, kleine JSON.

Aufruf:
    python3 src/institutional_13f.py --bauen            # Quartalstabelle neu bauen
    python3 src/institutional_13f.py --bauen --quartale 2   # nur 2 Quartale (Test)
    python3 src/institutional_13f.py --zeigen AAPL MSFT # Eintraege ansehen
"""

import argparse
import bisect
import collections
import csv
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import pfade

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

# SEC verlangt einen aussagekraeftigen User-Agent mit Kontakt, sonst 403.
UA = {"User-Agent": "Maick Trading System (mschwillo@freenet.de)"}

SEC_13F_INDEX = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
SEC_FTD_INDEX = "https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data"

QUARTALE_STANDARD = 4       # so viele Quartale zurueck -> 3 Uebergaenge
MIN_AKTIEN_REFERENZ = 50_000_000   # nur breit gehaltene Titel bilden den Median
STEIGEND_MIN_PCT = 1.0      # normierte Veraenderung ab hier gilt als "steigend"
# Referenzgrenze fuer den Quermarkt-Median der MELDERZAHL - dasselbe Motiv wie
# MIN_AKTIEN_REFERENZ, nur in der anderen Einheit: ein Titel mit drei Meldern
# springt bei einem einzigen Neuzugang um +33 % und wuerde den Median sonst
# verzerren.
MIN_MELDER_REFERENZ = 50
# Schwelle fuer "neue Adressen steigen ein". Bewusst derselbe Wert wie
# STEIGEND_MIN_PCT (nach der Normierung sind beide Reihen Prozentwerte um
# Null), aber als eigene Konstante, damit sie sich getrennt nachziehen
# laesst, sobald die Forward-Kohorte dazu reif ist (siehe pivot_backtest.py).
MELDER_STEIGEND_MIN_PCT = 1.0
# Spaltenname der Melder-Kennung in der INFOTABLE. Mehrere Kandidaten, weil
# die Spalte hier NICHT live gegen SEC verifiziert werden konnte (der Build-
# Runner hat Netzzugang, die Entwicklungsumgebung nicht) - findet sich keiner
# davon, bleiben die Melderzahlen leer und alles Bisherige laeuft unveraendert
# weiter. Lieber ein fehlendes Feld als ein abgebrochener Quartalslauf.
ACCESSION_SPALTEN = ("ACCESSION_NUMBER", "ACCESSIONNUMBER", "ACCESSION_NO")


def _hole(url, versuche=3):
    """Bytes einer URL, mit Wiederholung. SEC drosselt und liefert dabei
    schon einmal ein abgeschnittenes Archiv (beim Test aufgetreten) - der
    Aufrufer prueft deshalb zusaetzlich die Entpackbarkeit."""
    letzter = None
    for i in range(versuche):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180, context=SSL_CTX) as r:
                return r.read()
        except Exception as ex:
            letzter = ex
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"Abruf fehlgeschlagen: {url} ({letzter})")


def ticker_zu_cusip():
    """{TICKER: CUSIP} aus der SEC-"Fails to Deliver"-Datei.

    Das ist die einzige kostenlose, autoritative Ticker->CUSIP-Bruecke, die
    SEC selbst veroeffentlicht (13F kennt nur CUSIP, nicht das Symbol).
    Gemessen 2026-08-30: rund 13.500 Zuordnungen, deckt auch Small Caps ab,
    die in Index-basierten Quellen fehlen."""
    seite = _hole(SEC_FTD_INDEX).decode("utf-8", "replace")
    treffer = re.findall(r'href="([^"]*cnsfails[^"]*\.zip)"', seite)
    if not treffer:
        raise RuntimeError("Keine FTD-Datei auf der SEC-Seite gefunden")
    url = "https://www.sec.gov" + treffer[0]
    roh = _hole(url)
    out = {}
    with zipfile.ZipFile(io.BytesIO(roh)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".txt")), None)
        if not name:
            raise RuntimeError("FTD-Archiv enthaelt keine .txt")
        with z.open(name) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            for row in csv.DictReader(text, delimiter="|"):
                c = (row.get("CUSIP") or "").strip().upper()
                s = (row.get("SYMBOL") or "").strip().upper()
                if c and s:
                    out.setdefault(s, c)
    return out


def verfuegbare_quartale(anzahl):
    """Die letzten `anzahl` 13F-Datensatz-URLs, aelteste zuerst."""
    seite = _hole(SEC_13F_INDEX).decode("utf-8", "replace")
    pfade_ = re.findall(r'href="([^"]*form-13f-data-sets/[^"]*_form13f\.zip)"', seite)
    # Die Seite listet neueste zuerst; wir wollen chronologisch aufsteigend.
    gewaehlt = list(dict.fromkeys(pfade_))[:anzahl]
    return [("https://www.sec.gov" + p, _label(p)) for p in reversed(gewaehlt)]


def _label(pfad):
    """'01mar2026-31may2026_form13f.zip' -> '01mar2026-31may2026'"""
    return os.path.basename(pfad).replace("_form13f.zip", "")


def aggregiere_quartal(url):
    """({CUSIP: Aktien}, {CUSIP: Melder}) eines Quartals aus der INFOTABLE.

    Bewusst nur echte Aktienpositionen: PUTCALL-Zeilen (Optionen) und
    SSHPRNAMTTYPE != 'SH' (z.B. Anleihe-Nennwerte) fliessen NICHT ein -
    sonst mischt man Stueckzahlen mit Nominalbetraegen.

    MELDER = Zahl der verschiedenen Meldungen (ACCESSION_NUMBER), die den
    Titel als Aktienposition fuehren - die Basis der Breiten-/underowned-
    Messung, siehe Modul-Docstring. Zwei bekannte Ungenauigkeiten, bewusst in
    Kauf genommen und deshalb hier benannt statt kaschiert:
      * Nachtraegliche Berichtigungen (13F-HR/A) sind eigene Meldungen und
        zaehlen doppelt. Das trifft alle Titel gleichmaessig und faellt durch
        die Quermarkt-Normierung des Trends heraus; das Niveau ist ohnehin
        nur als Perzentil gemeint, nicht als Halterzahl.
      * Ein Konzern mit mehreren meldenden Toechtern zaehlt mehrfach - wie
        bei der Aktiensumme, nur deutlich schwaecher (eine Meldung je Tochter
        statt der vollen Stueckzahl je Tochter).
    Speicherbewusst: ueber 3,8 Mio. Zeilen je Quartal wuerde ein Set echter
    (CUSIP, ACCESSION)-Paare mehrere hundert MB belegen. Gespeichert werden
    deshalb nur deren Hashes - fuer ein Abzaehlen genuegt das (eine Kollision
    verliert einen einzigen Melder von zehntausenden)."""
    roh = _hole(url)
    try:
        z = zipfile.ZipFile(io.BytesIO(roh))
    except zipfile.BadZipFile:
        raise RuntimeError(f"Archiv unvollstaendig geladen: {url}")
    # Der Dateiname im Archiv ist NICHT stabil: neuere Quartale legen die
    # TSVs flach ab, aeltere (geprueft: 01jun2025-31aug2025) in einen
    # Unterordner "<QUARTAL>_form13f/". Deshalb am Namensende suchen statt
    # "INFOTABLE.tsv" fest anzunehmen.
    name = next((n for n in z.namelist()
                 if n.upper().endswith("INFOTABLE.TSV")), None)
    if not name:
        raise RuntimeError(f"Keine INFOTABLE im Archiv: {url} ({z.namelist()[:5]})")
    summe = collections.Counter()
    melder = collections.Counter()
    gesehen = set()
    with z.open(name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        leser = csv.DictReader(text, delimiter="\t")
        acc_spalte = next((sp for sp in ACCESSION_SPALTEN
                           if sp in (leser.fieldnames or [])), None)
        if not acc_spalte:
            print(f"    Hinweis: keine Melder-Spalte gefunden "
                  f"({', '.join(ACCESSION_SPALTEN)}) - Breiten-Messung "
                  f"entfaellt fuer dieses Quartal")
        for row in leser:
            if (row.get("PUTCALL") or "").strip():
                continue
            if (row.get("SSHPRNAMTTYPE") or "").strip().upper() != "SH":
                continue
            c = (row.get("CUSIP") or "").strip().upper()
            if not c:
                continue
            if acc_spalte:
                acc = (row.get(acc_spalte) or "").strip()
                if acc:
                    h = hash((c, acc))
                    if h not in gesehen:
                        gesehen.add(h)
                        melder[c] += 1
            try:
                summe[c] += int(float(row.get("SSHPRNAMT") or 0))
            except ValueError:
                continue
    return summe, melder


def normierte_veraenderung(vorher, nachher, min_referenz=MIN_AKTIEN_REFERENZ):
    """(dict je CUSIP, Median) der um den Quermarkt bereinigten Veraenderung.

    Ohne diesen Schritt ist die Kennzahl unbrauchbar - siehe Modul-Docstring.
    Der Median wird nur ueber breit gehaltene Titel gebildet
    (min_referenz), damit Kleinstpositionen ihn nicht verzerren. Dieselbe
    Rechnung dient zwei Reihen: Aktiensumme (min_referenz in Stueck) und
    Melderzahl (min_referenz in Meldungen, MIN_MELDER_REFERENZ)."""
    gemeinsam = [c for c in nachher
                 if c in vorher and vorher[c] >= min_referenz and nachher[c] > 0]
    if len(gemeinsam) < 100:
        return {}, None
    roh = {c: (nachher[c] / vorher[c] - 1) * 100 for c in gemeinsam}
    werte = sorted(roh.values())
    median = werte[len(werte) // 2]
    # Auch Titel unterhalb der Referenzgrenze bekommen einen Wert - sie
    # bilden den Median nur nicht mit.
    alle = {c: (nachher[c] / vorher[c] - 1) * 100 - median
            for c in nachher if c in vorher and vorher[c] > 0}
    return alle, median


def bauen(anzahl_quartale=QUARTALE_STANDARD, ziel=None):
    ziel = ziel or pfade.INSTITUTIONAL_13F
    print(f"Ticker->CUSIP von SEC holen …")
    t2c = ticker_zu_cusip()
    print(f"  {len(t2c):,} Zuordnungen")

    quartale = verfuegbare_quartale(anzahl_quartale)
    print(f"13F-Datensaetze: {', '.join(l for _, l in quartale)}")
    aggregate, melder_q, labels = [], [], []
    for url, label in quartale:
        print(f"  {label} …", flush=True)
        stueck, melder = aggregiere_quartal(url)
        print(f"    {len(stueck):,} CUSIPs, {sum(stueck.values()):,} Aktien, "
              f"{sum(melder.values()):,} Melder-Positionen")
        aggregate.append(stueck)
        melder_q.append(melder)
        labels.append(label)

    if len(aggregate) < 2:
        raise RuntimeError("Mindestens zwei Quartale noetig fuer eine Richtung")

    uebergaenge, mediane = [], []
    for i in range(1, len(aggregate)):
        norm, med = normierte_veraenderung(aggregate[i - 1], aggregate[i])
        uebergaenge.append(norm)
        mediane.append(med)
        print(f"  Uebergang {labels[i-1]} -> {labels[i]}: "
              f"Quermarkt-Median {med:+.1f}% (herausgerechnet)")

    # Zweite, unabhaengige Reihe: die Zahl der MELDER je Titel (Breite/
    # "underowned", siehe Modul-Docstring). Gleiche Normierung, andere
    # Referenzgrenze. Leer, wenn die INFOTABLE keine Melder-Spalte hatte -
    # dann bleibt alles beim Stand vor 2026-09-15.
    melder_uebergaenge, melder_mediane = [], []
    hat_melder = any(melder_q)
    if hat_melder:
        for i in range(1, len(melder_q)):
            norm, med = normierte_veraenderung(melder_q[i - 1], melder_q[i],
                                               MIN_MELDER_REFERENZ)
            melder_uebergaenge.append(norm)
            melder_mediane.append(med)
            if med is not None:
                print(f"  Melder {labels[i-1]} -> {labels[i]}: "
                      f"Quermarkt-Median {med:+.1f}% (herausgerechnet)")

    c2t = {}
    for t, c in t2c.items():
        c2t.setdefault(c, t)

    # Niveau der Breite als PERZENTIL im 13F-Universum (kein erfundener
    # Schwellenwert, siehe Modul-Docstring). Referenz ist das jeweils
    # juengste Quartal; gezaehlt werden alle CUSIPs mit mindestens einem
    # Melder, nicht nur die spaeter zugeordneten Ticker - sonst haengt das
    # Perzentil an der Qualitaet der Ticker-Bruecke.
    melder_letzte = melder_q[-1] if hat_melder else collections.Counter()
    melder_sortiert = sorted(melder_letzte.values())

    def _pctl(anzahl):
        if not melder_sortiert:
            return None
        return round(bisect.bisect_left(melder_sortiert, anzahl)
                     / len(melder_sortiert) * 100)

    werte = {}
    for ticker, cusip in t2c.items():
        reihe = [round(u[cusip], 1) if cusip in u else None for u in uebergaenge]
        if all(x is None for x in reihe):
            continue
        folge = 0
        for x in reversed(reihe):
            if x is not None and x >= STEIGEND_MIN_PCT:
                folge += 1
            else:
                break
        eintrag = {"norm_pct": reihe, "steigend_folge": folge}
        if hat_melder:
            m_reihe = [int(q[cusip]) if cusip in q else None for q in melder_q]
            m_delta = [round(u[cusip], 1) if cusip in u else None
                       for u in melder_uebergaenge]
            m_folge = 0
            for x in reversed(m_delta):
                if x is not None and x >= MELDER_STEIGEND_MIN_PCT:
                    m_folge += 1
                else:
                    break
            eintrag.update({
                "melder": m_reihe,
                "melder_delta_pct": m_delta,
                "melder_folge": m_folge,
                "melder_pctl": _pctl(melder_letzte[cusip]) if cusip in melder_letzte else None,
            })
        werte[ticker] = eintrag

    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quelle": "SEC Form 13F (data-research/form-13f-data-sets)",
        "quartale": labels,
        "uebergaenge": [f"{labels[i-1]}->{labels[i]}" for i in range(1, len(labels))],
        "quermarkt_median_pct": [round(m, 2) if m is not None else None for m in mediane],
        "steigend_min_pct": STEIGEND_MIN_PCT,
        "melder_vorhanden": hat_melder,
        "melder_steigend_min_pct": MELDER_STEIGEND_MIN_PCT,
        "melder_quermarkt_median_pct": [round(m, 2) if m is not None else None
                                        for m in melder_mediane],
        "melder_universum": {
            "n": len(melder_sortiert),
            "median": melder_sortiert[len(melder_sortiert) // 2] if melder_sortiert else None,
            "q10": melder_sortiert[len(melder_sortiert) // 10] if melder_sortiert else None,
            "q90": melder_sortiert[len(melder_sortiert) * 9 // 10] if melder_sortiert else None,
        },
        "hinweis": ("norm_pct = um den Quermarkt-Median bereinigte Veraenderung der "
                    "gemeldeten Aktien je Uebergang. NUR Richtung, kein Niveau: 13F "
                    "zaehlt Positionen doppelt (Konzernstrukturen, Wertpapierleihe). "
                    "Nur US-meldepflichtige Institutionelle - europaeische Werte fehlen. "
                    "melder/melder_delta_pct/melder_pctl = Zahl der meldenden "
                    "Adressen je Quartal, ihre quermarkt-normierte Veraenderung und "
                    "das Perzentil im 13F-Universum (100 = am breitesten gehalten) - "
                    "die Breiten-/underowned-Haelfte des Kriteriums."),
        "werte": werte,
    }
    os.makedirs(os.path.dirname(ziel), exist_ok=True)
    pfade.schreibe_json_atomar(ziel, out, ensure_ascii=False)
    mit_folge = sum(1 for v in werte.values() if v["steigend_folge"] >= 2)
    print(f"\nGespeichert: {ziel}")
    print(f"  {len(werte):,} Ticker, davon {mit_folge:,} mit >= 2 Quartalen in Folge steigend")
    if hat_melder:
        mit_pctl = [v["melder_pctl"] for v in werte.values()
                    if v.get("melder_pctl") is not None]
        neue = sum(1 for v in werte.values() if (v.get("melder_folge") or 0) >= 1)
        print(f"  Breite: {len(mit_pctl):,} Ticker mit Melder-Perzentil, "
              f"{neue:,} mit neuen Adressen im letzten Uebergang "
              f"(Universum-Median {out['melder_universum']['median']} Melder)")
    else:
        print("  Breite: keine Melder-Spalte in der INFOTABLE - "
              "underowned-Messung bleibt leer")
    return out


def lade():
    """Fertige Tabelle oder {} - wird von scorer.py benutzt."""
    try:
        with open(pfade.INSTITUTIONAL_13F, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bauen", action="store_true", help="Quartalstabelle neu bauen")
    p.add_argument("--quartale", type=int, default=QUARTALE_STANDARD)
    p.add_argument("--zeigen", nargs="*", metavar="TICKER")
    a = p.parse_args()
    if a.bauen:
        bauen(a.quartale)
    elif a.zeigen is not None:
        d = lade()
        if not d:
            print("Keine Tabelle vorhanden - erst --bauen laufen lassen.")
            return
        print(f"Stand {d.get('erstellt')}, Uebergaenge {d.get('uebergaenge')}")
        for t in (a.zeigen or list(d.get("werte", {}))[:10]):
            v = d.get("werte", {}).get(t.upper())
            print(f"  {t.upper():8s} {v}" if v else f"  {t.upper():8s} nicht enthalten")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
