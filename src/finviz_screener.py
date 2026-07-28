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

def ticker_einer_url(url, max_seiten=5):
    url = normalisiere(url)
    sep = "&" if "?" in url else "?"
    alle = []
    gesehen = set()
    for seite in range(max_seiten):
        start = seite * 20 + 1
        voll = url if seite == 0 else f"{url}{sep}r={start}"
        try:
            html = hole_seite(voll)
        except Exception as e:
            print(f"  ! Seite {seite+1}: {e}")
            break
        neu = []
        for t in TICKER_RX.findall(html):
            if t not in gesehen:
                gesehen.add(t)
                neu.append(t)
        if not neu:
            break  # keine neuen Treffer -> Ende
        alle.extend(neu)
        time.sleep(0.7)  # schonend
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
    max_seiten = fcfg.get("max_seiten", 5)
    signale = []
    for s in liste:
        name, url = s["name"], s["url"]
        tickers = ticker_einer_url(url, max_seiten)
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
