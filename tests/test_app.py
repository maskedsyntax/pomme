from textual.widgets import DataTable, Input, Tabs

from pomme.adapter import AscClient, Repository
from pomme.app import Pomme
from pomme.models import Availability


async def ready(app, pilot):
    await pilot.pause()
    if app.screen_name == "home":
        app.action_explore()
        await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_navigation_dates_views_and_export(
    store, context, period, cached_portfolio, tmp_path
):
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=cached_portfolio)
    app.period, app.comparison, app.preset = period, period.previous(), "Custom"
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        assert len(app.current) == 2
        assert app.query_one("#table", DataTable).row_count == 2
        app.query_one("#search", Input).value = "Orchard"
        await pilot.pause()
        assert app.query_one("#table", DataTable).row_count == 1
        app.query_one("#table", DataTable).focus()
        await pilot.press("enter")
        await ready(app, pilot)
        assert app.screen_name == "details"
        assert app.query_one("#table", DataTable).row_count == 7
        app.action_back()
        await pilot.pause()
        app.action_add_compare()
        await pilot.pause()
        assert app.screen_name == "compare" and len(app.compare_ids) == 1
        app.action_dates()
        await pilot.pause()
        assert app.screen.query_one("#start", Input).value == str(period.start)
        await pilot.press("escape")
        await pilot.pause()
        app.action_save_view()
        await pilot.pause()
        app.screen.query_one("#value", Input).value = "Weekly pulse"
        await pilot.click("#save")
        await pilot.pause()
        assert "Weekly pulse" in app.saved_views()
        saved_ids = list(app.saved_views()["Weekly pulse"]["compare_ids"])
        app.compare_ids.append("2")
        assert app.saved_views()["Weekly pulse"]["compare_ids"] == saved_ids
        app.compare_ids.remove("2")
        app.action_export()
        await pilot.pause()
        app.screen.query_one("#value", Input).value = str(tmp_path / "out.json")
        await pilot.click("#save")
        await pilot.pause()
        assert (tmp_path / "out.json").exists()
        app.query_one("#navigation", Tabs).active = "saved"
        await pilot.pause()
        assert app.query_one("#table", DataTable).row_count == 1
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        await pilot.resize_terminal(160, 48)
        await pilot.pause()
        app.save_screenshot(filename="pomme-test.svg", path=str(tmp_path))


async def test_offline_tabs_and_no_network(store, context, period, cached_portfolio):
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=cached_portfolio)
    app.period, app.comparison, app.preset = period, period.previous(), "Custom"
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        app.query_one("#navigation", Tabs).active = "details"
        app.query_one("#detail-tabs", Tabs).active = "acquisition"
        await ready(app, pilot)
        assert app.extra["sources"].status == Availability.MISSING
        app.action_help()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        app.action_settings()
        await pilot.pause()
        await pilot.press("escape")


async def test_typing_does_not_trigger_shortcuts(store, context, period, cached_portfolio):
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=cached_portfolio)
    app.period, app.comparison = period, period.previous()
    async with app.run_test(size=(120, 40)) as pilot:
        await ready(app, pilot)
        app.query_one("#search", Input).focus()
        await pilot.press("q", "d", "r", "e", "s")
        assert app.query_one("#search", Input).value == "qdres"


async def test_fetch_while_modal_open_and_date_race(store, context, period, cached_portfolio):
    import asyncio
    from datetime import timedelta

    from conftest import metrics_payload

    from pomme.models import Dataset, Period

    client = AscClient()

    async def fetch(app_id, endpoint, p):
        await asyncio.sleep(0.04)
        return Dataset(app_id, endpoint, p, metrics_payload(app_id, p, 9), fetched_at="test")

    client.fetch = fetch
    repo = Repository(client, store, context)
    app = Pomme(store, client, repository=repo, apps=cached_portfolio)
    app.period, app.comparison, app.preset = period, None, "Custom"
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot)
        app.load_core(refresh=True)
        app.action_dates()
        await pilot.pause(0.1)
        await app.workers.wait_for_complete()
        assert app.current["1"].series()["units"].total == 63
        await pilot.press("escape")
        app.load_core(refresh=True)
        latest = Period(period.start + timedelta(days=20), period.end + timedelta(days=20))
        app.period = latest
        app.load_core(refresh=True)
        await ready(app, pilot)
        assert all(ds.period == latest for ds in app.current.values())
        assert app.query_one("#chart").region.bottom <= app.query_one("#body").region.bottom


async def test_breakdown_semantics_and_compare_selection(store, context, period, cached_portfolio):
    from textual.widgets import Select

    from pomme.models import Dataset

    payload = {
        "appId": "1",
        "startDate": str(period.start),
        "endDate": str(period.end),
        "measure": "pageViewUnique",
        "result": {
            "results": [
                {
                    "group": {"key": "Search", "title": "App Store Search"},
                    "totals": {"key": "pageViewUnique", "type": "AVERAGE", "value": 2},
                    "data": [{"date": str(d), "pageViewUnique": 2} for d in period.dates()],
                },
                {
                    "group": {"key": "Private", "title": "Private"},
                    "meetsThreshold": False,
                    "totals": {"key": "pageViewUnique", "type": "AVERAGE", "value": 50},
                    "data": [],
                },
            ]
        },
    }
    store.put(context, Dataset("1", "sources", period, payload, fetched_at="test"))
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=cached_portfolio)
    app.period, app.comparison = period, None
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot)
        app.query_one("#navigation", Tabs).active = "details"
        app.query_one("#detail-tabs", Tabs).active = "acquisition"
        await ready(app, pilot)
        rows = app.breakdown_rows(app.extra["sources"])
        assert rows[0][1:3] == (2, "AVERAGE")  # Never turn an average into 14 views.
        assert rows[1][1] is None
        app.query_one("#navigation", Tabs).active = "compare"
        app.query_one("#compare-0", Select).value = "1"
        app.query_one("#compare-1", Select).value = "2"
        await pilot.pause()
        assert app.compare_ids == ["1", "2"]
        app.query_one("#compare-0", Select).clear()
        await pilot.pause()
        assert app.compare_ids == ["2"]


async def test_sales_subscriptions_and_saved_view_management(
    store, context, period, cached_portfolio
):
    from copy import deepcopy

    from conftest import metrics_payload

    from pomme.models import Dataset
    from pomme.widgets import TrendChart

    for p in [period, period.previous()]:
        records = metrics_payload("1", p)["result"]["results"]
        sales = {
            "appId": "1",
            "startDate": str(p.start),
            "endDate": str(p.end),
            "summary": [r for r in records if r["measure"] in ("proceeds", "sales", "iap")],
            "revenueByTerritory": {"name": "Proceeds by Territory", "total": 0},
        }
        subscriptions = deepcopy(sales)
        subscriptions["summary"] = [
            {
                "measure": "subscription-state-plans-active",
                "total": 4,
                "data": [{"date": str(d), "value": 4} for d in p.dates()],
            }
        ]
        subscriptions["planTimeline"] = [
            {
                "totals": {"key": "summary-plans-paid-net", "value": 2, "type": "COUNT"},
                "data": [{"date": str(d), "summary-plans-paid-net": 2} for d in p.dates()],
            }
        ]
        for endpoint, payload in [("sales", sales), ("subscriptions", subscriptions)]:
            store.put(context, Dataset("1", endpoint, p, payload, fetched_at="test"))
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=cached_portfolio)
    app.period, app.comparison, app.preset = period, period.previous(), "Custom"
    async with app.run_test(size=(120, 36)) as pilot:
        await ready(app, pilot)
        app.query_one("#navigation", Tabs).active = "details"
        app.query_one("#detail-tabs", Tabs).active = "sales"
        await ready(app, pilot)
        chart = app.query_one(TrendChart)
        assert chart.unit == "USD" and len(chart.lines) == 2
        assert any(row.get("metric") == "iap" for row in app.export_rows)
        app.query_one("#detail-tabs", Tabs).active = "subscriptions"
        await ready(app, pilot)
        assert app.breakdown_rows(app.extra["subscriptions"])[0][0] == "Summary Plans Paid Net"
        app.saved_views()["Old"] = app.state()
        app.query_one("#navigation", Tabs).active = "saved"
        await pilot.pause()
        app.rename_saved()
        await pilot.pause()
        app.screen.query_one("#value", Input).value = "New"
        await pilot.click("#save")
        await pilot.pause()
        assert "New" in app.saved_views() and "Old" not in app.saved_views()
        app.delete_saved()
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause()
        assert not app.saved_views()


async def test_simple_home_always_opens_yesterday(store, context, monkeypatch, tmp_path):
    from datetime import date

    from conftest import metrics_payload
    from textual.widgets import Button

    from pomme.models import AppInfo, Dataset, preset_period

    monkeypatch.setattr("pomme.models.reporting_today", lambda: date(2026, 1, 9))
    monkeypatch.setattr("pomme.app.reporting_today", lambda: date(2026, 1, 9))
    store.save_settings(
        {
            "last_view": {
                "screen": "compare",
                "preset": "Last 30 days",
                "comparison_mode": "previous",
                "search": "Old filter",
                "favorites_only": True,
            }
        }
    )
    period = preset_period("Yesterday")
    apps = [AppInfo("1", "Orchard"), AppInfo("2", "Seedling")]
    for app in apps:
        store.put(
            context,
            Dataset(
                app.id, "metrics", period, metrics_payload(app.id, period, 3), fetched_at="test"
            ),
        )
    repo = Repository(AscClient(), store, context, offline=True)
    app = Pomme(store, repo.client, offline=True, repository=repo, apps=apps)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen_name == "home"
        assert app.period == period and app.comparison is None
        assert app.query_one("#table", DataTable).row_count == 3
        assert not app.query_one("#navigation").display
        assert not app.query_one("#cards").display
        assert not app.query_one("#chart").display
        assert not app.query_one("#global-controls").display
        assert app.query_one("#next-day", Button).disabled
        assert app.query_one("#table").region.bottom <= app.query_one("#body").region.bottom
        app.save_screenshot(filename="pomme-simple.svg", path=str(tmp_path))
        await pilot.click("#today-button")
        await pilot.pause()
        assert app.screen.title_text == "Today's data"
        await pilot.press("escape")
        await pilot.click("#previous-day")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.period.end == date(2026, 1, 7)
        assert app.current["1"].status == Availability.MISSING
        await pilot.click("#yesterday-button")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.period == period
        await pilot.click("#home-date")
        await pilot.pause()
        app.screen.query_one("#day", Input).value = "2026-01-09"
        await pilot.click("#apply")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DayDialog"
        await pilot.press("escape")
        await pilot.click("#explore-button")
        await pilot.pause()
        assert app.screen_name == "portfolio"
        app.action_back()
        await pilot.pause()
        app.query_one("#table", DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen_name == "details"
