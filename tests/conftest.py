from datetime import date

import pytest

from pomme.models import METRICS, AppInfo, Context, Dataset, Period
from pomme.storage import Store


@pytest.fixture
def period():
    return Period(date(2026, 1, 2), date(2026, 1, 8))


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "cache", tmp_path / "config")
    yield result
    result.close()


@pytest.fixture
def context():
    return Context("tester@example.test", "123", "KEY")


def metrics_payload(app_id, period, value=5):
    return {
        "appId": app_id,
        "startDate": str(period.start),
        "endDate": str(period.end),
        "frequency": "day",
        "result": {
            "results": [
                {
                    "measure": m.key,
                    "total": value if m.unit == "percent" else value * period.days,
                    "type": "AVERAGE" if m.unit == "percent" else "COUNT",
                    "meetsThreshold": True,
                    "data": [{"date": str(d), "value": value} for d in period.dates()],
                }
                for m in METRICS
            ]
        },
    }


@pytest.fixture
def cached_portfolio(store, context, period):
    apps = [AppInfo("1", "Orchard"), AppInfo("2", "Seedling")]
    store.catalog(context, apps)
    for app in apps:
        for p, v in [(period, 5), (period.previous(), 2)]:
            store.put(
                context,
                Dataset(
                    app.id,
                    "metrics",
                    p,
                    metrics_payload(app.id, p, v),
                    fetched_at="2026-01-10T00:00:00+00:00",
                ),
            )
    return apps
