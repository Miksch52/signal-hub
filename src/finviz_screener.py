#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finviz-Screener fuer den Daily Signal Hub.

Ruft die in config.json hinterlegten Finviz-Screener-URLs ab, extrahiert die
Treffer-Ticker (aus den t=-Links) ueber alle Ergebnisseiten und schreibt
data/signals_raw_finviz.json (gleiches Schema wie PDF/Mail).

Hinweis: Nur fuer den privaten Abruf der EIGENEN Screener-URLs gedacht; schonend
(kurze Pause je Seite). ToS von Finviz beachten.

Test:  python3 src/finviz_screener.py
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade
PROJEKT = pfade.PROJEKT
DATA = pfade.DATA
CONFIG = pfade.CONFIG

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

TICKER_RX = re.compile(r'[?&]t=([A-Z][A-Z0-9.\-]{0,5})\b')

def lade_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)

def normalisiere(url):
    # finviz.com/screener?... -> screener.ashx?... ; bestehendes &r= entfernen
    url = url.replace("/screener?", "/screener.ashx?")
    url = re.sub(r"[?&]r=\d+", "", url)
    return url

def screener_id(url):
    m = re.search(r"preset=([A-Za-z0-9]+)", url)
    if m:
        return "finviz:" + m.group(1)
    return "finviz:" + str(abs(hash(url)) % 10_000_000)

def hole_seite(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")

MAX_TREFFER_STANDARD = 200   # je Screener; config: quellen.finviz.max_treffer
MAX_ABRUFE = 40              # Notbremse gegen Endlosblaettern
PAUSE = 1.5                  # Sekunden zwischen zwei Seiten (schonend)
PAUSE_DROSSEL = 15           # Wartezeit vor dem einen Wiederholversuch

def _hole_mit_wiederholung(url):
    """Eine Seite holen; bei Drosselung (HTTP 429/5xx, Zeitueberschreitung)
    genau einmal nach PAUSE_DROSSEL Sekunden erneut versuchen."""
    try:
        return hole_seite(url)
    except Exception as e:
        print(f"  ! {e} - neuer Versuch in {PAUSE_DROSSEL}s")
        time.sleep(PAUSE_DROSSEL)
        return hole_seite(url)

def ticker_einer_url(url, max_treffer=MAX_TREFFER_STANDARD):
    """Alle Treffer eines Screeners ueber die Ergebnisseiten einsammeln.

    Bugfix 2026-11-15: bis dahin wurde fest in 20er-Schritten geblaettert
    (r=1, 21, 41) und nach 3 Seiten abgebrochen. Die Chart-Ansicht (v=211),
    die alle hinterlegten Screener nutzen, zeigt aber nur 12 Treffer je Seite -
    es kamen also hoechstens 36 Treffer an, mit Loechern (Platz 13-20 und
    33-40 fehlten). Gemessen: 36 von 81 bzw. 36 von 139. Jetzt wird um genau
    so viele Plaetze weitergeblaettert, wie die Seite gezeigt hat - das passt
    fuer jede Ansicht. Grenze ist die Trefferzahl, nicht mehr die Seitenzahl.
    """
    url = normalisiere(url)
    sep = "&" if "?" in url else "?"
    alle = []
    gesehen = set()
    start = 1
    for _ in range(MAX_ABRUFE):
        voll = url if start == 1 else f"{url}{sep}r={start}"
        try:
            html = _hole_mit_wiederholung(voll)
        except Exception as e:
            print(f"  ! ab Platz {start}: {e}")
            break
        auf_seite = list(dict.fromkeys(TICKER_RX.findall(html)))
        neu = [t for t in auf_seite if t not in gesehen]
        if not neu:
            break  # keine neuen Treffer -> Ende (Finviz wiederholt die letzte Seite)
        gesehen.update(neu)
        alle.extend(neu)
        if len(alle) >= max_treffer:
            alle = alle[:max_treffer]
            break
        start += len(auf_seite)
        time.sleep(PAUSE)
    return alle

def _screener_liste(fcfg):
    """Normalisiert config -> Liste {name,url}. Unterstuetzt neues 'screener'
    (Liste von Dicts mit aktiv) und altes 'screener_urls' (Liste von Strings)."""
    out = []
    for s in fcfg.get("screener", []):
        if s.get("aktiv", True) and s.get("url"):
            out.append({"name": s.get("name") or "Screener", "url": s["url"]})
    for url in fcfg.get("screener_urls", []):
        out.append({"name": "Screener", "url": url})
    return out

def screene_finviz():
    cfg = lade_config()
    fcfg = cfg["quellen"].get("finviz", {})
    liste = _screener_liste(fcfg)
    if not fcfg.get("aktiv") or not liste:
        print("Finviz-Quelle deaktiviert oder keine URL.")
        return []
    heute = datetime.now().strftime("%Y-%m-%d")
    # "max_seiten" (alt) wird nicht mehr gelesen: es zaehlte 20er-Seiten, die
    # es in der Chart-Ansicht nicht gibt (siehe ticker_einer_url).
    max_treffer = int(fcfg.get("max_treffer", MAX_TREFFER_STANDARD))
    signale = []
    for s in liste:
        name, url = s["name"], s["url"]
        tickers = ticker_einer_url(url, max_treffer)
        print(f"  + finviz:{name}: {len(tickers)} Treffer")
        for t in tickers:
            signale.append({
                "ticker": t, "name": None, "exchange": None, "markt": None,
                "quelle_typ": "finviz:" + name, "quelle_datei": screener_id(url),
                "datum": heute, "seite": None,
                "kontext": f"Finviz-Screener „{name}“", "fund_art": "finviz",
            })
    return signale

def main():
    signale = screene_finviz()
    print(f"\n=== {len(signale)} Roh-Signale aus Finviz ===")
    print(", ".join(s["ticker"] for s in signale[:40]))
    os.makedirs(DATA, exist_ok=True)
    ziel = pfade.RAW_FINVIZ
    with open(ziel, "w", encoding="utf-8") as f:
        json.dump(signale, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {ziel}")

if __name__ == "__main__":
    main()
