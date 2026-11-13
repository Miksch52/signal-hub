#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest fuer den Index-Vergleich (Systempruefung Punkt 5).

Prueft zwei Dinge:
  1. die Rechenlogik (exakte Datumszuordnung, Naeherungs-Fallback,
     Markt-Zuordnung, Aggregation),
  2. dass die DREI Kopien von index_vergleich.py identisch sind - sie liegen
     bewusst dupliziert in Signal-Hub, Price-Action-Hub und Rotation-Dashboard
     (eigenstaendige Repos mit unterschiedlichen relativen Pfaden, siehe
     Modul-Docstring). Ohne diese Klammer wuerden die Zahlen der Engines
     unbemerkt auseinanderlaufen - und genau ihre Vergleichbarkeit ist der
     Zweck des Umbaus.

Kein Netz, kein Server, keine POSTs -> gefahrlos gegen die Live-Daten.

  python3 tests/test_index_vergleich.py
"""
import hashlib
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
WURZEL = os.path.join(HIER, "..", "..")
sys.path.insert(0, os.path.join(WURZEL, "Signal-Hub", "src"))
import index_vergleich as iv

KOPIEN = [
    os.path.join(WURZEL, "Signal-Hub", "src", "index_vergleich.py"),
    os.path.join(WURZEL, "Price-Action-Hub", "src", "index_vergleich.py"),
    os.path.join(WURZEL, "Rotation-Dashboard", "src", "index_vergleich.py"),
]


def _chart(paare):
    """paare: [(datum, close), ...] -> Chart-Dict im Format aller drei Repos."""
    return {"dates": [d for d, _ in paare], "closes": [c for _, c in paare]}


def test_kopien_sind_identisch():
    summen = {}
    for pfad in KOPIEN:
        assert os.path.exists(pfad), f"fehlt: {pfad}"
        with open(pfad, "rb") as f:
            summen[pfad] = hashlib.sha256(f.read()).hexdigest()
    einzigartig = set(summen.values())
    assert len(einzigartig) == 1, (
        "index_vergleich.py ist in den drei Repos nicht identisch - "
        "eine Aenderung wurde nur teilweise nachgezogen:\n  "
        + "\n  ".join(f"{os.path.relpath(p, WURZEL)}: {h[:12]}" for p, h in summen.items()))
    print(f"ok  alle drei Kopien identisch ({list(einzigartig)[0][:12]})")


def test_exakte_datumszuordnung():
    """Mit Datumsreihe wird der Startpunkt exakt am Signaltag gesucht."""
    chart = _chart([("2026-06-01", 100.0), ("2026-06-15", 105.0), ("2026-07-01", 110.0)])
    # Signal am 15.06. -> Start 105, Ende 110 -> +4,76 %
    ret = iv.index_return(chart, "2026-06-15", 16)
    assert round(ret * 100, 2) == 4.76, ret
    print("ok  exakte Datumszuordnung nutzt den Kurs am Signaltag")


def test_signaltag_ohne_handelstag_nimmt_den_naechsten():
    """Signal an einem Wochenend-/Feiertag -> erster Handelstag danach."""
    chart = _chart([("2026-06-01", 100.0), ("2026-06-15", 105.0), ("2026-07-01", 110.0)])
    ret = iv.index_return(chart, "2026-06-10", 21)     # kein Bar am 10.06.
    assert round(ret * 100, 2) == 4.76, ret            # -> startet am 15.06.
    print("ok  Signal ohne eigenen Handelstag startet am naechsten Bar")


def test_naeherung_ohne_datumsreihe():
    """Chart ohne dates (alte Caches, Umstellungstag) -> Handelstage-Naeherung
    statt Ausfall der ganzen Kohorte."""
    closes = [100.0 + i for i in range(60)]            # 60 Bars, letzter = 159
    chart = {"closes": closes}
    ret = iv.index_return(chart, "2026-06-15", 28)     # 28 * 5/7 = 20 Bars zurueck
    assert round(ret * 100, 2) == round((159 / 139 - 1) * 100, 2), ret
    print("ok  ohne Datumsreihe greift die Handelstage-Naeherung")


def test_zeitraum_nicht_abbildbar_liefert_none():
    chart = _chart([("2026-06-01", 100.0), ("2026-06-15", 105.0)])
    assert iv.index_return(chart, "2027-01-01", 10) is None      # Signal nach dem Chart
    assert iv.index_return({}, "2026-06-01", 10) is None         # kein Chart
    assert iv.index_return({"closes": [1.0]}, "2026-06-01", 900) is None  # zu kurz
    print("ok  nicht abbildbarer Zeitraum liefert None statt einer Scheinzahl")


def test_markt_zuordnung():
    assert iv.index_symbol("USA") == "^GSPC"
    assert iv.index_symbol("Europa") == "^STOXX"
    assert iv.index_symbol(None) == "^GSPC"           # Rotation-Dashboard loggt keinen Markt
    assert iv.index_symbol("Unbekannt") == "^GSPC"
    print("ok  Markt-Zuordnung inklusive Rueckfall auf den US-Leitindex")


def test_fenster_nutzt_festen_stichtag():
    """index_return_fenster misst Signaltag -> Signaltag + N Tage, NICHT bis
    heute - das Gegenstueck zur Strategie-Spur aus exit_simulation.py."""
    chart = _chart([("2026-06-01", 100.0), ("2026-08-18", 120.0), ("2026-12-01", 200.0)])
    ret = iv.index_return_fenster(chart, "2026-06-01", 78)   # Stichtag 18.08.
    assert round(ret * 100, 2) == 20.0, ret                  # der 01.12. zaehlt nicht mit
    # Ohne Datumsreihe bewusst None (eine Naeherung waere hier irrefuehrend)
    assert iv.index_return_fenster({"closes": [1.0, 2.0]}, "2026-06-01", 78) is None
    print("ok  Fenster-Rechnung endet am festen Stichtag, nicht heute")


def test_edge_fuer_und_aggregation():
    idx = {"USA": _chart([("2026-06-01", 100.0), ("2026-07-01", 110.0)]),
           "Europa": _chart([("2026-06-01", 100.0), ("2026-07-01", 105.0)])}
    # Pick +15 %, US-Index +10 % -> Vorsprung 5 Pp
    edge = iv.edge_fuer(idx, "USA", "2026-06-01", 30, 0.15)
    assert round(edge * 100, 2) == 5.0, edge
    # Unbekannter Markt faellt auf USA zurueck
    assert round(iv.edge_fuer(idx, "Asien", "2026-06-01", 30, 0.15) * 100, 2) == 5.0

    stats = iv.ergaenze_edge({"n": 2, "win": 50.0}, [0.05, -0.01])
    assert stats["edge_idx_avg"] == 2.0, stats
    assert stats["edge_idx_win"] == 50.0, stats
    assert stats["edge_idx_n"] == 2, stats
    # Ohne Edge-Werte bleibt das Stats-Dict unveraendert (Kohorte ohne Index-Bezug)
    assert "edge_idx_avg" not in iv.ergaenze_edge({"n": 1}, [])
    print("ok  Vorsprung je Pick und Kohorten-Aggregation rechnen korrekt")


def _tagesreihe(start, n, kurs_fn):
    """Ein Bar je Kalendertag ab start, Kurs von kurs_fn(tag_index)."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return {"dates": [(d0 + timedelta(days=i)).isoformat() for i in range(n)],
            "closes": [kurs_fn(i) for i in range(n)]}


def test_fenster_returns_zaehlt_in_allen_erreichten_horizonten():
    """Eine Episode zaehlt in JEDEM Horizont, den sie erreicht hat - nicht nur
    im reifsten, wie es alle Forward-Tests bis 2026-09-13 taten."""
    chart = _tagesreihe("2026-01-01", 90, lambda i: 100.0 + i)
    r = iv.fenster_returns(chart, "2026-01-01", 100.0, vor_datum="2027-01-01")
    assert round(r["4W"] * 100, 2) == 21.0, r     # Tag 21 -> Kurs 121
    assert round(r["8W"] * 100, 2) == 50.0, r
    assert round(r["12W"] * 100, 2) == 78.0, r
    print("ok  feste Fenster: eine Episode zaehlt in 4W, 8W und 12W zugleich")


def test_fenster_wert_bleibt_fest_wenn_der_chart_waechst():
    """Das Stabilitaetskriterium aus der Roadmap: ein spaeterer Lauf mit mehr
    Kurshistorie aendert den 4W-Wert derselben Episode nicht."""
    kurz = _tagesreihe("2026-01-01", 30, lambda i: 100.0 + i)
    lang = _tagesreihe("2026-01-01", 200, lambda i: 100.0 + i)
    a = iv.fenster_returns(kurz, "2026-01-01", 100.0, vor_datum="2027-01-01")
    b = iv.fenster_returns(lang, "2026-01-01", 100.0, vor_datum="2027-01-01")
    assert a["4W"] == b["4W"], (a, b)
    assert a["8W"] is None and b["8W"] is not None, (a, b)
    print("ok  4W-Wert bleibt identisch, wenn spaeter mehr Kurshistorie da ist")


def test_laufender_handelstag_zaehlt_nicht_als_stichtag():
    """Faellt der Stichtag auf heute, ist der Bar noch ein Intraday-Stand und
    wird nicht verwendet - sonst aenderte sich der 'feste' Wert am Folgetag."""
    chart = _tagesreihe("2026-01-01", 22, lambda i: 100.0 + i)   # letzter Bar = 2026-01-22
    assert iv.fenster_returns(chart, "2026-01-01", 100.0, vor_datum="2026-01-22")["4W"] is None
    assert iv.fenster_returns(chart, "2026-01-01", 100.0, vor_datum="2026-01-23")["4W"] is not None
    print("ok  der Bar des laufenden Tages zaehlt nicht als Stichtag")


def test_fenster_ohne_datumsreihe_liefert_nichts():
    r = iv.fenster_returns({"closes": [1.0] * 100}, "2026-01-01", 1.0)
    assert all(v is None for v in r.values()), r
    assert not iv.hat_datumsreihe({"closes": [1.0]})
    assert iv.hat_datumsreihe(_tagesreihe("2026-01-01", 3, lambda i: 1.0))
    print("ok  ohne Datumsreihe wird nichts genaehert")


def test_fenster_edges_und_laengster_horizont():
    pick = _tagesreihe("2026-01-01", 90, lambda i: 120.0 if i >= 21 else 100.0)
    index = _tagesreihe("2026-01-01", 90, lambda i: 105.0 if i >= 21 else 100.0)
    rets = iv.fenster_returns(pick, "2026-01-01", 100.0, vor_datum="2027-01-01")
    edges = iv.fenster_edges({"USA": index}, "USA", "2026-01-01", rets)
    assert round(edges["4W"] * 100, 2) == 15.0, edges      # +20 % Pick, +5 % Index
    assert iv.laengster_horizont(rets)[0] == "12W"
    assert iv.laengster_horizont({"4W": None, "8W": None, "12W": None}) == (None, None)
    print("ok  Index-Vorsprung je festem Fenster und laengster Horizont korrekt")


def test_start_ist_schlusskurs_am_signaltag_nicht_geloggter_preis():
    """Der geloggte Preis meint je nach Logzeit Vortagesschluss, Intraday-Kurs
    oder Signaltagsschluss (Code-Review 2026-09-13). Gemessen wird deshalb ab dem
    Schlusskurs am Signaltag - der geloggte Preis (hier 95) spielt keine Rolle."""
    chart = _tagesreihe("2026-01-01", 90, lambda i: 100.0 + i)
    r = iv.fenster_returns(chart, "2026-01-01", 95.0, vor_datum="2027-01-01")
    assert round(r["4W"] * 100, 2) == 21.0, r        # 121 / 100, nicht 121 / 95
    print("ok  Startkurs ist der Schlusskurs am Signaltag, nicht der geloggte Preis")


def test_pick_und_index_messen_denselben_zeitraum():
    """Kernbefund des Code-Reviews: Pick und Index muessen am selben Bar starten
    und enden. Laufen beide identisch, ist der Vorsprung in jedem Horizont exakt
    null - gleich, welcher Preis geloggt wurde."""
    reihe = _tagesreihe("2025-12-20", 120, lambda i: 100.0 * (1.01 ** i))
    rets = iv.fenster_returns(reihe, "2026-01-02", 87.0, vor_datum="2027-01-01")
    edges = iv.fenster_edges({"USA": reihe}, "USA", "2026-01-02", rets)
    for h in ("4W", "8W", "12W"):
        assert rets[h] is not None and abs(edges[h]) < 1e-12, (h, rets[h], edges[h])
    print("ok  Pick und Index starten und enden am selben Bar (Vorsprung exakt 0)")


def test_index_fenster_ignoriert_laufenden_tag():
    """Wie beim Pick: faellt der Stichtag des Index auf heute, gibt es noch
    keinen Wert - sonst aenderte sich der Vorsprung am Folgetag."""
    chart = _tagesreihe("2026-01-01", 22, lambda i: 100.0 + i)   # letzter Bar 2026-01-22
    assert iv.index_return_fenster(chart, "2026-01-01", 21, vor_datum="2026-01-22") is None
    assert iv.index_return_fenster(chart, "2026-01-01", 21, vor_datum="2026-01-23") is not None
    print("ok  Index-Fenster nimmt den laufenden Tag nie als Stichtag")


def test_markt_fuer_leitet_fehlenden_markt_aus_dem_suffix_ab():
    """Das Rotations-Logbuch fuehrt keinen Markt - europaeische Werte wurden
    gegen den S&P 500 gemessen (Code-Review 2026-09-13)."""
    assert iv.markt_fuer("USA", "SAP.DE") == "USA"          # geloggter Markt gewinnt
    assert iv.markt_fuer(None, "SHEL.L") == "Europa"
    assert iv.markt_fuer(None, "ASML.AS") == "Europa"
    assert iv.markt_fuer(None, "AAPL") == "USA"
    assert iv.markt_fuer("Unbekannt", "TTE.PA") == "Europa"
    assert iv.markt_fuer(None, None) == "USA"
    print("ok  fehlender Markt wird aus dem Boersensuffix abgeleitet")


def test_rotation_ohne_markt_misst_europa_pick_gegen_stoxx():
    us = _tagesreihe("2026-01-01", 90, lambda i: 100.0)                  # S&P flach
    eu = _tagesreihe("2026-01-01", 90, lambda i: 100.0 + i)              # STOXX steigt
    rets = iv.fenster_returns(eu, "2026-01-01", 100.0, vor_datum="2027-01-01")
    edges = iv.fenster_edges({"USA": us, "Europa": eu}, None, "2026-01-01", rets,
                             pick_chart=eu, ticker="SHEL.L", vor_datum="2027-01-01")
    assert abs(edges["4W"]) < 1e-12, edges       # gegen STOXX: exakt null, nicht +21 %
    print("ok  Europa-Pick ohne geloggten Markt wird gegen den STOXX gemessen")


def test_index_misst_an_den_handelstagen_des_picks_feiertag():
    """London geschlossen, STOXX offen: der Pick hat am Stichtag keinen Bar
    und endet einen Tag spaeter. Der Index muss am SELBEN Tag enden - vorher
    endete er nach seinem eigenen Kalender einen Tag frueher."""
    idx = _tagesreihe("2026-01-01", 90, lambda i: 100.0 if i < 21 else (110.0 if i == 21 else 120.0))
    pick = {"dates": [d for d in idx["dates"] if d != "2026-01-22"],
            "closes": [c for d, c in zip(idx["dates"], idx["closes"]) if d != "2026-01-22"]}
    rets = iv.fenster_returns(pick, "2026-01-01", 100.0, vor_datum="2027-01-01")
    assert round(rets["4W"] * 100, 2) == 20.0, rets                      # Pick endet am 23.01.
    mit_pick = iv.fenster_edges({"Europa": idx}, "Europa", "2026-01-01", rets,
                                pick_chart=pick, vor_datum="2027-01-01")
    nur_kalender = iv.fenster_edges({"Europa": idx}, "Europa", "2026-01-01", rets,
                                    vor_datum="2027-01-01")
    assert abs(mit_pick["4W"]) < 1e-12, mit_pick                         # beide 01.01. -> 23.01.
    assert round(nur_kalender["4W"] * 100, 2) == 10.0, nur_kalender      # der alte Versatz
    print("ok  Index misst an den Handelstagen des Picks (Feiertag ohne Versatz)")


# ---------------------------------------------------------------------------
# Belegter Handelstag statt Schreibdatum (seit 2026-10-04)
# ---------------------------------------------------------------------------
def _werktage(paare_ausser=()):
    """US-Kalender 21.08.-30.10.2026 ohne Wochenenden und ohne die Tage in
    paare_ausser (z.B. Labor Day 07.09.); Kurs steigt je Bar um 1."""
    from datetime import date, timedelta
    d, dates = date(2026, 8, 21), []
    while d <= date(2026, 10, 30):
        if d.weekday() < 5 and d.isoformat() not in paare_ausser:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return {"dates": dates, "closes": [100.0 + i for i in range(len(dates))]}


def test_letzter_handelstag_am_wochenende_und_feiertag():
    """Sa-, So- und Mo-Lauf sehen dieselben Freitagskurse; am Dienstag nach
    Labor Day ist es ebenfalls noch der Freitag. Der Bar des laufenden Tages
    zaehlt nie."""
    us = _werktage({"2026-09-07"})
    for logtag in ("2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08"):
        assert iv.letzter_handelstag(us, logtag) == "2026-09-04", logtag
    assert iv.letzter_handelstag(us, "2026-09-09") == "2026-09-08"
    assert iv.letzter_handelstag({}, "2026-09-09") is None
    ht = iv.handelstag_fuer({"USA": us}, None, "NVDA", vor_datum="2026-09-06")
    assert ht == "2026-09-04", ht
    print("ok  belegter Handelstag: Sa/So/Mo/Feiertag -> Freitag, laufender Tag zaehlt nicht")


def test_logbuch_schluessel_trennt_altbestand_und_handelstag():
    """Ein altes Logdatum darf nie einen neuen Handelstag blockieren - auch
    nicht, wenn beide zufaellig dasselbe Datum tragen."""
    alt = {"datum": "2026-10-02", "ticker": "X", "kohorte": "k"}
    neu = {"datum": "2026-10-04", "handelstag": "2026-10-02", "ticker": "X", "kohorte": "k"}
    assert iv.logbuch_schluessel(alt, "ticker", "kohorte") != iv.logbuch_schluessel(neu, "ticker", "kohorte")
    sa = dict(neu, datum="2026-10-03")
    assert iv.logbuch_schluessel(sa, "ticker", "kohorte") == iv.logbuch_schluessel(neu, "ticker", "kohorte")
    print("ok  Log-Schluessel: gleicher Handelstag = ein Eintrag, Altbestand getrennt")


def test_start_datum_neu_und_altbestand_treffen_denselben_bar():
    """Neuer Eintrag (Handelstag Fr) und alter Eintrag (Logdatum Sa) starten am
    selben Bar wie bisher: Montagsschluss."""
    us = _werktage()
    alt = {"datum": "2026-09-05"}
    neu = {"datum": "2026-09-06", "handelstag": "2026-09-04"}
    assert iv.start_datum(alt) == "2026-09-05"
    assert iv.start_datum(neu) == "2026-09-05"
    assert iv.episoden_start(us, iv.start_datum(alt), "2027-01-01") == "2026-09-07"
    assert iv.episoden_start(us, iv.start_datum(neu), "2027-01-01") == "2026-09-07"
    # Werktag: Logdatum Di (Morgenlauf, sieht Mo-Schluss) == Handelstag Mo
    assert iv.episoden_start(us, iv.start_datum({"datum": "2026-09-15"}), "2027-01-01") == \
        iv.episoden_start(us, iv.start_datum({"handelstag": "2026-09-14"}), "2027-01-01") == "2026-09-15"
    print("ok  Startbar: neue und alte Eintraege meinen denselben Handelstag")


def test_dubletten_wochenende_und_labor_day():
    """Der Befund vom 2026-10-04: Sa/So/Mo und Labor Day + Folgetag zaehlten
    mehrfach. Jetzt eine Episode je Startbar - die erste Nennung gewinnt,
    nichts wird geloescht."""
    us = _werktage({"2026-09-07"})
    logtage = ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"]
    gesehen, gezaehlt = set(), []
    for d in logtage:
        if not iv.ist_dublette(gesehen, us, d, "NVDA", "leader"):
            gezaehlt.append(d)
    # Fr eigenstaendig; Sa/So/Mo(Feiertag)/Di -> alle Startbar Di 08.09.; Mi eigenstaendig
    assert gezaehlt == ["2026-09-04", "2026-09-05", "2026-09-09"], gezaehlt
    # Andere Kohorte oder anderer Ticker am selben Tag sind KEINE Dublette
    assert not iv.ist_dublette(gesehen, us, "2026-09-06", "NVDA", "resilient")
    assert not iv.ist_dublette(gesehen, us, "2026-09-06", "AMD", "leader")
    print("ok  Dubletten: Sa/So/Mo + Labor Day zaehlen einmal, Kohorten/Ticker getrennt")


def test_europa_wert_am_us_feiertag_bleibt_eigenstaendig():
    """Die Pruefung arbeitet am Ticker-Chart: ein europaeischer Wert handelt am
    Labor Day und bekommt dort seine eigene Episode."""
    eu = _werktage()
    gesehen = set()
    assert not iv.ist_dublette(gesehen, eu, "2026-09-07", "SAP.DE", "leader")
    assert not iv.ist_dublette(gesehen, eu, "2026-09-08", "SAP.DE", "leader")
    print("ok  Europa-Wert am US-Feiertag: zwei eigenstaendige Episoden")


if __name__ == "__main__":
    test_kopien_sind_identisch()
    test_exakte_datumszuordnung()
    test_signaltag_ohne_handelstag_nimmt_den_naechsten()
    test_naeherung_ohne_datumsreihe()
    test_zeitraum_nicht_abbildbar_liefert_none()
    test_markt_zuordnung()
    test_fenster_nutzt_festen_stichtag()
    test_edge_fuer_und_aggregation()
    test_fenster_returns_zaehlt_in_allen_erreichten_horizonten()
    test_fenster_wert_bleibt_fest_wenn_der_chart_waechst()
    test_laufender_handelstag_zaehlt_nicht_als_stichtag()
    test_fenster_ohne_datumsreihe_liefert_nichts()
    test_fenster_edges_und_laengster_horizont()
    test_start_ist_schlusskurs_am_signaltag_nicht_geloggter_preis()
    test_pick_und_index_messen_denselben_zeitraum()
    test_index_fenster_ignoriert_laufenden_tag()
    test_markt_fuer_leitet_fehlenden_markt_aus_dem_suffix_ab()
    test_rotation_ohne_markt_misst_europa_pick_gegen_stoxx()
    test_letzter_handelstag_am_wochenende_und_feiertag()
    test_logbuch_schluessel_trennt_altbestand_und_handelstag()
    test_start_datum_neu_und_altbestand_treffen_denselben_bar()
    test_dubletten_wochenende_und_labor_day()
    test_europa_wert_am_us_feiertag_bleibt_eigenstaendig()
    test_index_misst_an_den_handelstagen_des_picks_feiertag()
    print("\nAlle Index-Vergleichs-Tests bestanden.")
