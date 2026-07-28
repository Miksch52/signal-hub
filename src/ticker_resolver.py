#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ticker-Resolver fuer den Daily Signal Hub.

Loest Firmennamen / Roh-Ticker -> validem Yahoo-Symbol auf. Portiert die in
"Maick's Trading System.html" bewaehrte Logik (TICKER_MAP, guessTicker,
_rankYahooQuote) und ergaenzt einen Namens-Aehnlichkeits-Check, damit z.B.
"AXA" nicht faelschlich auf "Axalta" (AXTA) gemappt wird.

US-Roh-Ticker (NVDA, MELI ...) werden direkt verwendet (US-Primaerlisting).
Reine Namen (Adidas, Vinci ...) gehen ueber TICKER_MAP -> Yahoo-Suche.
"""

import json
import re
import ssl
import time
import urllib.parse
import urllib.request

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}

# ---------------------------------------------------------------------------
# Name -> Yahoo-Symbol (portiert + erweitert). EU-Namen bevorzugt Heimatboerse.
# ---------------------------------------------------------------------------
TICKER_MAP = {
    # --- DACH / Europa ---
    "rheinmetall": "RHM.DE", "siemens energy": "ENR.DE", "siemens healthineers": "SHL.DE",
    "siemens": "SIE.DE", "sap": "SAP.DE", "adidas": "ADS.DE", "allianz": "ALV.DE",
    "deutsche telekom": "DTE.DE", "dt.telekom": "DTE.DE", "telekom": "DTE.DE",
    "rwe": "RWE.DE", "evotec": "EVT.DE", "nordex": "NDX1.DE", "takkt": "TTK.DE",
    "freenet": "FNTN.DE", "hypoport": "HYQ.DE", "vossloh": "VOS.DE",
    "delivery hero": "DHER.DE", "energiekontor": "EKT.DE", "mutares": "MUX.DE",
    "cancom": "COK.DE", "compugroup": "COP.DE", "porsche": "P911.DE",
    "hugo boss": "BOSS.DE", "duerr": "DUE.DE", "dürr": "DUE.DE", "bechtle": "BC8.DE",
    "infineon": "IFX.DE", "sartorius": "SRT.DE", "merck": "MRK.DE", "basf": "BAS.DE",
    "deutsche bank": "DBK.DE", "munich re": "MUV2.DE", "münchener rück": "MUV2.DE",
    "mercedes": "MBG.DE", "bmw": "BMW.DE", "volkswagen": "VOW3.DE", "vw": "VOW3.DE",
    "fresenius": "FRE.DE", "henkel": "HEN3.DE", "beiersdorf": "BEI.DE",
    "ams-osram": "AMS.SW", "ams osram": "AMS.SW", "melexis": "MELE.BR",
    "airbus": "AIR.PA", "vinci": "DG.PA", "axa": "CS.PA", "lvmh": "MC.PA",
    "schneider electric": "SU.PA", "totalenergies": "TTE.PA", "saint-gobain": "SGO.PA",
    "asml": "ASML.AS", "nestle": "NESN.SW", "nestlé": "NESN.SW", "novo nordisk": "NOVO-B.CO",
    "vestas": "VWS.CO", "mowi": "MOWI.OL", "salmar": "SALM.OL", "elbit": "ESLT",
    "sivers semiconductors": "SIVE.ST", "sivers": "SIVE.ST",
    # --- USA (Roh-Ticker werden i.d.R. direkt genutzt; hier nur Sonderfaelle/Namen) ---
    "robinhood": "HOOD", "salesforce": "CRM", "unity software": "U", "unity": "U",
    "hims & hers": "HIMS", "hims&hers": "HIMS", "hims": "HIMS", "arista networks": "ANET",
    "arista": "ANET", "dexcom": "DXCM", "pure storage": "PSTG", "stryker": "SYK",
    "paypal": "PYPL", "dropbox": "DBX", "chevron": "CVX", "take-two": "TTWO",
    "northrop": "NOC", "freeport": "FCX", "mercadolibre": "MELI",
    "taiwan semiconductor": "TSM", "tsmc": "TSM",
}

GENERISCH = {"ag", "se", "inc", "inc.", "corp", "corp.", "corporation", "co", "co.",
             "ltd", "ltd.", "plc", "nv", "n.v.", "sa", "s.a.", "spa", "group", "holding",
             "holdings", "company", "the", "technologies", "technology", "systems",
             "international", "&", "und", "and"}

def _tokens(s):
    s = (s or "").lower()
    s = re.sub(r"[^\wäöüß& ]", " ", s)
    return {t for t in s.split() if t and t not in GENERISCH}

def _aehnlich(query, kandidat_name):
    """Mindestens ein gemeinsames signifikantes Token."""
    a, b = _tokens(query), _tokens(kandidat_name)
    return bool(a & b)

def _passt(query, kandidat_name):
    """Strenger: alle Tokens der kuerzeren Seite muessen enthalten sein
    (verhindert AXA->Axalta-artige Fehltreffer)."""
    a, b = _tokens(query), _tokens(kandidat_name)
    if not a or not b:
        return False
    klein, gross = (a, b) if len(a) <= len(b) else (b, a)
    return klein <= gross

def guess_ticker(name):
    nl = (name or "").lower().strip()
    if not nl:
        return None
    for key, sym in TICKER_MAP.items():
        if key in nl:
            return sym
    return None

# --- Yahoo-Quote-Ranking (fuer Signal Hub: Primaerlisting bevorzugen) -------
# US-Werte -> US-Boerse, EU-Werte -> Heimatboerse. Spiegel-/OTC-Listings hinten.
US_MAJOR = {"NMS", "NYQ", "NGM", "ASE", "PCX", "BTS", "NCM", "NSI"}
EU_PRIMARY = {"GER", "PAR", "AMS", "EBS", "LSE", "MIL", "MCE", "CPH", "STO",
              "OSL", "HEL", "BRU", "LIS", "ICE", "ISE", "DUB"}
EU_PRIMARY_SFX = (".PA", ".AS", ".SW", ".L", ".MI", ".MC", ".CO", ".OL",
                  ".ST", ".HE", ".BR", ".LS", ".DE")
MIRROR_EX = {"FRA", "MUN", "STU", "BER", "DUS", "HAM", "HAN"}
MIRROR_SFX = (".F", ".MU", ".SG", ".BE", ".DU", ".HM", ".HA", ".STU")
OTC_EX = {"PNK", "OTC", "OQB", "OQX", "OBB", "PINK"}

def rank_quote(q):
    if q.get("quoteType") == "MUTUALFUND":
        return 999
    s = q.get("symbol", "")
    ex = (q.get("exchange") or "").upper()
    base = s.split(".")[0]
    if base.isdigit():
        return 900
    score = 0 if q.get("quoteType") == "EQUITY" else 15
    if s.endswith((".BA", ".VI", ".TI", ".XC", ".SN")):   # CEDEAR/Spiegel ganz hinten
        score += 30
    elif ex in OTC_EX:
        score += 40
    elif ex in US_MAJOR:
        score += 0                       # US-Primaerlisting zuerst
    elif "." not in s:
        score += 1                       # US-artig ohne Suffix
    elif ex in EU_PRIMARY or s.endswith(EU_PRIMARY_SFX):
        score += 2                       # EU-Heimatboerse
    elif ex in MIRROR_EX or s.endswith(MIRROR_SFX):
        score += 8                       # FRA/MUN-Spiegel
    else:
        score += 12
    return score

def _http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        return json.load(r)

def yahoo_suche(name):
    url = (f"https://query1.finance.yahoo.com/v1/finance/search?q="
           f"{urllib.parse.quote(name)}&quotesCount=8&newsCount=0")
    try:
        d = _http_json(url)
    except Exception:
        return None
    kandidaten = [q for q in d.get("quotes", [])
                  if q.get("quoteType") == "EQUITY" and q.get("symbol")
                  and _passt(name, q.get("shortname") or q.get("longname") or "")]
    if not kandidaten:
        return None
    kandidaten.sort(key=rank_quote)
    return kandidaten[0]["symbol"]

# --- Roh-Ticker erkennen ---------------------------------------------------
def _ist_wkn(s):
    return bool(re.fullmatch(r"[A-Z0-9]{6}", s or "")) and any(c.isdigit() for c in s)

def _ist_isin(s):
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s or ""))

# ---------------------------------------------------------------------------
def resolve(ticker, name, cache):
    """Gibt ein Yahoo-Symbol zurueck (oder None). Nutzt + fuellt cache (dict)."""
    ticker = (ticker or "").strip().upper()
    name = (name or "").strip()
    cache_key = f"{ticker}|{name.lower()}"
    if cache_key in cache:
        return cache[cache_key]

    sym = None
    # 1) echter US-/Normal-Ticker -> direkt (US-Primaerlisting, korrektes Volumen/RS)
    if ticker and not _ist_wkn(ticker) and not _ist_isin(ticker) and re.fullmatch(r"[A-Z]{1,5}", ticker):
        sym = ticker
    # 2) Name -> kuratierte Map
    if not sym and name:
        sym = guess_ticker(name)
    # 3) Name -> Yahoo-Suche (mit Ranking + Aehnlichkeit)
    if not sym and name:
        sym = yahoo_suche(name)
        time.sleep(0.2)
    cache[cache_key] = sym
    return sym
