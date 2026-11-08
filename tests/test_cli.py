import pytest
from conftest import metrics_payload

from pomme.cli import main, parser, stats
from pomme.models import AppInfo, Availability, Dataset


def test_legacy_flags_and_today(capsys):
    assert parser().parse_args(["--cli", "--date", "2026-01-01"]).cli
    assert parser().parse_args(["--gui"]).gui
    assert main(["--today"]) == 2
    assert "unavailable data does not mean zero" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--today", "--date", "2026-01-01"])


async def test_stats_partial_exit(store, context, period, monkeypatch, capsys):
    async def connect(self):
        return context

    async def apps(self):
        return [AppInfo("1", "Good"), AppInfo("2", "Denied")]

    async def fetch(self, app_id, endpoint, p):
        return (
            Dataset(app_id, endpoint, p, metrics_payload(app_id, p), fetched_at="test")
            if app_id == "1"
            else Dataset(app_id, endpoint, p, status=Availability.DENIED)
        )

    monkeypatch.setattr("pomme.cli.AscClient.connect", connect)
    monkeypatch.setattr("pomme.cli.AscClient.apps", apps)
    monkeypatch.setattr("pomme.cli.AscClient.fetch", fetch)
    args = parser().parse_args(["stats", "--date", str(period.start)])
    assert await stats(args, store) == 2
    assert "TOTAL" in capsys.readouterr().out
