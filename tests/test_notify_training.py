"""Push-Drosselung in der Trainingsphase (Signal-Hub/src/notify.py)."""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "Signal-Hub", "src"))
import notify  # noqa: E402


def test_standard_bis_trainingsende():
    assert notify.training_aktiv({}, datetime.date(2026, 11, 12))
    assert notify.training_aktiv({}, datetime.date(2027, 1, 3))
    assert not notify.training_aktiv({}, datetime.date(2027, 1, 4))


def test_config_ueberschreibt_und_schaltet_ab():
    assert not notify.training_aktiv({"training_bis": ""}, datetime.date(2026, 11, 12))
    assert notify.training_aktiv({"training_bis": "2027-03-01"}, datetime.date(2027, 2, 1))
    assert not notify.training_aktiv({"training_bis": "kein Datum"}, datetime.date(2026, 11, 12))


def test_slot_push_stumm_morning_brief_nicht(monkeypatch=None):
    gesendet = []
    alt = (notify.lade_config, notify.sende_ntfy, notify.baue_nachricht, sys.argv)
    notify.lade_config = lambda: {"benachrichtigung": {"aktiv": True, "ntfy_thema": "test"}}
    notify.sende_ntfy = lambda *a, **k: gesendet.append(a) or 200
    notify.baue_nachricht = lambda cfg, morgens=False: ("Titel", "Text")
    heute = notify.datetime.date.today()
    try:
        if heute <= datetime.date.fromisoformat(notify.TRAINING_BIS_STANDARD):
            sys.argv = ["notify.py"]
            notify.main()
            assert gesendet == []
        sys.argv = ["notify.py", "--morgens"]
        notify.main()
        assert len(gesendet) == 1
    finally:
        notify.lade_config, notify.sende_ntfy, notify.baue_nachricht, sys.argv = alt
