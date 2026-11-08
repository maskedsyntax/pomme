from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import metrics_payload

from pomme.models import (
    AppInfo,
    Availability,
    Dataset,
    Period,
    change,
    comparable_apps,
    format_value,
    portfolio_summary,
    preset_period,
)


def test_period_and_pacific_boundaries(monkeypatch):
    monkeypatch.setattr("pomme.models.reporting_today", lambda: date(2026, 3, 9))
    p = preset_period("Last 7 days")
    assert p.start == date(2026, 3, 2) and p.end == date(2026, 3, 8)
    assert p.days == p.previous().days == 7
    assert p.previous().end == date(2026, 3, 1)
    with pytest.raises(ValueError):
        Period(date(2026, 3, 9), date(2026, 3, 9))
    with pytest.raises(ValueError):
        Period(date(2026, 3, 8), date(2026, 3, 7))
    assert datetime(2026, 3, 9, 1, tzinfo=ZoneInfo("UTC")).astimezone(
        ZoneInfo("America/Los_Angeles")
    ).date() == date(2026, 3, 8)


def test_change_units_and_missing():
    assert change(5, 0) == "New"
    assert change(0, 0) == "0%"
    assert change(None, 0) == "—"
    assert change(3, 2, "percent") == "+1.00 pp"
    assert change(5, 10) == "-50.0%"
    assert format_value(0, "USD") == "$0.00"
    assert format_value(None) == "—"


def test_suppression_zero_and_reported_average(period):
    payload = metrics_payload("1", period, 3)
    payload["result"]["results"][0]["meetsThreshold"] = False
    ds = Dataset("1", "metrics", period, payload)
    series = ds.series()
    assert series["units"].total is None
    assert set(series["units"].points.values()) == {None}
    assert series["units"].availability == Availability.SUPPRESSED
    assert series["conversionRate"].total == 3  # Never sum the daily percentages.


def test_coverage_currency_and_matching(period):
    apps = [AppInfo("1", "One"), AppInfo("2", "Two")]
    ds = Dataset("1", "metrics", period, metrics_payload("1", period))
    assert portfolio_summary(apps, {"1": ds}, "units")[:2] == (35, 1)
    assert portfolio_summary(apps, {}, "units")[:2] == (None, 0)
    assert portfolio_summary(apps, {"1": ds}, "conversionRate")[:2] == (None, 0)
    other = metrics_payload("2", period)
    next(r for r in other["result"]["results"] if r["measure"] == "sales")["currency"] = "EUR"
    assert (
        portfolio_summary(apps, {"1": ds, "2": Dataset("2", "metrics", period, other)}, "sales")[0]
        is None
    )
    assert comparable_apps(apps, {"1": ds}, {"1": ds}, "units") == apps[:1]


def test_fractional_averages_are_visible():
    assert format_value(0.14, "count", "AVERAGE") == "0.14"


def test_reporting_day_uses_utc(monkeypatch):
    from pomme.models import reporting_today

    class Clock:
        @classmethod
        def now(cls, tz):
            return datetime(2026, 11, 7, 1, tzinfo=ZoneInfo("UTC")).astimezone(tz)

    monkeypatch.setattr("pomme.models.datetime", Clock)
    assert reporting_today() == date(2026, 11, 7)
