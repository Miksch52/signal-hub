#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF-Screener fuer den Daily Signal Hub.

Liest TraderFox-Magazine (Trader-Zeitung, Megatrend, Tenbagger, Narrative Edge ...)
und extrahiert Aktien-Erwaehnungen als "Roh-Signale":
  { ticker, name, exchange, markt, quelle_typ, quelle_datei, datum, kontext, seite }

Impressum / Offenlegung werden uebersprungen (das sind Pflichtnennungen, keine Tipps).
Komplett lokal, kostenlos (pypdf). Keine Bewertung hier - die macht scorer.py.

Standalone-Test:
    python3 pdf_screener.py "<pfad-zur.pdf>"
    python3 pdf_screener.py            # nutzt Ordner aus config.json
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

try:
    import pypdf
except ImportError:
    sys.exit("pypdf fehlt. Installieren mit:  python3 -m pip install --user pypdf")

try:
    from ticker_resolver import TICKER_MAP
except ImportError:
    TICKER_MAP = {}

HIER = os.path.dirname(os.path.abspath(__file__))
import pfade
PROJEKT = pfade.PROJEKT
CONFIG_PFAD = pfade.CONFIG

# ----------------------------------------------------------------------------
# Magazin-Typ aus Dateiname ableiten
# ----------------------------------------------------------------------------
MAGAZIN_MUSTER = [
    ("trader-zeitung", re.compile(r"trader[-_ ]?zeitung", re.I)),
    ("megatrend",      re.compile(r"megatrend", re.I)),
    ("tenbagger",      re.compile(r"tenbagger", re.I)),
    ("narrative_edge", re.compile(r"narrative[_ ]?edge", re.I)),
    ("aktien-magazin", re.compile(r"aktien[-_ ]?magazin", re.I)),
    ("growth",         re.compile(r"\bgrowth\b", re.I)),
    ("trendfollowing", re.compile(r"trendfollowing", re.I)),
    ("nebenwerte",     re.compile(r"nebenwerte", re.I)),
    ("the-big-call",   re.compile(r"big[-_ ]?call", re.I)),
]

def magazin_typ(dateiname):
    for typ, muster in MAGAZIN_MUSTER:
        if muster.search(dateiname):
            return typ
    return "sonstiges"

# ----------------------------------------------------------------------------
# Datum aus Dateiname ziehen (mehrere Formate)
# ----------------------------------------------------------------------------
def datum_aus_name(dateiname):
    # 2026-05-08  oder  20260508
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", dateiname)
    if m:
        try:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        except ValueError:
            pass
    return None

# ----------------------------------------------------------------------------
# Seiten erkennen, die uebersprungen werden (Impressum / Offenlegung / Werbung)
# ----------------------------------------------------------------------------
AUSSCHLUSS_MARKER = re.compile(
    r"IMPRESSUM|Herausgeber|Offenlegung|Interessenkonflikt|Haftungsausschluss|"
    r"Risikohinweis|Datenschutz|Vervielf|Disclaimer",
    re.I,
)

def seite_ausschliessen(text):
    treffer = AUSSCHLUSS_MARKER.findall(text)
    # nur ausschliessen, wenn es wirklich eine Rechtsseite ist (mehrere Marker)
    return len(treffer) >= 2

# ----------------------------------------------------------------------------
# Ticker-Erkennung
# ----------------------------------------------------------------------------
# 1) Boersen-qualifiziert:  (NASDAQ: MCHP)  (NYSE: XYZ)  (ETR: ADS)
EXCHANGE_TICKER = re.compile(
    r"\((NASDAQ|NYSE|NYSEARCA|AMEX|OTC|XETRA|ETR|FRA|FWB|LSE|LON|SIX|EPA|BME|MIL|AMS|STO|CPH|HEL|OSL)"
    r"\s*[:\s]\s*([A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?)\)"
)

# 2) Blanker Ticker in Klammern direkt hinter Firmenname:  Kulicke & Soffa (KLIC)
NAME_TICKER = re.compile(
    r"([A-ZÄÖÜ][\wÄÖÜäöüß&.\-]+(?:[ ][A-ZÄÖÜ0-9][\wÄÖÜäöüß&.\-]+){0,4})\s*\(([A-Z]{1,5})\)"
)

# 3) ISIN / WKN (fuer Pivotal-Points-Tabellen)
ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")

# 4) Ueberschrift "FIRMENNAME: ..." (GROSSBUCHSTABEN, vor Doppelpunkt) - faengt
#    v.a. EU-Werte ohne Klammer-Ticker (SIEMENS:, AMS-OSRAM:, MERCADOLIBRE:)
HEADLINE = re.compile(
    r"\b([A-ZÄÖÜ][A-ZÄÖÜ0-9&.\-]{2,}(?:[ ]+[A-ZÄÖÜ0-9&.\-]{2,}){0,3})\s*:"
)
# Abschnitts-Ueberschriften, die KEINE Firmen sind
SEKTION_STOP = {
    "MARKTBERICHT", "TOP", "TOP-STORYS", "TOPSTORYS", "MEINUNG", "MEINUNGEN", "INHALT",
    "INHALTE", "TRADING-ROOM", "TRADING-DESK", "TRADING", "IMPRESSUM", "EDITORIAL",
    "KOLUMNE", "NEWS", "FAZIT", "ANALYSE", "CHARTANALYSE", "WATCHLIST", "AKTIE",
    "SYMBOL", "WKN", "ISIN", "FOTO", "TEXT", "QUELLE", "STAND", "SEITE", "SEITEN",
    "ACHTUNG", "HINWEIS", "TIPP", "FAZIT", "PIVOTAL", "POINTS", "USA", "EUROPA",
    "DEUTSCHLAND", "MEINUNG VON", "STORY", "STORYS", "BIG", "CALL", "THE", "DER",
    "DIE", "DAS", "MEGATREND", "MEGATRENDS", "DEPOT", "PORTFOLIO", "ZUSAMMENFASSUNG",
    "NASDAQ", "NYSE", "AMEX", "XETRA", "IMAGE", "FOTOS", "BILD", "GRAFIK",
    "ÖSTERREICH", "SCHWEIZ", "CHINA", "INDIEN", "JAPAN", "FRANKREICH", "ITALIEN",
    "SPANIEN", "GROSSBRITANNIEN", "EUROPA", "ASIEN", "TRADING-PLAN", "UPDATE",
    "INTERVIEW", "VIDEO", "PODCAST", "WEBINAR", "SPECIAL", "EXKLUSIV", "PREMIUM",
    "GRATIS", "KAUFEN", "VERKAUFEN", "HALTEN", "GASTBEITRAG", "BEISPIEL", "TABELLE",
    "ABBILDUNG", "CHART", "CHARTS", "WOCHE", "MONAT", "JAHR", "HEUTE", "MORGEN",
    "BREAKING", "EILMELDUNG", "KOMMENTAR", "AUSBLICK", "RUECKBLICK", "RÜCKBLICK",
}

# Falsch-Positive: Grossbuchstaben-Abkuerzungen, die KEINE Ticker sind
KEIN_TICKER = {
    "CALL", "PUT", "USA", "EU", "KI", "AI", "CEO", "CFO", "COO", "CTO", "ETF",
    "HBM", "TCB", "MCU", "USD", "EUR", "GBP", "CHF", "GAP", "KO", "WKN", "ISIN",
    "RSI", "SMA", "EMA", "EPS", "KGV", "KUV", "IPO", "FED", "EZB", "BIP", "GDP",
    "ROE", "ROI", "FCF", "RD", "IT", "KW", "NL", "AG", "SE", "INC", "LLC", "LTD",
    "CORP", "GMBH", "PLC", "NV", "SA", "SPA", "ESG", "API", "SaaS", "B2B", "B2C",
    "Q1", "Q2", "Q3", "Q4", "YOY", "QOQ", "ATH", "ATL", "DAX", "MDAX", "SDAX",
    "SP", "DOW", "NYSE", "NASDAQ", "ETR", "FRA", "LSE", "OK", "TOP", "NEW",
    "USP", "MOAT", "GUV", "EBIT", "EBITDA", "CAGR", "TAM", "ARR", "MRR",
    "NEU", "PER", "VON", "DER", "DIE", "DAS", "UND", "MIT", "FUER", "AUS",
    # Finanz-/Geschaefts-Abkuerzungen, die als "Begriff (ABK)" im Text definiert werden
    "AUM", "GMV", "EBT", "CDN", "CDP", "CIS", "ARPU", "ROIC", "NOPAT", "WACC",
    "CAPEX", "OPEX", "NPS", "KPI", "FCFF", "GMVS", "DCF", "LTV", "CAC", "SOTP",
    "TTM", "RPO", "ASP", "YTD", "MOM", "WOW", "GAAP", "YOY2", "BNPL", "GPU",
    "CPU", "DPU", "SOC", "IOT", "EV", "BEV", "SUV", "DRAM", "NAND", "SSD",
    "REIT", "ADR", "GDR", "FAQ", "CPO", "COGS", "SG", "VR", "AR", "ML",
}

def ist_abkuerzungs_definition(name, ticker):
    """True, wenn der Klammer-Ausdruck die Initialen der davorstehenden Woerter
    ist (z.B. 'Customer Data Platform (CDP)') -> dann KEIN Ticker."""
    woerter = [w for w in re.split(r"[ \-]", name) if w]
    initialen = "".join(w[0] for w in woerter if w[:1].isupper())
    return len(initialen) >= 2 and initialen.upper() == ticker.upper()

# Boerse -> Markt-Zuordnung
US_BOERSEN = {"NASDAQ", "NYSE", "NYSEARCA", "AMEX", "OTC"}

def markt_aus_boerse(boerse):
    if boerse in US_BOERSEN:
        return "USA"
    return "Europa"

# ----------------------------------------------------------------------------
# Kontext (Umgebung einer Fundstelle) holen
# ----------------------------------------------------------------------------
def kontext_um(text, pos, breite=160):
    a = max(0, pos - breite)
    b = min(len(text), pos + breite)
    schnipsel = text[a:b].replace("\n", " ")
    schnipsel = re.sub(r"\s+", " ", schnipsel).strip()
    return schnipsel

# ----------------------------------------------------------------------------
# Extraktion aus beliebigem Text - genutzt von PDF UND E-Mail (mail_screener)
# ----------------------------------------------------------------------------
def extrahiere_aus_text(text, quelle_typ, quelle_datei, datum, seite=None, gesehen=None):
    if gesehen is None:
        gesehen = set()
    signale = []
    if not text or not text.strip():
        return signale

    # (1) Boersen-qualifizierte Ticker: (NASDAQ: MCHP)
    for m in EXCHANGE_TICKER.finditer(text):
        boerse, ticker = m.group(1).upper(), m.group(2).upper()
        k = (ticker, seite)
        if k in gesehen:
            continue
        gesehen.add(k)
        signale.append({"ticker": ticker, "name": None, "exchange": boerse,
            "markt": markt_aus_boerse(boerse), "quelle_typ": quelle_typ,
            "quelle_datei": quelle_datei, "datum": datum, "seite": seite,
            "kontext": kontext_um(text, m.start()), "fund_art": "exchange_ticker"})

    # (2) Firmenname (Ticker) - blanke Klammer
    for m in NAME_TICKER.finditer(text):
        name, ticker = m.group(1).strip(), m.group(2).upper()
        if ticker in KEIN_TICKER or len(name) < 3 or name.upper() in KEIN_TICKER:
            continue
        if ist_abkuerzungs_definition(name, ticker):
            continue
        k = (ticker, seite)
        if k in gesehen:
            continue
        gesehen.add(k)
        signale.append({"ticker": ticker, "name": name, "exchange": None, "markt": None,
            "quelle_typ": quelle_typ, "quelle_datei": quelle_datei, "datum": datum,
            "seite": seite, "kontext": kontext_um(text, m.start()), "fund_art": "name_ticker"})

    # (3) Bekannte Namen aus der Map (v.a. EU-Werte)
    tl = text.lower()
    getroffen = [key for key in TICKER_MAP if re.search(r"\b" + re.escape(key) + r"\b", tl)]
    getroffen = [key for key in getroffen if not any(key != o and key in o for o in getroffen)]
    for key in getroffen:
        k = ("MAP:" + key, seite)
        if k in gesehen:
            continue
        gesehen.add(k)
        signale.append({"ticker": None, "name": key, "exchange": None, "markt": None,
            "quelle_typ": quelle_typ, "quelle_datei": quelle_datei, "datum": datum,
            "seite": seite, "kontext": kontext_um(text, tl.find(key)), "fund_art": "map_name"})

    # (4) Ueberschriften-Firmennamen (EU ohne Klammer-Ticker: SIEMENS:, AMS-OSRAM:)
    for m in HEADLINE.finditer(text):
        name = m.group(1).strip(" .-")
        up = name.upper()
        if up in SEKTION_STOP or len(name) < 4:
            continue
        if any(w.upper() in SEKTION_STOP for w in name.split()):
            continue
        if not re.search(r"[AEIOUÄÖÜaeiouäöü]", name):
            continue
        k = ("HL:" + up, seite)
        if k in gesehen:
            continue
        gesehen.add(k)
        signale.append({"ticker": None, "name": name, "exchange": None, "markt": None,
            "quelle_typ": quelle_typ, "quelle_datei": quelle_datei, "datum": datum,
            "seite": seite, "kontext": kontext_um(text, m.start()), "fund_art": "headline"})

    return signale

# ----------------------------------------------------------------------------
# Eine PDF auswerten
# ----------------------------------------------------------------------------
def screene_pdf(pfad):
    dateiname = os.path.basename(pfad)
    typ = magazin_typ(dateiname)
    datum = datum_aus_name(dateiname)
    signale = []
    gesehen = set()  # pro PDF geteilt; Key enthaelt Seite -> selber Wert auf anderer Seite ok
    try:
        leser = pypdf.PdfReader(pfad)
    except Exception as e:
        print(f"  ! Konnte {dateiname} nicht lesen: {e}", file=sys.stderr)
        return signale
    for seiten_nr, seite in enumerate(leser.pages, start=1):
        try:
            text = seite.extract_text() or ""
        except Exception:
            continue
        if not text.strip() or seite_ausschliessen(text):
            continue
        signale.extend(extrahiere_aus_text(text, typ, dateiname, datum,
                                           seite=seiten_nr, gesehen=gesehen))
    return signale

# ----------------------------------------------------------------------------
# Ordner / Config-gesteuert
# ----------------------------------------------------------------------------
def lade_config():
    with open(CONFIG_PFAD, encoding="utf-8") as f:
        return json.load(f)

def finde_pdfs(ordner, max_alter_tage):
    grenze = datetime.now() - timedelta(days=max_alter_tage)
    treffer = []
    for name in os.listdir(ordner):
        if not name.lower().endswith(".pdf"):
            continue
        pfad = os.path.join(ordner, name)
        mtime = datetime.fromtimestamp(os.path.getmtime(pfad))
        d = datum_aus_name(name)
        ist_neu = mtime >= grenze
        if d:
            try:
                ist_neu = ist_neu or datetime.strptime(d, "%Y-%m-%d") >= grenze
            except ValueError:
                pass
        if ist_neu:
            treffer.append(pfad)
    return sorted(treffer)

def screene_ordner(cfg):
    pdfcfg = cfg["quellen"]["pdf"]
    ordner = pdfcfg["ordner"]
    if not os.path.isdir(ordner):
        # Lokaler iCloud-Ordner (Mac-spezifisch, aus config.json) existiert
        # hier nicht -> Cloud-Lauf: R2-Mirror unter _magazine/ verwenden (siehe
        # markets360_screener.py/trendscreener_screener.py, gleiches Muster).
        ordner = pfade.EXTERN_PDF_ORDNER
    if not os.path.isdir(ordner):
        print(f"PDF-Quelle nicht gefunden ({ordner}) - uebersprungen.")
        return []
    pdfs = finde_pdfs(ordner, pdfcfg.get("max_alter_tage", 21))
    prio = pdfcfg.get("magazine_prioritaet", [])
    alle = []
    for pfad in pdfs:
        typ = magazin_typ(os.path.basename(pfad))
        if prio and typ not in prio and typ != "sonstiges":
            continue
        sig = screene_pdf(pfad)
        if sig:
            print(f"  + {os.path.basename(pfad)[:50]:50s} [{typ}]  {len(sig)} Treffer")
        alle.extend(sig)
    return alle

# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    if len(sys.argv) > 1:
        ziel = sys.argv[1]
        if os.path.isdir(ziel):
            cfg = lade_config()
            cfg["quellen"]["pdf"]["ordner"] = ziel
            signale = screene_ordner(cfg)
        else:
            signale = screene_pdf(ziel)
    else:
        cfg = lade_config()
        signale = screene_ordner(cfg)

    # Zusammenfassung
    print(f"\n=== {len(signale)} Roh-Signale ===")
    von_ticker = {}
    for s in signale:
        schluessel = s.get("ticker") or ("~" + (s.get("name") or "?"))
        von_ticker.setdefault(schluessel, []).append(s)
    rangliste = sorted(von_ticker.items(), key=lambda kv: len(kv[1]), reverse=True)
    for schluessel, eintraege in rangliste[:30]:
        name = next((e["name"] for e in eintraege if e.get("name")), "")
        quellen = ", ".join(sorted({e["quelle_typ"] for e in eintraege}))
        print(f"  {str(schluessel)[:8]:8s} {name[:28]:28s} {len(eintraege)}x  [{quellen}]")

    ziel_json = pfade.RAW_PDF
    with open(ziel_json, "w", encoding="utf-8") as f:
        json.dump(signale, f, ensure_ascii=False, indent=2)
    print(f"\nGespeichert: {ziel_json}")

if __name__ == "__main__":
    main()
