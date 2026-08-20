#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Einmaliges Backfill der OHLC-Tageshistorie fuer den Zeitraum VOR dem ersten
echten Snapshot (seit 2026-08-20 laeuft src/ohlc_history.py taeglich mit,
siehe dessen Docstring/Top-Setups-Roadmap Punkt 4/6).

Deckt die eindeutigen Ticker ab, die irgendwann im Pivot-Logbuch
(_state/pivot_logbuch.json, seit 2026-07-02) aufgetaucht sind - NICHT das
komplette taegliche Signal-Hub-Universum rueckwirkend (welche Ticker an
einem vergangenen Tag ueberhaupt gescreent wurden, ist nicht mehr
rekonstruierbar). Fuer den Zweck von Punkt 6 (Forward-Performance
historischer Pivot-Signale unverzerrt auswerten, ohne Reset auf "Signale ab
Einfuehrung des Snapshots") ist das der richtige, vollstaendige Datensatz.

Holt pro Ticker range=2y/interval=1d direkt von Yahoo (dieselbe Rohquelle
wie scorer.py::yahoo_chart, hier aber mit Zeitstempeln statt nur den
letzten 126 Tagen - Yahoo liefert diese ohnehin in jeder Antwort mit,
scorer.py wirft sie nur bisher weg), baut daraus Tages-Snapshots im
GLEICHEN Format wie ohlc_history.py (ticker,close,volume) und laedt sie
nach r2:signalhub-magazine/ohlc-history/ hoch - rein additiv, ueberschreibt
keine bereits vorhandenen Tagesdateien (siehe --bis-Default).

Einmaliger, manueller Lauf - bewusst NICHT Teil von run.py::pipeline()
(anders als ohlc_history.py, das taeglich automatisch laeuft).

Aufruf:
  python3 scripts/backfill_ohlc_history.py                 # voller Umfang
  python3 scripts/backfill_ohlc_history.py --limit 10       # Testlauf
  python3 scripts/backfill_ohlc_history.py --ab 2026-07-15  # anderer Start
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "src"))
import pfade

UA = {"User-Agent": "Mozilla/5.0"}


def _http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def yahoo_chart_mit_datum(symbol):
    """Wie scorer.py::yahoo_chart, aber mit Datum je Kurspunkt statt nur der
    letzten 126 Werte - fuer den Rueckblick brauchen wir die Zuordnung."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=2y&interval=1d")
    try:
        d = _http_json(url)
    except Exception:
        return None
    res = d.get("chart", {}).get("result")
    if not res:
        return None
    r = res[0]
    ts = r.get("timestamp") or []
    q = r.get("indicators", {}).get("quote", [{}])[0]
    closes, vols = q.get("close") or [], q.get("volume") or []
    out = {}
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else None
        if c is None:
            continue
        d_str = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        out[d_str] = (round(c, 2), int(v) if v is not None else "")
    return out


def lade_logbuch_ticker():
    r = subprocess.run(
        ["rclone", "cat", "r2:signalhub-magazine/_state/pivot_logbuch.json"],
        capture_output=True, text=True, check=True)
    eintraege = json.loads(r.stdout)
    return sorted({e["ticker"] for e in eintraege if e.get("ticker")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ab", default="2026-07-02", help="erster Handelstag (inkl.)")
    ap.add_argument("--bis", default="2026-08-19", help="letzter Handelstag (inkl.) - "
                     "bewusst VOR dem ersten echten Snapshot vom 2026-08-20")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Ticker (Testlauf)")
    ap.add_argument("--kein-upload", action="store_true", help="nur lokal schreiben, nicht nach R2")
    args = ap.parse_args()

    ticker = lade_logbuch_ticker()
    if args.limit:
        ticker = ticker[:args.limit]
    print(f"{len(ticker)} Ticker (Zeitraum {args.ab} bis {args.bis}).")

    ergebnisse = {}
    fehler = []
    fertig = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(yahoo_chart_mit_datum, tk): tk for tk in ticker}
        for fut in as_completed(futs):
            tk = futs[fut]
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                ergebnisse[tk] = res
            else:
                fehler.append(tk)
            fertig += 1
            if fertig % 50 == 0 or fertig == len(ticker):
                print(f"  ... {fertig}/{len(ticker)} ({len(fehler)} ohne Daten bisher)")

    print(f"{len(ergebnisse)} Ticker mit Daten, {len(fehler)} ohne "
          f"(delisted/Symbolwechsel/Yahoo-Fehler): {', '.join(fehler[:20])}"
          f"{' ...' if len(fehler) > 20 else ''}")

    pro_tag = {}
    for tk, tage in ergebnisse.items():
        for d_str, (close, vol) in tage.items():
            if args.ab <= d_str <= args.bis:
                pro_tag.setdefault(d_str, []).append((tk, close, vol))

    out_dir = pfade.OHLC_HISTORY_DIR
    os.makedirs(out_dir, exist_ok=True)
    geschrieben = []
    uebersprungen = []
    for d_str in sorted(pro_tag.keys()):
        pfad = os.path.join(out_dir, f"{d_str}.csv")
        if os.path.exists(pfad):
            uebersprungen.append(d_str)
            continue
        with open(pfad, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "close", "volume"])
            for tk, close, vol in sorted(pro_tag[d_str]):
                w.writerow([tk, close, vol])
        geschrieben.append(d_str)

    if uebersprungen:
        print(f"Uebersprungen (Datei existiert schon, nicht ueberschrieben): {uebersprungen}")
    if not geschrieben:
        print("Keine neuen Tagesdateien geschrieben.")
        return
    print(f"{len(geschrieben)} Tagesdateien geschrieben: {geschrieben[0]} bis {geschrieben[-1]}")
    print(f"  -> {out_dir}")

    if not args.kein_upload:
        subprocess.run(["rclone", "copy", out_dir, "r2:signalhub-magazine/ohlc-history/"], check=True)
        print("Nach R2 hochgeladen (r2:signalhub-magazine/ohlc-history/).")


if __name__ == "__main__":
    main()
