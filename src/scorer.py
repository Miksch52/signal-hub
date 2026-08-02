#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scorer fuer den Daily Signal Hub.

Nimmt die Roh-Signale (PDF + E-Mail + Finviz), aggregiert sie pro Ticker,
validiert jeden Ticker gegen Yahoo Finance (kostenlos) und berechnet einen
Unified Momentum Score aus 6 Faktoren, die Minervini / Weinstein / O'Neil /
Livermore / Zanger gemeinsam beschreiben:

  1. stage2_trend        - Aufwaertstrend (Trend-Template / Stage 2)
  2. relative_staerke    - Outperformance ggü. Markt-Index
  3. naehe_52w_hoch      - Naehe zum 52-Wochen-Hoch
  4. basis_konsolidierung- Volatilitaets-Verengung (VCP / enge Basis)
  5. volumen_bestaetigung- Volumen-Schub
  6. quellen_konsens     - in wie vielen Quellen genannt

Ausgabe:  data/signals.json  (+ data/signals.js fuers Dashboard ueber file://)

Test:  python3 scorer.py            # alle Roh-Signale
       python3 scorer.py --limit 40 # nur Top-40 nach Konsens (schnell, schonend)
"""

import concurrent.futures
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import ticker_resolver

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade
PROJEKT = pfade.PROJEKT
DATA = pfade.DATA
LOKAL = pfade.LOKAL
CONFIG_PFAD = pfade.CONFIG
CACHE_PFAD = pfade.YAHOO_CACHE
SYMBOL_CACHE_PFAD = pfade.SYMBOL_CACHE

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}

US_EXCHANGES = {"NMS", "NYQ", "NGM", "ASE", "PCX", "BTS", "NCM", "NSI", "SNP", "DJI"}

# ---------------------------------------------------------------------------
# Yahoo-Abruf (mit Tages-Cache)
# ---------------------------------------------------------------------------
def _http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        return json.load(r)

def yahoo_chart(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=2y&interval=1d")
    d = _http_json(url)
    res = d.get("chart", {}).get("result")
    if not res:
        return None
    r = res[0]
    meta = r.get("meta", {})
    q = r.get("indicators", {}).get("quote", [{}])[0]
    co, vo = q.get("close") or [], q.get("volume") or []
    hi, lo, op = q.get("high") or [], q.get("low") or [], q.get("open") or []
    # AUSGERICHTET: nur Indizes nehmen, an denen close+volume vorhanden sind
    closes, volumes, highs, lows, opens = [], [], [], [], []
    for i in range(len(co)):
        c = co[i]
        v = vo[i] if i < len(vo) else None
        if c is None or v is None:
            continue
        closes.append(c)
        volumes.append(v)
        highs.append(hi[i] if i < len(hi) and hi[i] is not None else c)
        lows.append(lo[i] if i < len(lo) and lo[i] is not None else c)
        # Fallback auf Close, falls Open fehlt -> Gap=0, loest f_gap80 nie faelschlich aus
        opens.append(op[i] if i < len(op) and op[i] is not None else c)
    if len(closes) < 60:
        return None
    return {"meta": meta, "closes": closes, "volumes": volumes,
            "highs": highs, "lows": lows, "opens": opens}

def lade_earnings_kalender(tage):
    """Nasdaq-Earnings-Kalender fuer die naechsten `tage` Tage -> {SYMBOL: 'YYYY-MM-DD'}.
    Wenige Abrufe (1 je Tag) statt 400 Einzelabfragen. V.a. US-Werte."""
    H = {"User-Agent": UA["User-Agent"], "Accept": "application/json, text/plain, */*",
         "Accept-Language": "en-US,en;q=0.9"}
    kal = {}
    heute = datetime.now().date()
    for off in range(0, max(1, tage) + 1):
        d = (heute + timedelta(days=off)).strftime("%Y-%m-%d")
        try:
            req = urllib.request.Request(
                f"https://api.nasdaq.com/api/calendar/earnings?date={d}", headers=H)
            with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
                j = json.load(r)
            for row in (j.get("data", {}).get("rows") or []):
                sym = (row.get("symbol") or "").upper().strip()
                if sym and sym not in kal:
                    kal[sym] = d
        except Exception:
            pass
        time.sleep(0.3)
    return kal

PROFIL_CACHE_PFAD = pfade.PROFIL_CACHE

def lade_profil_cache():
    if os.path.exists(PROFIL_CACHE_PFAD):
        try:
            return json.load(open(PROFIL_CACHE_PFAD, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def nasdaq_profil(symbol):
    """{sektor,branche} via Nasdaq (US-Werte)."""
    H = {"User-Agent": UA["User-Agent"], "Accept": "application/json, text/plain, */*",
         "Accept-Language": "en-US,en;q=0.9"}
    try:
        req = urllib.request.Request(
            f"https://api.nasdaq.com/api/company/{urllib.parse.quote(symbol)}/company-profile", headers=H)
        with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as r:
            d = (json.load(r).get("data") or {})
        sek = (d.get("Sector") or {}).get("value")
        bra = (d.get("Industry") or {}).get("value")
        if sek or bra:
            return {"sektor": sek, "branche": bra}
    except Exception:
        pass
    return None

def _nasdaq_num(s):
    """'$416,161,000' / '(1,234)' / '-' / 'NM' -> float oder None."""
    if not s:
        return None
    s = s.strip().replace("$", "").replace(",", "").replace("%", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-", "N/A", "--", "NM"):
        return None
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None

def nasdaq_fundamental(symbol):
    """{eps_g, rev_g, eps_g_prior, rev_g_prior} als JAHRES-YoY via Nasdaq
    annual income statement - Crumb-FREIE Zweitquelle fuer US-Werte, wenn
    Yahoo (quoteSummary, Crumb) gerade blockiert. Net-Income-Wachstum als
    Naeherung fuers EPS-Wachstum (Aktienzahl meist stabil; O'Neil/Minervini
    zielen ohnehin aufs Gewinnwachstum). Nur US (api.nasdaq.com). Endpunkt am
    2026-07-24 gegen die echte Antwort verifiziert (financials?frequency=1 ->
    data.incomeStatementTable, Zeilen 'Total Revenue' / 'Net Income Applicable
    to Common Shareholders', Spalten value2=neuestes .. value5=aeltestes Jahr).
    Die Tabelle liefert 4 Jahresspalten in einem einzigen Abruf - value4 wird
    zusaetzlich zu value2/value3 ausgelesen, um neben der aktuellen YoY-Rate
    auch die VORJAHRES-YoY-Rate zu bilden (eps_g_prior/rev_g_prior) und damit
    Wachstums-BESCHLEUNIGUNG zu erkennen (siehe fundamental_wert()), ohne
    einen zweiten Request zu brauchen."""
    H = {"User-Agent": UA["User-Agent"], "Accept": "application/json, text/plain, */*",
         "Accept-Language": "en-US,en;q=0.9"}
    try:
        req = urllib.request.Request(
            f"https://api.nasdaq.com/api/company/{urllib.parse.quote(symbol)}/financials?frequency=1",
            headers=H)
        with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as r:
            d = (json.load(r).get("data") or {})
        rows = (d.get("incomeStatementTable") or {}).get("rows") or []
        rev = ni = None
        for row in rows:
            lbl = (row.get("value1") or "").strip().lower()
            if rev is None and "total revenue" in lbl:
                rev = (_nasdaq_num(row.get("value2")), _nasdaq_num(row.get("value3")),
                       _nasdaq_num(row.get("value4")))
            if ni is None and lbl == "net income applicable to common shareholders":
                ni = (_nasdaq_num(row.get("value2")), _nasdaq_num(row.get("value3")),
                      _nasdaq_num(row.get("value4")))
        if ni is None:      # Fallback-Zeile, falls die Common-Zeile fehlt
            for row in rows:
                if (row.get("value1") or "").strip().lower() == "net income":
                    ni = (_nasdaq_num(row.get("value2")), _nasdaq_num(row.get("value3")),
                          _nasdaq_num(row.get("value4")))
                    break

        def _g(cur, prev):
            if cur is None or prev is None or prev <= 0:   # Vorjahr <=0 -> Wachstum sinnlos
                return None
            return cur / prev - 1

        eg = _g(ni[0], ni[1]) if ni else None
        rg = _g(rev[0], rev[1]) if rev else None
        eg_prior = _g(ni[1], ni[2]) if ni else None
        rg_prior = _g(rev[1], rev[2]) if rev else None
        if eg is None and rg is None:
            return None
        return {"eps_g": eg, "rev_g": rg, "eps_g_prior": eg_prior, "rev_g_prior": rg_prior}
    except Exception:
        return None

def yahoo_crumb():
    """(opener, crumb) oder (None, None) - fuer quoteSummary (EU-Sektor/Earnings)."""
    try:
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                         urllib.request.HTTPSHandler(context=SSL_CTX))
        try:
            op.open(urllib.request.Request("https://finance.yahoo.com", headers=UA), timeout=12)
        except Exception:
            pass
        crumb = op.open(urllib.request.Request(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", headers=UA), timeout=12).read().decode()
        if crumb and "<html" not in crumb.lower() and "too many" not in crumb.lower():
            return op, crumb
    except Exception:
        pass
    return None, None

def yahoo_profil_earnings(symbol, op, crumb):
    """{sektor,branche,earnings_datum} via Yahoo quoteSummary (funktioniert auch fuer EU)."""
    if not op or not crumb:
        return None
    try:
        u = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(symbol)}"
             f"?modules=assetProfile,calendarEvents&crumb={urllib.parse.quote(crumb)}")
        with op.open(urllib.request.Request(u, headers=UA), timeout=12) as r:
            j = json.load(r)["quoteSummary"]["result"][0]
        ap = j.get("assetProfile", {}) or {}
        ce = (j.get("calendarEvents", {}) or {}).get("earnings", {}).get("earningsDate", [])
        ed = None
        if ce and ce[0].get("raw"):
            ed = datetime.fromtimestamp(ce[0]["raw"]).strftime("%Y-%m-%d")
        return {"sektor": ap.get("sector"), "branche": ap.get("industry"), "earnings_datum": ed}
    except Exception:
        return None

# --- Fundamental-Faktor (EPS-/Umsatzwachstum) ------------------------------
FUND_CACHE_TAGE = 7      # Fundamentals aendern sich quartalsweise -> 7-Tage-TTL
FUND_MAX_ABRUFE = 300    # Deckel je Lauf (schonend; Rest kommt beim naechsten Lauf)

def yahoo_fundamental(symbol, op, crumb):
    """{eps_g, rev_g} (juengstes Quartal vs. Vorjahresquartal, als Anteile,
    z.B. 0.25 = +25%) via Yahoo quoteSummary financialData (earningsGrowth/
    revenueGrowth - Felder am 2026-07-24 gegen die quoteSummary-Schnittstelle
    verifiziert). Braucht den Crumb wie yahoo_profil_earnings()."""
    if not op or not crumb:
        return None
    try:
        u = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
             f"{urllib.parse.quote(symbol)}?modules=financialData&crumb={urllib.parse.quote(crumb)}")
        with op.open(urllib.request.Request(u, headers=UA), timeout=12) as r:
            j = json.load(r)["quoteSummary"]["result"][0].get("financialData") or {}
        eg = (j.get("earningsGrowth") or {}).get("raw")
        rg = (j.get("revenueGrowth") or {}).get("raw")
        if eg is None and rg is None:
            return None
        return {"eps_g": eg, "rev_g": rg}
    except Exception:
        return None

def fundamental_wert(eps_g, rev_g, eps_g_prior=None):
    """0..1 aus EPS-/Umsatzwachstum (YoY). O'Neil/Minervini: >=25%
    EPS-Wachstum ideal, Umsatz bestaetigt. eps: 0% -> 0, >=25% -> 1;
    Umsatz: 0% -> 0, >=20% -> 1; Mittel der vorhandenen Teile.
    Beschleunigungs-Bonus (+0.15, gedeckelt bei 1.0): waechst der Gewinn
    schneller als in der Vorperiode? Minervini gewichtet beschleunigendes
    Wachstum hoeher als die reine Hoehe der Rate. Nur verfuegbar, wenn eine
    Vorperioden-Vergleichszahl vorliegt (aktuell nur ueber den Nasdaq-
    Jahresdaten-Fallback, siehe nasdaq_fundamental())."""
    teile = []
    if eps_g is not None:
        teile.append(clamp(eps_g / 0.25))
    if rev_g is not None:
        teile.append(clamp(rev_g / 0.20))
    if not teile:
        return None
    wert = sum(teile) / len(teile)
    if eps_g is not None and eps_g > 0 and eps_g_prior is not None and eps_g > eps_g_prior:
        wert = clamp(wert + 0.15)
    return wert

def _fmt_pct(x):
    return f"{x*100:+.0f}%" if x is not None else "?"

def f_fundamental(ergebnisse, gew, gew_summe, schwellen, yop, ycrumb):
    """Post-Pass: Fundamental-Bestaetigung (C-A-N/SEPA: Gewinn- und
    Umsatzwachstum) fuer Treffer ab Beobachten-Schwelle. Zwei Datenquellen mit
    7-Tage-Cache (auch Fehlversuche werden gecacht, sonst haemmert jeder Lauf
    dieselben Luecken):
      1. Yahoo quoteSummary financialData (Quartal, YoY) - erste Wahl, braucht
         aber den Crumb (der zeitweise Yahoo-seitig blockiert ist).
      2. Nasdaq annual income statement (Jahr, YoY) - Crumb-FREIE Zweitquelle
         NUR fuer US-Werte (nasdaq_fundamental()). Springt ein, wenn Yahoo
         nichts liefert - ein erfolgloser Yahoo-only-Cache blockiert den
         Nasdaq-Versuch NICHT (sonst bliebe der Fallback bei blockiertem Crumb
         7 Tage ungenutzt). Ist eine Aktie auch bei Nasdaq abgefragt und leer,
         wird DAS gecacht -> kein staendiges Nachschlagen.
    Ohne Zahlen aus beiden Quellen greift der Quellen-Fallback: Fund ueber den
    Finviz-SEPA-Screener (harter EPS/Sales>20%-QoQ-Filter) oder Code 33 im
    Trend-Screener-Kontext => 0.75; sonst leichter Malus 0.35 (statt neutral
    0.5, Stand bis 2026-07-24) - komplett fehlende Fundamentaldaten sind kein
    Beleg fuer Qualitaet und sollen nicht wie eine Bestaetigung wirken, aber
    auch kein hartes "rot" (ampel()-Schwelle liegt bei 0.33), da es oft nur an
    der Datenquelle liegt (Crumb blockiert, Nicht-US-Wert). Score-Anpassung
    wie bei den anderen Post-Pass-Faktoren nur ueber das Delta zum
    0.5-Platzhalter aus dem Haupt-Loop."""
    cache = {}
    if os.path.exists(pfade.FUNDAMENTAL_CACHE):
        try:
            cache = json.load(open(pfade.FUNDAMENTAL_CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    heute = datetime.now().date()
    abrufe = mit_zahlen = neu_yahoo = neu_nasdaq = 0
    for e in ergebnisse:
        sym = e["yahoo_symbol"]
        c = cache.get(sym)
        hat_zahlen = bool(c and (c.get("eps_g") is not None or c.get("rev_g") is not None))
        frisch = False
        if c and c.get("stand"):
            try:
                frisch = (heute - datetime.strptime(c["stand"], "%Y-%m-%d").date()).days <= FUND_CACHE_TAGE
            except Exception:
                frisch = False
        # US-Fallback nachholen: ein erfolgloser Yahoo-only-Cache (frisch, aber
        # ohne Zahlen und noch nie bei Nasdaq versucht) darf Nasdaq nicht sperren.
        nasdaq_offen = (e.get("markt") == "USA" and not hat_zahlen
                        and (c or {}).get("quelle") != "nasdaq")
        relevant = e["score"] >= schwellen["beobachten"]
        if relevant and abrufe < FUND_MAX_ABRUFE and (not frisch or nasdaq_offen):
            d, quelle, versucht = None, None, False
            if ycrumb and not frisch:            # Yahoo nur bei wirklich veraltetem Cache
                d = yahoo_fundamental(sym, yop, ycrumb)
                time.sleep(0.15); abrufe += 1; versucht = True
                if d:
                    quelle = "yahoo"; neu_yahoo += 1
            if not d and e.get("markt") == "USA":   # Crumb-freie US-Zweitquelle
                nd = nasdaq_fundamental(sym)
                time.sleep(0.15); abrufe += 1; versucht = True
                if nd:
                    d, quelle = nd, "nasdaq"; neu_nasdaq += 1
            # Nur cachen, wenn wirklich ein Abruf lief - sonst wuerde ein
            # EU-Wert bei blockiertem Crumb faelschlich als "geprueft/leer"
            # 7 Tage den spaeteren Yahoo-Retry sperren.
            if versucht:
                if quelle is None:      # Fehlversuch: Quelle = zuletzt versuchte
                    quelle = "nasdaq" if e.get("markt") == "USA" else "yahoo"
                c = {"eps_g": (d or {}).get("eps_g"), "rev_g": (d or {}).get("rev_g"),
                     "eps_g_prior": (d or {}).get("eps_g_prior"),
                     "quelle": quelle, "stand": heute.strftime("%Y-%m-%d")}
                cache[sym] = c
        eps_g = c.get("eps_g") if c else None
        rev_g = c.get("rev_g") if c else None
        eps_g_prior = c.get("eps_g_prior") if c else None
        wert = fundamental_wert(eps_g, rev_g, eps_g_prior)
        if wert is not None:
            mit_zahlen += 1
            beschleunigt = (eps_g is not None and eps_g > 0
                            and eps_g_prior is not None and eps_g > eps_g_prior)
            if (c or {}).get("quelle") == "nasdaq":
                detail = f"EPS {_fmt_pct(eps_g)} / Umsatz {_fmt_pct(rev_g)} (Jahr, YoY · Nasdaq)"
                if beschleunigt:
                    detail += f" · beschleunigt ggü. Vorjahr ({_fmt_pct(eps_g_prior)})"
            else:
                detail = f"EPS {_fmt_pct(eps_g)} / Umsatz {_fmt_pct(rev_g)} (Quartal, YoY)"
        else:
            typen = e.get("quellen", {}).get("typen") or []
            kontext = e.get("quellen", {}).get("kontext") or ""
            sepa = any("SEPA" in t.upper() for t in typen)
            code33 = "Code 33" in kontext
            if sepa or code33:
                wert = 0.75
                quelle = "Finviz-SEPA-Filter (EPS/Sales >20% QoQ)" if sepa else "Trend-Screener Code 33"
                detail = f"keine Zahlen - Fundamental-Pass via {quelle}"
            else:
                wert = 0.35
                detail = "keine Fundamentaldaten (leichter Malus, keine Bestaetigung moeglich)"
        e["faktoren"]["fundamental"] = {
            "wert": round(wert, 2), "ampel": ampel(wert),
            "gewicht": gew.get("fundamental", 0), "detail": detail}
        delta = gew.get("fundamental", 0) * (wert - 0.5) / gew_summe * 100
        score = e["score"] + delta
        e["score"] = round(score, 1)
        e["tier"] = ("A" if score >= schwellen["kauf_kandidat"]
                     else "B" if score >= schwellen["beobachten"] else "C")
        e["einstufung"] = ("Kauf-Kandidat" if score >= schwellen["kauf_kandidat"]
                            else "Beobachten" if score >= schwellen["beobachten"] else "—")
    with open(pfade.FUNDAMENTAL_CACHE, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)
    print(f"Fundamental: {mit_zahlen} mit Zahlen, {abrufe} Abrufe "
          f"(Yahoo {neu_yahoo}, Nasdaq {neu_nasdaq}; "
          f"Crumb {'ok' if ycrumb else 'fehlt -> Nasdaq-Fallback fuer US'})")

def lade_depot_namen(datei):
    """Namen offener Positionen (status 'Offen'). Lokal aus mts_data.json;
    im Cloud-Lauf (keine mts_data.json im Checkout) aus der Umgebungsvariable
    SIGNALHUB_DEPOT_NAMEN (JSON-Array reiner Namen, kein Betrag/Stueckzahl -
    GitHub-Secret, siehe signal-hub.yml). Env hat Vorrang, falls gesetzt."""
    env = os.environ.get("SIGNALHUB_DEPOT_NAMEN")
    if env:
        try:
            return json.loads(env)
        except Exception:
            return []
    if not os.path.isabs(datei):
        datei = os.path.normpath(os.path.join(PROJEKT, datei))
    try:
        d = json.load(open(datei, encoding="utf-8"))
    except Exception:
        return []
    return [t["name"] for t in d.get("trades", [])
            if (t.get("status") or "").lower() == "offen" and t.get("name")]

def depot_match(name, depot_namen):
    """True, wenn alle signifikanten Tokens der kuerzeren Seite enthalten sind
    (strikter als bloss ein gemeinsames Wort -> keine ETF-Fehltreffer)."""
    nt = ticker_resolver._tokens(name)
    if not nt:
        return False
    for dn in depot_namen:
        dt = ticker_resolver._tokens(dn)
        if not dt:
            continue
        klein, gross = (dt, nt) if len(dt) <= len(nt) else (nt, dt)
        if klein <= gross:
            return True
    return False

def _decay(datum_str, halbwert):
    if not datum_str or not halbwert:
        return 1.0
    try:
        alter = (datetime.now().date() - datetime.strptime(datum_str, "%Y-%m-%d").date()).days
    except Exception:
        return 1.0
    return 0.5 ** (alter / float(halbwert)) if alter > 0 else 1.0

# Cache --------------------------------------------------------------------
def lade_cache():
    if os.path.exists(CACHE_PFAD):
        try:
            with open(CACHE_PFAD, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def speichere_cache(cache):
    with open(CACHE_PFAD, "w", encoding="utf-8") as f:
        json.dump(cache, f)

def hole_chart_cached(symbol, cache):
    heute = datetime.now().strftime("%Y-%m-%d")
    key = f"{symbol}@{heute}"
    if key in cache:
        return cache[key]
    try:
        data = yahoo_chart(symbol)
    except Exception:
        # Performance-Review 2026-08-02: transiente Netzwerkfehler NICHT cachen -
        # sonst ist ein einmaliger Timeout fuer den Rest des Tages ununterscheidbar
        # von "Ticker hat wirklich keine Daten" und sperrt ihn aus der Pipeline.
        # Kein Cache-Eintrag -> ein spaeterer Aufruf (naechster Cron-Slot, oder
        # falls dieser Aufruf aus dem Haupt-Loop nach einem parallelen Praefetch-
        # Fehlschlag kommt) versucht es erneut. Nur ein echtes (fehlerfreies)
        # Leerergebnis von yahoo_chart() gilt als legitimes "keine Daten".
        time.sleep(0.25)
        return None
    cache[key] = data
    time.sleep(0.25)  # schonend zu Yahoo
    return data

def prefetch_charts_parallel(symbols, cache, max_workers=5):
    """Holt alle noch nicht gecachten Charts parallel statt seriell mit
    time.sleep() dazwischen - bei ~1300 Tickern kostete die serielle Variante
    allein >5 Minuten reine Sleep-Zeit pro Lauf (Performance-Review 2026-08-02).
    Schreibt Ergebnisse in `cache` im selben Format/Key wie hole_chart_cached(),
    danach ist der bestehende sequenzielle Bewertungs-Loop UNVERAENDERT - jeder
    hole_chart_cached()-Aufruf dort wird zum reinen Cache-Hit (kein Refactoring
    der 1000+ Zeilen Faktoren-Logik noetig). max_workers bewusst moderat (5),
    um Yahoo nicht mit zu vielen gleichzeitigen Anfragen zu belasten - jeder
    Worker pausiert wie bisher zwischen seinen eigenen Anfragen."""
    heute = datetime.now().strftime("%Y-%m-%d")
    fehlend, gesehen = [], set()
    for symbol in symbols:
        if not symbol or symbol in gesehen:
            continue
        gesehen.add(symbol)
        if f"{symbol}@{heute}" not in cache:
            fehlend.append(symbol)
    if not fehlend:
        return
    def _fetch(symbol):
        try:
            data = yahoo_chart(symbol)
            fehler = False
        except Exception:
            data, fehler = None, True
        time.sleep(0.2)
        return symbol, data, fehler
    print(f"  Praefetch: {len(fehlend)} neue Charts ({max_workers} parallel) ...")
    fertig = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for symbol, data, fehler in ex.map(_fetch, fehlend):
            if not fehler:
                # nur erfolgreiche Abrufe cachen (auch ein echtes Leerergebnis) -
                # bei einer Exception bleibt der Key leer, siehe hole_chart_cached()
                cache[f"{symbol}@{heute}"] = data
            fertig += 1
            if fertig % 100 == 0:
                print(f"    {fertig}/{len(fehlend)} praefetcht ...")

# ---------------------------------------------------------------------------
# Indikatoren
# ---------------------------------------------------------------------------
def sma(werte, n, offset=0):
    teil = werte[-(n + offset): len(werte) - offset] if offset else werte[-n:]
    if len(teil) < n:
        return None
    return sum(teil) / n

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def markt_von_meta(meta):
    exch = (meta.get("exchangeName") or meta.get("fullExchangeName") or "").upper()
    cur = (meta.get("currency") or "").upper()
    if exch in US_EXCHANGES or cur == "USD":
        return "USA"
    return "Europa"

def markt_regime(idx_closes):
    """Ampel fuer den Gesamtmarkt (Minervini/Weinstein: erst das Umfeld, dann
    die Aktie - in einem schwachen Markt scheitern auch die besten Setups).
    Seit 2026-07-27 dieselbe 4-Kriterien-Schwelle wie die volle Minervini-
    Ampel (assets/marktampel.js::ampelEvaluateIndex, geteilt mit Startseite/
    Markets 360): kurs_ueber_sma50, kurs_ueber_sma200, sma50_ueber_sma200,
    sma200_steigt (21-Tage-Lookback, konsistent zu f_stage2) - n = Anzahl
    erfuellter Kriterien. gruen = ueber MA200 UND MA50>MA200 UND n>=3; rot =
    NICHT ueber MA200 ODER NICHT MA50>MA200; gelb = dazwischen. Zuvor verlangte
    "gruen" hier zusaetzlich hart Kurs>MA50 und pruefte MA50>MA200 gar nicht -
    das liess die Score-Daempfung (f_marktampel_dynamik) vom vollen Header-
    Ampel-Chip auseinanderlaufen (z.B. Kurs knapp unter MA50, sonst ueberall
    bullisch: volle Ampel gruen, alte Pruefung hier faelschlich gelb). Wird
    je Markt berechnet und nach signals.json geschrieben -> funktioniert auch
    auf der Cloud-Version ohne erreichbaren Mac mini. Die Sentiment-Schicht
    der vollen Ampel (VIX/Distribution-Days/Small-Caps) bleibt bewusst aussen
    vor - die noetigen Zusatzdaten holt der Scorer bisher nicht."""
    if not idx_closes or len(idx_closes) < 221:
        return {"ampel": "unbekannt", "detail": "zu wenig Index-Historie", "hinweis": ""}
    p = idx_closes[-1]
    s50, s200 = sma(idx_closes, 50), sma(idx_closes, 200)
    s200_alt = sma(idx_closes, 200, offset=21)
    ueber50, ueber200 = p > s50, p > s200
    s50_ueber_s200 = bool(s50 and s200 and s50 > s200)
    steigt = bool(s200 and s200_alt and s200 > s200_alt)
    n = sum([ueber50, ueber200, s50_ueber_s200, steigt])
    if ueber200 and s50_ueber_s200 and n >= 3:
        a, hinweis = "gruen", "Markt im Aufwärtstrend – gutes Umfeld für neue Käufe."
    elif not ueber200 or not s50_ueber_s200:
        a, hinweis = "rot", "Index unter MA200 oder MA50 unter MA200 – Minervini: keine neuen Positionen im schwachen Markt."
    else:
        a, hinweis = "gelb", "Markt uneinheitlich – neue Käufe nur mit Vorsicht."
    return {"ampel": a, "ueber_ma50": ueber50, "ueber_ma200": ueber200,
            "sma50_ueber_sma200": s50_ueber_s200, "ma200_steigt": steigt, "hinweis": hinweis,
            "detail": (f"Index {'>' if ueber200 else '<'}MA200 "
                       f"({'steigend' if steigt else 'fallend'}), "
                       f"{'>' if ueber50 else '<'}MA50, MA50 {'>' if s50_ueber_s200 else '<'}MA200")}

# --- die 6 Faktoren, jeweils 0..1 + Detailtext -----------------------------
def f_stage2(closes, hi52, lo52):
    p = closes[-1]
    s50, s150, s200 = sma(closes, 50), sma(closes, 150), sma(closes, 200)
    # Lookback 21 Handelstage (~1 Kalendermonat), abgeglichen mit
    # MinerviniMarkets360 (config.yaml: sma200_uptrend_lookback: 21).
    s200_alt = sma(closes, 200, offset=21)
    s150_alt = sma(closes, 150, offset=21)
    # Feste 8 Kriterien: fehlende SMA (zu wenig Historie) = NICHT erfuellt.
    # 30%/25%-Schwellen unten sind bereits identisch mit MinerviniMarkets360
    # (min_pct_above_low/within_pct_of_high) und dem Trend-Screener
    # (MIN_PCT_ABOVE_52W_LOW/MAX_PCT_BELOW_52W_HIGH) - keine Aenderung noetig.
    # RS-Schwellen bleiben bewusst engine-spezifisch: hier ein stetiger
    # 0..1-Faktor (f_relative_staerke), bei Markets 360 ein hartes
    # Trend-Template-Kriterium (rs_min 70), beim Trend-Screener ein
    # "Leader"-Label (RS_THRESHOLD 80) - unterschiedliche Konzepte, keine
    # gemeinsame Zahl. VCP-Erkennung bleibt aus demselben Grund je Engine
    # eigenstaendig (siehe Punkt 4 in der Architektur-Notiz).
    krit = [
        bool(s150 and s200 and p > s150 and p > s200),
        bool(s150 and s200 and s150 > s200),
        bool(s200 and s200_alt and s200 > s200_alt),   # SMA200 steigt
        bool(s150 and s150_alt and s150 > s150_alt),   # SMA150 steigt
        bool(s50 and s150 and s200 and s50 > s150 > s200),
        bool(s50 and p > s50),
        p >= 1.30 * lo52,                              # >=30% ueber 52W-Tief
        p >= 0.75 * hi52,                              # <=25% unter 52W-Hoch
    ]
    erfuellt = sum(krit)
    return erfuellt / len(krit), f"{erfuellt}/{len(krit)} Trend-Kriterien"

# Gewichtete Multi-Fenster-RS (IBD/Minervini-Stil): das juengste Quartal
# zaehlt doppelt - erkennt Werte, die JETZT Fuehrung uebernehmen, statt
# solcher, die nur vor Monaten liefen.
RS_FENSTER = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))

def rs_gewichtet(closes):
    """Gewichtete Kursrelation ueber 3/6/9/12 Monate (40/20/20/20).
    Fehlende lange Fenster (junge Aktien/IPOs) werden uebersprungen und die
    Gewichte renormalisiert - wie beim IBD-Rating, das junge Werte auf
    kuerzerer Historie einstuft; mindestens das 63-Tage-Fenster ist Pflicht."""
    c = [x for x in closes if x is not None]
    if len(c) < 64 or not c[-1]:
        return None
    summe = gew = 0.0
    for n, w in RS_FENSTER:
        if len(c) <= n or not c[-1 - n]:
            continue
        summe += w * (c[-1] / c[-1 - n])
        gew += w
    return summe / gew if gew else None

def f_relative_staerke(closes, idx_closes):
    """Gewichtete Multi-Fenster-RS gegen den Markt-Index (frueher: ein
    einzelnes 6-Monats-Fenster). Der stetige 0..1-Wert hier ist vorlaeufig -
    nach dem Haupt-Loop mischt f_rs_pool_rang() ihn 50/50 mit dem
    Perzentil-Rang im aktuellen Kandidatenpool (RS ist bei IBD/Minervini
    ein RANG, keine absolute Zahl)."""
    s = rs_gewichtet(closes)
    i = rs_gewichtet(idx_closes)
    if s is None or i is None or not i:
        return 0.0, "zu wenig Historie", None
    rs = s / i
    wert = clamp((rs - 0.95) / (1.40 - 0.95))
    return wert, f"RS gew. {rs:.2f} (40% 3M + je 20% 6/9/12M vs Index)", rs

def f_naehe_hoch(closes, hi52):
    p = closes[-1]
    dist = (hi52 - p) / hi52 if hi52 else 1.0
    wert = clamp(1 - dist / 0.25)
    return wert, f"{dist*100:.0f}% unter 52W-Hoch"

def f_basis(closes):
    if len(closes) < 60:
        return 0.0, "zu wenig Historie"
    letzte = closes[-20:]
    davor = closes[-60:-20]
    r_neu = (max(letzte) - min(letzte)) / min(letzte) if min(letzte) else 1
    r_alt = (max(davor) - min(davor)) / min(davor) if min(davor) else 1
    contraction = r_alt / r_neu if r_neu else 1
    wert = clamp((contraction - 0.8) / (1.8 - 0.8))
    if r_neu > 0.20:                # zu weite Range = keine enge Basis
        wert = min(wert, 0.4)
    return wert, f"Range 4W {r_neu*100:.0f}% (Verengung {contraction:.1f}x)"

def f_volumen(closes, volumes):
    if len(volumes) < 50:
        return 0.0, "zu wenig Volumendaten"
    avg50 = sum(volumes[-50:]) / 50
    last5 = sum(volumes[-5:]) / 5
    ratio = last5 / avg50 if avg50 else 1
    wert = clamp((ratio - 0.9) / (1.6 - 0.9))
    up_day = len(closes) >= 2 and closes[-1] > closes[-2]
    if up_day and volumes[-1] > 1.4 * avg50:
        wert = max(wert, 0.85)
    return wert, f"Vol 5T/50T {ratio:.1f}x"

def basis_anzahl(closes):
    """Grobe Naeherung der Minervini-Basiszahl (rein informativ, KEIN Score-
    Faktor): zaehlt abgeschlossene Konsolidierungen (Hoch -> Ruecksetzer >=10 %
    -> neues Hoch) seit dem letzten 52-Wochen-Tief. Je hoeher die Zahl, desto
    spaeter/riskanter die aktuelle Basis (Minervini: Vorsicht ab der 3./4. Basis,
    Klimax-Gefahr). Heuristik auf Schlusskursen, kein Peak-Detection-Modell -
    dient der groben Einordnung, nicht als exakte Basiszaehlung wie im Chart.
    """
    c = [x for x in closes if x is not None]
    if len(c) < 40:
        return None
    fenster = c[-252:] if len(c) >= 252 else c
    lo_idx = fenster.index(min(fenster))
    segment = fenster[lo_idx:]
    if len(segment) < 30:
        return 0
    hoch = segment[0]
    tief_seit_hoch = segment[0]
    zaehler = 0
    for p in segment[1:]:
        if p > hoch:
            if tief_seit_hoch <= hoch * 0.90:      # >=10% Ruecksetzer vor neuem Hoch
                zaehler += 1
            hoch = p
            tief_seit_hoch = p
        else:
            tief_seit_hoch = min(tief_seit_hoch, p)
    return zaehler

def extended_pct(closes):
    """Prozentualer Abstand des Kurses zur SMA50 (Minervini: der Standard-Test
    fuer 'zu weit gelaufen' - nicht mehr kaufen, ggf. Gewinne sichern statt
    nachkaufen). Rein informativ, KEIN Score-Faktor, wie basis_anzahl()."""
    c = [x for x in closes if x is not None]
    s50 = sma(c, 50)
    if not s50 or not c:
        return None
    return round((c[-1] - s50) / s50 * 100, 1)

def ist_short_setup(closes):
    """Optionale Zusatz-Einordnung zur 50/80-Regel (Minervini nutzt Gap-down +
    Bruch der gleitenden Durchschnitte auch als Short-Signal): Kurs UNTER
    SMA50, SMA150 UND SMA200 gleichzeitig - das Gegenstueck zum Stage-2-Trend-
    Kriterium. Nur eine grobe Lage-Einordnung, kein eigener VCP-/Ausbruchstest
    wie beim Long-Pivot-Detektor."""
    c = [x for x in closes if x is not None]
    s50, s150, s200 = sma(c, 50), sma(c, 150), sma(c, 200)
    if not (s50 and s150 and s200) or not c:
        return False
    p = c[-1]
    return p < s50 and p < s150 and p < s200

def gewicht_fuer_typ(typ, mailgew):
    """Gewicht einer Quelle fuer den Konsens. PDF/Finviz = 1.0; Mail-Typen
    nach config.mail_signal_gewichte (falls aktiv), sonst 1.0."""
    if typ.startswith("mail:") and mailgew.get("aktiv"):
        return float(mailgew.get(typ.split(":", 1)[1], 1.0))
    return 1.0

def provider_group(typ):
    """Unabhaengiger Anbieter. Alle TraderFox-Publikationen + deren Mail-Alerts
    zaehlen als EIN Anbieter (kein unabhaengiger Konsens); Finviz, Markets 360
    und der Trend-Screener sind eigene, unabhaengige Scoring-Engines."""
    if typ.startswith("finviz"):
        return "finviz"
    if typ == "markets360":
        return "markets360"
    if typ == "trendscreener":
        return "trendscreener"
    return "traderfox"

def f_smart_money(closes, volumes):
    """Akkumulation/Distribution ueber das Up/Down-Volumen-Verhaeltnis (Minervini U/D):
    Volumen an gruenen Tagen vs. roten Tagen, letzte ~50 Handelstage.
    >1 = Kaeufe in die Staerke (Smart Money akkumuliert)."""
    n = min(len(closes), len(volumes))
    if n < 30:
        return 0.0, "zu wenig Daten"
    start = max(1, n - 50)
    up = dn = 0.0
    for i in range(start, n):
        v = volumes[i] or 0
        if closes[i] > closes[i - 1]:
            up += v
        elif closes[i] < closes[i - 1]:
            dn += v
    ud = up / dn if dn else (2.5 if up else 1.0)
    wert = clamp((ud - 0.8) / (2.0 - 0.8))
    return wert, f"U/D-Vol {ud:.2f} (50T, >1=Akkumulation)"

def f_gap80(closes, volumes, opens, cfg):
    """Minervini 50/80-Regel: nach einem scharfen Gap-down auf hohem Volumen
    (typischerweise eine Erdenttaeuschung) hat eine Aktie laut Minervini eine
    hohe Wahrscheinlichkeit, innerhalb der folgenden ~8 Monate noch einmal
    unter das Gap-Tief zu fallen - deshalb nicht als "billig" nachkaufen,
    sondern fuer diese Zeit meiden. Sucht den juengsten Tag im Fenster mit
    Gap (Vortages-Close -> Open) <= Schwelle UND Volumen >= Faktor x Ø50-Vol.
    Eine einzelne Aktie kann an einem Tag zweistellig einbrechen, ein ganzer
    Index praktisch nie (nur Ereignisse wie 1987/2008/2020) - die Schwelle
    wirkt dadurch schon als Einzeltitel- vs. Markt-Crash-Filter, ganz ohne
    Abgleich mit historischen Earnings-Terminen (die liegen uns nicht vor,
    `earnings_info` kennt nur den naechsten Termin).
    Malus klingt linear ab: kurz nach dem Gap staerkster Abzug, am Fensterende
    (Standard 168 Handelstage ≈ 8 Monate) wieder neutral (wert=1.0)."""
    if not cfg.get("aktiv", True):
        return 1.0, "50/80-Filter deaktiviert", None
    schwelle = cfg.get("schwelle_pct", -10.0)
    vol_faktor = cfg.get("volumen_faktor", 1.5)
    fenster = cfg.get("fenster_handelstage", 168)
    n = min(len(closes), len(volumes), len(opens))
    if n < 60:
        return 1.0, "zu wenig Historie", None
    fenster = min(fenster, n - 1)
    start = max(1, n - fenster)
    treffer_idx = None
    for i in range(start, n):
        prevclose = closes[i - 1]
        if not prevclose:
            continue
        avg50 = sum(volumes[max(0, i - 50):i]) / len(volumes[max(0, i - 50):i]) if i > 10 else None
        if not avg50:
            continue
        gap_pct = (opens[i] - prevclose) / prevclose * 100
        vol_ratio = volumes[i] / avg50
        if gap_pct <= schwelle and vol_ratio >= vol_faktor:
            treffer_idx = i  # letzter Treffer im Fenster gewinnt (juengster zaehlt)
    if treffer_idx is None:
        return 1.0, f"kein 50/80-Warnsignal in den letzten {fenster} Handelstagen", None
    prevclose = closes[treffer_idx - 1]
    avg50 = sum(volumes[max(0, treffer_idx - 50):treffer_idx]) / len(volumes[max(0, treffer_idx - 50):treffer_idx])
    gap_pct = (opens[treffer_idx] - prevclose) / prevclose * 100
    vol_ratio = volumes[treffer_idx] / avg50
    handelstage_seit = n - 1 - treffer_idx
    wochen_seit = round(handelstage_seit / 5)
    wert = clamp(0.2 + 0.8 * handelstage_seit / fenster)
    info = {"gap_pct": round(gap_pct, 1), "vol_ratio": round(vol_ratio, 2),
            "handelstage_seit": handelstage_seit, "wochen_seit": wochen_seit,
            "fenster_handelstage": fenster}
    detail = (f"Gap {gap_pct:.0f}% auf {vol_ratio:.1f}x Volumen vor {wochen_seit} Wochen "
              f"(Minervini 50/80: Vorsicht bis ~{round(fenster/21)} Monate danach)")
    return wert, detail, info

def f_cmf(highs, lows, closes, volumes, n=21):
    """Chaikin Money Flow (21): Intraday-Akkumulation/Distribution.
    Schliesst hoch im Tagesbereich + Volumen -> Geld fliesst rein (>0)."""
    m = min(len(highs), len(lows), len(closes), len(volumes))
    if m < n + 1:
        return 0.0, "keine High/Low-Daten"
    mfv = vol = 0.0
    for i in range(m - n, m):
        h, l, c, v = highs[i], lows[i], closes[i], volumes[i]
        rng = h - l
        mfm = ((c - l) - (h - c)) / rng if rng > 0 else 0.0
        mfv += mfm * v
        vol += v
    cmf = mfv / vol if vol else 0.0
    wert = clamp((cmf + 0.05) / (0.20 + 0.05))
    return wert, f"CMF(21) {cmf:+.2f} (>0=Akkumulation)"

# 11 SPDR Select Sector ETFs (S&P-500-Sektoren; Symbole am 2026-07-24 gegen
# sectorspdrs.com/Yahoo verifiziert). Vom Kandidatenpool UNABHAENGIGE
# Messlatte fuer die Branchenstaerke. Schluessel = Substring-Match (lower)
# gegen die Sektor-Namen aus Nasdaq-Profil ("Finance", "Telecommunications")
# und Yahoo quoteSummary ("Financial Services", "Communication Services").
SEKTOR_ETF = {
    "technology": "XLK",
    "financial": "XLF", "finance": "XLF",
    "health": "XLV",
    "consumer cyclical": "XLY", "consumer discretionary": "XLY",
    "consumer defensive": "XLP", "consumer staples": "XLP",
    "energy": "XLE",
    "industrial": "XLI",
    "basic materials": "XLB", "materials": "XLB",
    "utilities": "XLU",
    "real estate": "XLRE",
    "communication": "XLC", "telecommunication": "XLC",
}

def sektor_zu_etf(sektor):
    s = (sektor or "").lower()
    if not s:
        return None
    for schluessel, etf in SEKTOR_ETF.items():
        if schluessel in s:
            return etf
    return None

def sektor_etf_ranking(cache):
    """Rangliste der 11 Sektor-ETFs nach gewichteter Multi-Fenster-RS
    (rs_gewichtet, dieselbe Formel wie bei den Aktien). Liefert je ETF
    {perzentil 0..1, rang, n, rs} - 11 zusaetzliche, tages-gecachte
    Yahoo-Abrufe, sonst nichts."""
    staerken = {}
    for etf in sorted(set(SEKTOR_ETF.values())):
        d = hole_chart_cached(etf, cache)
        s = rs_gewichtet(d["closes"]) if d else None
        if s is not None:
            staerken[etf] = s
    sortiert = sorted(staerken.values())
    n = len(sortiert)
    out = {}
    for etf, s in staerken.items():
        rang = sum(1 for x in sortiert if x <= s)
        out[etf] = {"perzentil": rang / n, "rang": rang, "n": n, "rs": round(s, 3)}
    return out

def f_rs_pool_rang(ergebnisse, gew, gew_summe, schwellen):
    """Post-Pass fuer die relative Staerke: mischt den absoluten 0..1-Wert
    aus dem Haupt-Loop 50/50 mit dem Perzentil-Rang der gewichteten RS im
    aktuellen Kandidatenpool (IBD/Minervini: RS ist ein Rang). Der absolute
    Anteil bleibt drin, damit ein schwacher Pool nicht automatisch starke
    Werte vortaeuscht. Score wird - wie bei der Branchenstaerke - nur um
    das Delta aus UNGERUNDETEN Werten angepasst (siehe Rundungs-Hinweis in
    f_sektor_staerke)."""
    werte = sorted(e["rs_gewichtet"] for e in ergebnisse if e.get("rs_gewichtet") is not None)
    n = len(werte)
    if n < 10:      # zu kleiner Pool -> Rang nicht belastbar, absolut belassen
        for e in ergebnisse:
            e.pop("_rs_wert", None)
        return
    import bisect
    for e in ergebnisse:
        x = e.get("rs_gewichtet")
        alt = e.pop("_rs_wert", None)
        if x is None or alt is None:
            continue
        perz = bisect.bisect_right(werte, x) / n
        neu = clamp(0.5 * alt + 0.5 * perz)
        fak = e["faktoren"]["relative_staerke"]
        fak["wert"] = round(neu, 2)
        fak["ampel"] = ampel(neu)
        fak["detail"] += f", Pool-Rang {perz*100:.0f}% ({n} Werte)"
        delta = gew.get("relative_staerke", 0) * (neu - alt) / gew_summe * 100
        score = e["score"] + delta
        e["score"] = round(score, 1)
        e["tier"] = ("A" if score >= schwellen["kauf_kandidat"]
                     else "B" if score >= schwellen["beobachten"] else "C")
        e["einstufung"] = ("Kauf-Kandidat" if score >= schwellen["kauf_kandidat"]
                            else "Beobachten" if score >= schwellen["beobachten"] else "—")

def f_sektor_staerke(ergebnisse, etf_rang, gew, gew_summe, schwellen):
    """Branchenstaerke gegen den GANZEN Markt statt (wie bis 2026-07-24) nur
    als Rangfolge innerhalb des eigenen Kandidatenpools: Massstab ist der
    Rang des zugehoerigen SPDR-Sektor-ETFs unter allen 11 Sektoren (nach
    derselben gewichteten Multi-Fenster-RS wie bei den Aktien). Der Pool
    ist bereits momentum-selektiert - ein pool-interner Rang sagte deshalb
    wenig aus. Minervini: erst die fuehrende Gruppe, dann der Marktfuehrer
    darin - den Marktfuehrer-Teil uebernimmt seit dem Umbau der
    Pool-Perzentil-Anteil in f_rs_pool_rang(). EU-Werte nutzen dieselben
    US-Sektor-ETFs als Naeherung (Sektortrends korrelieren global).
    Sektor unbekannt/nicht zuordenbar -> neutral 0.5."""
    for e in ergebnisse:
        etf = sektor_zu_etf(e.get("sektor"))
        info = etf_rang.get(etf) if etf else None
        if not info:
            wert = 0.5
            detail = ("Sektor unbekannt/keinem Sektor-ETF zuordenbar"
                      if not etf else f"keine Kursdaten fuer {etf}")
        else:
            wert = clamp(info["perzentil"])
            detail = (f"Sektor '{e.get('sektor')}' ({etf}): Rang {info['rang']}/{info['n']} "
                      f"aller Sektor-ETFs nach 3-12M-RS")
        e["faktoren"]["sektor_staerke"] = {
            "wert": round(wert, 2), "ampel": ampel(wert),
            "gewicht": gew.get("sektor_staerke", 0), "detail": detail}

        # Nur die Differenz zum Platzhalter (0.5) einrechnen, nicht den ganzen
        # Score aus den (auf 2 Nachkommastellen gerundeten) Anzeige-Werten neu
        # aufsummieren - sonst verschiebt sich der Score durch Rundung, obwohl
        # das Gewicht bei 0 unveraendert keinen Effekt haben darf.
        delta = gew.get("sektor_staerke", 0) * (wert - 0.5) / gew_summe * 100
        score = e["score"] + delta
        e["score"] = round(score, 1)
        e["tier"] = ("A" if score >= schwellen["kauf_kandidat"]
                     else "B" if score >= schwellen["beobachten"] else "C")
        e["einstufung"] = ("Kauf-Kandidat" if score >= schwellen["kauf_kandidat"]
                            else "Beobachten" if score >= schwellen["beobachten"] else "—")

def f_marktampel_dynamik(ergebnisse, regime, regime_cfg, schwellen):
    """Post-Pass: koppelt die Marktampel (markt_regime()) an den Einzel-Score,
    statt sie nur informativ nach signals.json zu schreiben. Minervini/
    Weinstein: der Markt entscheidet mit, ob ueberhaupt gekauft werden sollte
    - in einem schwachen Markt scheitern auch saubere Einzelsetups
    ueberproportional oft. Anders als die uebrigen Post-Pass-Faktoren ist das
    kein einzelner 0..1-Faktor mit Gewicht, sondern eine Umfeld-Bedingung fuer
    die GESAMTE Bewertung - daher multiplikative Daempfung des fertigen
    Scores statt eines Faktor-Deltas. "unbekannt" (zu wenig Index-Historie)
    daempft nicht - fehlende Daten duerfen nicht bestrafen."""
    if not regime_cfg.get("score_daempfung_aktiv", True):
        return
    faktoren = {"gelb": regime_cfg.get("score_gelb_faktor", 0.92),
                "rot": regime_cfg.get("score_rot_faktor", 0.80)}
    for e in ergebnisse:
        info = regime.get(e.get("markt")) or {}
        a = info.get("ampel", "unbekannt")
        fak = faktoren.get(a, 1.0)
        e["marktampel"] = {"ampel": a, "faktor": fak, "hinweis": info.get("hinweis", "")}
        if fak == 1.0:
            continue
        score = round(e["score"] * fak, 1)
        e["score"] = score
        e["tier"] = ("A" if score >= schwellen["kauf_kandidat"]
                     else "B" if score >= schwellen["beobachten"] else "C")
        e["einstufung"] = ("Kauf-Kandidat" if score >= schwellen["kauf_kandidat"]
                            else "Beobachten" if score >= schwellen["beobachten"] else "—")

def f_konsens(gew_konsens, n_provider):
    # gew_konsens basiert auf unabhaengigen Anbietern (je Anbieter gedeckelt),
    # nicht auf der blossen Zahl der Nennungen.
    wert = clamp(0.25 + 0.16 * (gew_konsens - 1))
    return wert, f"{gew_konsens:.1f} (Konsens, {n_provider} unabh. Quelle{'n' if n_provider != 1 else ''})"

def ampel(wert):
    return "gruen" if wert >= 0.66 else ("gelb" if wert >= 0.33 else "rot")

def _sma_reihe(closes, n, fenster):
    """SMA(n) fuer die letzten `fenster` Punkte (None wenn zu wenig Historie)."""
    L = len(closes)
    out = []
    for i in range(L - fenster, L):
        if i - n + 1 < 0:
            out.append(None)
        else:
            out.append(round(sum(closes[i - n + 1:i + 1]) / n, 2))
    return out

def _chartdaten(closes, volumes, opens=None, highs=None, lows=None, fenster=126):
    """Mini-Chart-Daten fuer ~6 Monate: seit 2026-07-24 volles OHLC (Kerzen-
    Darstellung im Dashboard wie im Price-Action-Hub) + Volumen + SMA50/150/200.
    o/h/l sind optional - fehlen sie, rendert das Dashboard weiter die
    Linien-Variante (Abwaertskompatibilitaet alter signals.js-Staende)."""
    fenster = min(fenster, len(closes))
    c = [round(x, 2) for x in closes[-fenster:]]
    v_roh = volumes[-fenster:] if volumes else []
    v = [int(x / 1000) for x in v_roh]  # in Tausend -> kompakter
    out = {
        "c": c, "v": v,
        "s50": _sma_reihe(closes, 50, fenster),
        "s150": _sma_reihe(closes, 150, fenster),
        "s200": _sma_reihe(closes, 200, fenster),
    }
    # Kerzen brauchen alle drei Reihen in voller Fensterlaenge, sonst weglassen.
    if (opens and highs and lows
            and len(opens) >= fenster and len(highs) >= fenster and len(lows) >= fenster):
        out["o"] = [round(x, 2) for x in opens[-fenster:]]
        out["h"] = [round(x, 2) for x in highs[-fenster:]]
        out["l"] = [round(x, 2) for x in lows[-fenster:]]
    return out

# ---------------------------------------------------------------------------
# Roh-Signale laden + pro Ticker aggregieren
# ---------------------------------------------------------------------------
def lade_rohsignale():
    alle = []
    for p in (pfade.RAW_PDF, pfade.RAW_MAIL, pfade.RAW_FINVIZ,
              pfade.RAW_MARKETS360, pfade.RAW_TRENDSCREENER):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                alle.extend(json.load(f))
    return alle

def lade_symbolcache():
    if os.path.exists(SYMBOL_CACHE_PFAD):
        try:
            return json.load(open(SYMBOL_CACHE_PFAD, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _billiges_symbol(s):
    """Symbol ohne Netzwerk: echter US-Ticker, EU-Ticker mit Boersen-Suffix
    (SAP.DE, VOW3.DE, MC.PA ...) oder kuratierte Map. Markets 360 und der
    Trend-Screener liefern bereits validierte Yahoo-Symbole inkl. Suffix."""
    t = (s.get("ticker") or "").upper()
    if t and re.fullmatch(r"[A-Z][A-Z0-9]{0,5}(\.[A-Z]{1,3})?", t):
        return t
    return ticker_resolver.guess_ticker(s.get("name") or "")

def resolve_gruppen(rohsignale, sym_cache):
    """Ordnet jedem Rohsignal ein Yahoo-Symbol zu und gruppiert danach.
    Teure Yahoo-Suche nur fuer name-only Werte, die in >=2 Ausgaben vorkommen."""
    gruppen = defaultdict(lambda: {"symbol": None, "eintraege": []})
    offen = []
    for s in rohsignale:
        sym = _billiges_symbol(s)
        if sym:
            gruppen[sym]["symbol"] = sym
            gruppen[sym]["eintraege"].append(s)
        else:
            offen.append(s)

    ausgaben_pro_name = defaultdict(set)
    for s in offen:
        ausgaben_pro_name[(s.get("name") or "").lower()].add(s.get("quelle_datei"))

    gesucht = 0
    for s in offen:
        nm = (s.get("name") or "").lower()
        if len(ausgaben_pro_name[nm]) < 2:
            continue  # zu schwach fuer eine Yahoo-Suche
        sym = ticker_resolver.resolve(None, s.get("name"), sym_cache)
        if sym:
            gruppen[sym]["symbol"] = sym
            gruppen[sym]["eintraege"].append(s)
            gesucht += 1
    return gruppen

# ---------------------------------------------------------------------------
# Hauptbewertung
# ---------------------------------------------------------------------------
def score_alle(limit=None):
    cfg = json.load(open(CONFIG_PFAD, encoding="utf-8"))
    gew = cfg["score_gewichte"]
    gew_summe = sum(gew.values()) or 1
    schwellen = cfg["score_schwellen"]
    maerkte = cfg["maerkte"]
    mailgew = cfg.get("mail_signal_gewichte", {})
    earn_cfg = cfg.get("earnings", {})
    gap80_cfg = cfg.get("minervini_5080", {})
    depot_cfg = cfg.get("depotabgleich", {})
    frische_cfg = cfg.get("frische_gewichtung", {})

    roh = lade_rohsignale()
    sym_cache = lade_symbolcache()
    gruppen = resolve_gruppen(roh, sym_cache)
    with open(SYMBOL_CACHE_PFAD, "w", encoding="utf-8") as fp:
        json.dump(sym_cache, fp, ensure_ascii=False)
    # nach Konsens sortieren, ggf. begrenzen
    rang = sorted(gruppen.values(),
                  key=lambda g: len({e["quelle_datei"] for e in g["eintraege"]}),
                  reverse=True)
    if limit:
        rang = rang[:limit]

    cache = lade_cache()
    # Indizes vorab holen + Markt-Regime-Ampel je Markt (keine neuen Abrufe)
    idx_daten = {}
    regime = {}
    for markt, m in maerkte.items():
        if m.get("aktiv"):
            d = hole_chart_cached(m["index_yahoo"], cache)
            idx_daten[markt] = d["closes"] if d else []
            regime[markt] = markt_regime(idx_daten[markt])
    if regime:
        print("Markt-Regime: " + " · ".join(
            f"{k} {v['ampel']} ({v.get('detail', '')})" for k, v in regime.items()))

    # Earnings-Kalender (Nasdaq, US) einmal laden
    earn_kal = lade_earnings_kalender(earn_cfg.get("warn_tage", 10)) if earn_cfg.get("aktiv") else {}
    if earn_kal:
        print(f"Earnings-Kalender (US): {len(earn_kal)} Termine geladen")

    # Sektor/Branche-Cache, Depot, Yahoo-Crumb (fuer EU-Sektor/Earnings), Frische
    profil_cache = lade_profil_cache()
    profil_neu = 0
    depot_aktiv = depot_cfg.get("aktiv") or bool(os.environ.get("SIGNALHUB_DEPOT_NAMEN"))
    depot_namen = lade_depot_namen(depot_cfg.get("datei", "../mts_data.json")) if depot_aktiv else []
    if depot_namen:
        print(f"Depot-Abgleich: {len(depot_namen)} offene Positionen")
    yop, ycrumb = yahoo_crumb()
    print("Yahoo-Crumb fuer EU-Sektor/Earnings:", "ok" if ycrumb else "nicht verfuegbar (EU-Sektor/Earnings entfallen ggf.)")
    frische_aktiv = bool(frische_cfg.get("aktiv"))
    halbwert = frische_cfg.get("halbwertszeit_tage", 10)

    prefetch_charts_parallel([g["symbol"] for g in rang], cache)

    ergebnisse = []
    print(f"Bewerte {len(rang)} Ticker ...")
    for i, g in enumerate(rang, 1):
        symbol = g["symbol"]
        if not symbol:
            continue
        chart = hole_chart_cached(symbol, cache)
        if not chart:
            continue  # ungueltig -> raus (filtert Abkuerzungs-Muell)
        ticker = symbol
        rohname = next((e["name"] for e in g["eintraege"] if e.get("name")), None)

        meta = chart["meta"]
        closes, volumes = chart["closes"], chart["volumes"]
        highs, lows = chart.get("highs") or [], chart.get("lows") or []
        markt = markt_von_meta(meta)
        idx_closes = idx_daten.get(markt) or idx_daten.get("USA") or []
        hi52 = meta.get("fiftyTwoWeekHigh") or max(closes[-252:])
        lo52 = meta.get("fiftyTwoWeekLow") or min(closes[-252:])
        basis_nr = basis_anzahl(closes)
        extended = extended_pct(closes)
        short_kandidat = ist_short_setup(closes)

        ausgaben = len({e["quelle_datei"] for e in g["eintraege"]})
        typen = sorted({e["quelle_typ"] for e in g["eintraege"]})
        issue_info = {}
        for e in g["eintraege"]:
            issue_info.setdefault(e["quelle_datei"], (e["quelle_typ"], e.get("datum")))
        # Konsens je UNABHAENGIGEM Anbieter sammeln, pro Anbieter deckeln
        gruppen_tiefe = {}
        for typ, dat in issue_info.values():
            w = gewicht_fuer_typ(typ, mailgew)
            if frische_aktiv:
                w *= _decay(dat, halbwert)
            pg = provider_group(typ)
            gruppen_tiefe[pg] = gruppen_tiefe.get(pg, 0.0) + w
        CAP = 3.0  # eine Quelle kann nicht unbegrenzt Konsens erzeugen
        gew_konsens = sum(min(t, CAP) for t in gruppen_tiefe.values())
        if len(gruppen_tiefe) >= 2:      # echte Unabhaengigkeit belohnen
            gew_konsens += 1.0
        n_provider = len(gruppen_tiefe)
        unabhaengige_quellen = sorted(gruppen_tiefe.keys())
        daten = [e["datum"] for e in g["eintraege"] if e.get("datum")]
        zuletzt = max(daten) if daten else None
        kontext = next((e["kontext"] for e in sorted(
            g["eintraege"], key=lambda e: e.get("datum") or "", reverse=True)
            if e.get("kontext")), "")

        faktoren = {}
        f = {}
        f["stage2_trend"], faktoren_detail_s2 = f_stage2(closes, hi52, lo52)
        f["relative_staerke"], d_rs, rs_gew = f_relative_staerke(closes, idx_closes)
        f["naehe_52w_hoch"], d_nh = f_naehe_hoch(closes, hi52)
        f["basis_konsolidierung"], d_ba = f_basis(closes)
        f["volumen_bestaetigung"], d_vo = f_volumen(closes, volumes)
        f["smart_money"], d_sm = f_smart_money(closes, volumes)
        f["cmf"], d_cmf = f_cmf(highs, lows, closes, volumes)
        f["quellen_konsens"], d_ko = f_konsens(gew_konsens, n_provider)
        f["minervini_5080"], d_50, gap80_info = f_gap80(closes, volumes, chart.get("opens") or [], gap80_cfg)
        # Klimax-Warnstufe: spaete Basis (>=3, siehe basis_anzahl()) UND frischer
        # 50/80-Gap-down zusammen sind das klassische Minervini-Topmuster -
        # kombiniert zwei bestehende Signale, kein neuer Indikator.
        klimax_warnung = bool(gap80_info and basis_nr is not None and basis_nr >= 3)
        # Platzhalter: Branchenstaerke (Sektor-ETF-Ranking) und Fundamental
        # (EPS/Umsatz, eigener Abruf nur fuer relevante Treffer) werden erst
        # nach dem Haupt-Loop nachgetragen (f_sektor_staerke/f_fundamental).
        f["sektor_staerke"] = 0.5
        f["fundamental"] = 0.5
        details = {"stage2_trend": faktoren_detail_s2, "relative_staerke": d_rs,
                   "naehe_52w_hoch": d_nh, "basis_konsolidierung": d_ba,
                   "volumen_bestaetigung": d_vo, "smart_money": d_sm,
                   "cmf": d_cmf, "quellen_konsens": d_ko, "minervini_5080": d_50,
                   "sektor_staerke": "wird nach Sektor-Zuordnung berechnet",
                   "fundamental": "wird nach der Bewertung geholt"}

        score = sum(gew[k] * f[k] for k in gew) / gew_summe * 100
        for k in f:
            # gew.get(): faktoren-Anzeige darf nicht crashen, wenn ein neuer
            # Faktor (wie sektor_staerke) im Code existiert, aber ein aelteres
            # config.json (z.B. noch nicht synches Cloud-Secret) den Schluessel
            # noch nicht kennt - dann zaehlt der Faktor einfach mit Gewicht 0.
            faktoren[k] = {"wert": round(f[k], 2), "ampel": ampel(f[k]),
                           "gewicht": gew.get(k, 0), "detail": details[k]}

        einstufung = ("Kauf-Kandidat" if score >= schwellen["kauf_kandidat"]
                      else "Beobachten" if score >= schwellen["beobachten"]
                      else "—")

        # --- Sektor/Branche, Earnings (US-Kalender + EU-Yahoo), Depot ---
        # Earnings-Status ehrlich: "termin" | "kein_termin" (geprueft, nichts im Fenster)
        # | "unbekannt" (keine Quelle erreichbar). KEIN Badge != "keine Zahlen".
        def _tage(ed):
            return (datetime.strptime(ed, "%Y-%m-%d").date() - datetime.now().date()).days
        relevant = score >= schwellen["beobachten"]
        cached = profil_cache.get(symbol) or {}
        sektor, branche = cached.get("sektor"), cached.get("branche")
        earnings_info = None
        if not earn_cfg.get("aktiv"):
            earnings_info = None
        elif markt == "USA":
            if not earn_kal:
                earnings_info = {"status": "unbekannt"}
            else:
                ed = earn_kal.get(ticker.split(".")[0])
                earnings_info = ({"status": "termin", "datum": ed, "tage": _tage(ed)} if ed
                                 else {"status": "kein_termin"})
            if relevant and sektor is None and branche is None:
                p = nasdaq_profil(ticker.split(".")[0]); time.sleep(0.15)
                if p:
                    sektor, branche = p.get("sektor"), p.get("branche")
                    profil_cache[symbol] = {"sektor": sektor, "branche": branche}; profil_neu += 1
        else:   # EU: Yahoo (Crumb) liefert Sektor+Branche+Earnings; sonst "unbekannt"
            p = yahoo_profil_earnings(symbol, yop, ycrumb) if relevant else None
            if relevant:
                time.sleep(0.15)
            if p is None:
                earnings_info = {"status": "unbekannt"}
            else:
                if sektor is None and branche is None:
                    sektor, branche = p.get("sektor"), p.get("branche")
                    profil_cache[symbol] = {"sektor": sektor, "branche": branche}; profil_neu += 1
                ed = p.get("earnings_datum")
                earnings_info = ({"status": "termin", "datum": ed, "tage": _tage(ed)}
                                 if ed and _tage(ed) >= 0 else {"status": "kein_termin"})

        im_depot = False
        if depot_namen:
            nm = meta.get("longName") or meta.get("shortName") or rohname or ticker
            im_depot = depot_match(nm, depot_namen)

        ergebnisse.append({
            "ticker": ticker,
            "yahoo_symbol": symbol,
            "name": meta.get("longName") or meta.get("shortName") or rohname or ticker,
            "markt": markt,
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "currency": meta.get("currency"),
            "preis": round(meta.get("regularMarketPrice") or closes[-1], 2),
            "score": round(score, 1),
            "tier": ("A" if score >= schwellen["kauf_kandidat"]
                     else "B" if score >= schwellen["beobachten"] else "C"),
            "einstufung": einstufung,
            "faktoren": faktoren,
            "quellen": {"ausgaben": ausgaben, "typen": typen,
                        "zuletzt": zuletzt, "kontext": kontext[:240],
                        "unabhaengig": unabhaengige_quellen},
            "earnings": earnings_info,
            "sektor": sektor,
            "branche": branche,
            "basis_nr": basis_nr,
            "extended_pct": extended,
            "minervini_5080": gap80_info,
            "klimax_warnung": klimax_warnung,
            "short_kandidat": short_kandidat,
            "im_depot": im_depot,
            "rs_gewichtet": round(rs_gew, 3) if rs_gew is not None else None,
            # unGErundeter RS-Faktorwert fuer den Pool-Rang-Post-Pass
            # (f_rs_pool_rang entfernt das Feld wieder, bleibt nicht im JSON)
            "_rs_wert": f["relative_staerke"],
            "chart": _chartdaten(closes, volumes, chart.get("opens"), highs, lows),
        })
        if i % 10 == 0:
            print(f"  ... {i}/{len(rang)}")

    # Sektor-ETF-Ranking VOR dem Cache-Speichern holen (11 gecachte Abrufe)
    etf_rang = sektor_etf_ranking(cache)
    if etf_rang:
        top3 = sorted(etf_rang.items(), key=lambda kv: -kv[1]["rang"])[:3]
        print("Sektor-ETF-Ranking (Top 3): " + " · ".join(
            f"{etf} {i['rang']}/{i['n']}" for etf, i in top3))
    speichere_cache(cache)
    if profil_neu:
        with open(PROFIL_CACHE_PFAD, "w", encoding="utf-8") as fp:
            json.dump(profil_cache, fp, ensure_ascii=False)
        print(f"Sektor/Branche: {profil_neu} neu geholt (Cache: {len(profil_cache)})")

    f_rs_pool_rang(ergebnisse, gew, gew_summe, schwellen)
    f_sektor_staerke(ergebnisse, etf_rang, gew, gew_summe, schwellen)
    f_fundamental(ergebnisse, gew, gew_summe, schwellen, yop, ycrumb)
    f_marktampel_dynamik(ergebnisse, regime, cfg.get("marktregime", {}), schwellen)
    ergebnisse.sort(key=lambda e: e["score"], reverse=True)

    # "neu" markieren: Kauf-Kandidaten, die seit dem letzten Lauf neu sind
    state_pfad = pfade.STATE
    state = {}
    if os.path.exists(state_pfad):
        try:
            state = json.load(open(state_pfad, encoding="utf-8"))
        except Exception:
            state = {}
    vorher = set(state.get("scorer_gesehen", []))
    kauf = schwellen["kauf_kandidat"]
    for e in ergebnisse:
        e["neu"] = e["score"] >= kauf and e["ticker"] not in vorher
    state["scorer_gesehen"] = [e["ticker"] for e in ergebnisse if e["score"] >= kauf]
    with open(state_pfad, "w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2)

    # Health-Status der Quellen (sichtbar im Dashboard) ------------------
    def _src(typ):
        if typ.startswith("mail"):
            return "mail"
        if typ.startswith("finviz"):
            return "finviz"
        if typ in ("markets360", "trendscreener"):
            return typ
        return "pdf"
    roh_counts = {}
    for s in roh:
        roh_counts[_src(s["quelle_typ"])] = roh_counts.get(_src(s["quelle_typ"]), 0) + 1
    status = {
        "pdf": roh_counts.get("pdf", 0),
        "mail": roh_counts.get("mail", 0),
        "finviz": roh_counts.get("finviz", 0),
        "markets360": roh_counts.get("markets360", 0),
        "trendscreener": roh_counts.get("trendscreener", 0),
        "earnings_us": len(earn_kal),
        "yahoo_eu": "ok" if ycrumb else "nicht verfuegbar",
        "bewertet": len(ergebnisse),
    }

    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anzahl": len(ergebnisse),
        "gewichte": gew,
        "schwellen": schwellen,
        "status": status,
        "marktregime": regime,
        "treffer": ergebnisse,
    }
    with open(pfade.SIGNALS_JSON, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    with open(pfade.SIGNALS_JS, "w", encoding="utf-8") as fp:  # file://-Fallback
        fp.write("window.SIGNAL_DATA = ")
        json.dump(out, fp, ensure_ascii=False)
        fp.write(";")
    import copy
    cfg_oeffentlich = copy.deepcopy(cfg)
    cfg_oeffentlich.get("server", {}).pop("token", None)  # Token nie ausliefern
    with open(pfade.CONFIG_JS, "w", encoding="utf-8") as fp:
        fp.write("window.APP_CONFIG = ")
        json.dump(cfg_oeffentlich, fp, ensure_ascii=False)
        fp.write(";")

    # Trefferquoten-Logbuch: 1x pro Tag Top-Picks festhalten (fuer spaetere Auswertung)
    heute = datetime.now().strftime("%Y-%m-%d")
    lb = []
    if os.path.exists(pfade.LOGBUCH):
        try:
            lb = json.load(open(pfade.LOGBUCH, encoding="utf-8"))
        except Exception:
            lb = []
    if not any(e.get("datum") == heute for e in lb):
        # tier mitloggen: Basis fuer score_backtest.py (Trefferquoten je Tier).
        top = [{"ticker": e["ticker"], "score": e["score"], "tier": e["tier"],
                "preis": e["preis"], "markt": e["markt"]}
               for e in ergebnisse if e["score"] >= schwellen["beobachten"]]
        lb.append({"datum": heute, "anzahl": len(top), "treffer": top})
        lb = lb[-400:]
        with open(pfade.LOGBUCH, "w", encoding="utf-8") as fp:
            json.dump(lb, fp, ensure_ascii=False)
    return out

# ---------------------------------------------------------------------------
def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    out = score_alle(limit=limit)
    print(f"\n=== Top 20 von {out['anzahl']} bewerteten Tickern ===")
    print(f"{'Ticker':7s}{'Markt':8s}{'Score':>6s}  Einstufung   Name")
    for e in out["treffer"][:20]:
        print(f"{e['ticker']:7s}{e['markt']:8s}{e['score']:6.1f}  "
              f"{e['einstufung']:13s}{e['name'][:30]}")
    print(f"\nGespeichert: data/signals.json + data/signals.js")

if __name__ == "__main__":
    main()
