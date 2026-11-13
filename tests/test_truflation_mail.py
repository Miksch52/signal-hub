"""Tests fuer Signal-Hub/src/truflation_mail.py (Truman-Newsletter -> truflation.json).

Das HTML unten ist dem echten Newsletter-Aufbau nachgebaut (gekuerzt, eigene
Texte) - bewusst keine echte Mail im Repo: die enthaelt personalisierte
Tracking-Links. Laeuft ohne Netz.
"""

import json
import os
import sys

import pytest

WURZEL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(WURZEL, "Signal-Hub", "src"))

import truflation_mail as T  # noqa: E402

TRK = "https://abc.clicks.mlsend.com/tj/c/XYZ"

MAIL = f"""<!DOCTYPE html><html><head><title>Test | Truman</title>
<style>.x{{color:red}}</style></head><body>
<td class="gutter">Wednesday, September 30, 2026 · Research cut 09:15 ET · 2 stories</td>
<h2>What you need to know</h2>
<a href="#story-a">Kurzfassung</a>
<a id="story-a" name="story-a" href=""></a>
<table><tr><td>1. <span>🇺🇸 📈</span> United States · PCE inflation</td></tr></table>
<h2>Spending Rebound Keeps Demand in Focus</h2>
<p>X-driven · Verified</p>
<p>Fliesstext, der NICHT uebernommen werden darf.</p>
<p>What’s Truflation saying?</p><p>Einordnung, ebenfalls nicht.</p>
<table><tr><td><p>Key Metrics</p>
<table>
<tr><td>Real PCE, month over month <span>· August 2026</span></td><td align="right">+0.6%</td></tr>
<tr><td>Truflation U.S. real-time inflation measure <span>· September 28, 2026</span></td><td>2.535%</td></tr>
</table></td></tr></table>
<p><strong>Market relevance.</strong> Auch nicht.</p>
<p><strong>Sources:</strong> <a href="{TRK}" title="x">@NickTimiraos</a> · <a href="{TRK}">bea.gov: August 2026 PCE</a></p>
<a id="prediction-market-watch" href=""></a><h2>Prediction Market Watch</h2>
<a id="story-b" name="story-b" href=""></a>
<table><tr><td>2. <span>🇯🇵</span> Japan · BOJ decision</td></tr></table>
<h2>BOJ Hike Is Priced</h2>
<p>Lookahead</p>
<p><strong>Sources:</strong> <a href="{TRK}">boj.or.jp: statement</a></p>
<p>Unsubscribe <a href="{TRK}">hier</a></p>
</body></html>"""


def test_kopf_und_stories():
    a = T.parse_ausgabe(MAIL)
    assert a["datum"] == "2026-09-30"
    assert a["research_cut"] == "09:15 ET"
    assert [s["titel"] for s in a["stories"]] == [
        "Spending Rebound Keeps Demand in Focus", "BOJ Hike Is Priced"]
    s1, s2 = a["stories"]
    assert (s1["nr"], s1["emoji"], s1["land"], s1["thema"]) == (1, "🇺🇸 📈", "United States", "PCE inflation")
    assert s1["status"] == "X-driven · Verified"
    assert s1["kennzahlen"][0] == {"name": "Real PCE, month over month", "periode": "August 2026", "wert": "+0.6%"}
    assert s1["kennzahlen"][1]["wert"] == "2.535%"
    assert s1["quellen"] == ["@NickTimiraos", "bea.gov: August 2026 PCE"]
    assert (s2["land"], s2["status"], s2["kennzahlen"]) == ("Japan", "Lookahead", [])


def test_nur_fakten_keine_links_keine_texte():
    s = json.dumps(T.parse_ausgabe(MAIL), ensure_ascii=False)
    for verboten in ("mlsend", "http", "Fliesstext", "Einordnung", "Auch nicht", "Unsubscribe"):
        assert verboten not in s


def test_fremde_mail_wird_ignoriert():
    assert T.parse_ausgabe("<html><body><p>Hallo</p></body></html>") is None


def test_notbremse_gegen_urls():
    with pytest.raises(ValueError):
        T._pruefe_oeffentlich({"quellen": ["siehe https://example.com"]})


def test_historie_unveraenderlich_und_json(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "HISTORIE", str(tmp_path / "hist"))
    monkeypatch.setattr(T, "AUSGABE", str(tmp_path / "truflation.json"))
    a = T.parse_ausgabe(MAIL)
    T.speichere([a])
    b = dict(a, stories=[])            # spaetere, abweichende Fassung desselben Tages
    T.speichere([b])
    gespeichert = json.loads((tmp_path / "hist" / "2026-09-30.json").read_text())
    assert len(gespeichert["stories"]) == 2
    out = json.loads((tmp_path / "truflation.json").read_text())
    assert out["stand"] == "2026-09-30" and len(out["ausgaben"]) == 1
    T.speichere([])                    # leeres Postfach: letzter Stand bleibt
    assert json.loads((tmp_path / "truflation.json").read_text())["stand"] == "2026-09-30"
