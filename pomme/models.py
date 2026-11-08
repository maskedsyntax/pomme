from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

REPORTING_ZONE = ZoneInfo("UTC")


def reporting_today() -> date:
    return datetime.now(REPORTING_ZONE).date()


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def __post_init__(self):
        if self.start > self.end:
            raise ValueError("Start must be on or before end")
        if self.end >= reporting_today():
            raise ValueError("Choose completed UTC reporting days, ending before today")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        return Period(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def dates(self) -> list[date]:
        return [self.start + timedelta(days=n) for n in range(self.days)]

    def json(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @classmethod
    def parse(cls, obj: dict) -> Period:
        return cls(date.fromisoformat(obj["start"]), date.fromisoformat(obj["end"]))


def preset_period(name: str) -> Period:
    today = reporting_today()
    end = today - timedelta(days=1)
    if name == "Yesterday":
        return Period(end, end)
    if name == "Month to date":
        if today.day == 1:
            raise ValueError("No completed reporting days in this month yet")
        return Period(today.replace(day=1), end)
    if name == "Previous month":
        end = today.replace(day=1) - timedelta(days=1)
        return Period(end.replace(day=1), end)
    return Period(end - timedelta(days=29 if name == "Last 30 days" else 6), end)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str = "count"
    additive: bool = True


METRICS = (
    Metric("units", "First-time downloads"),
    Metric("redownloads", "Redownloads"),
    Metric("impressionsTotal", "Impressions"),
    Metric("pageViewCount", "Product page views"),
    Metric("conversionRate", "Conversion rate", "percent", False),
    Metric("updates", "Updates"),
    Metric("iap", "IAP count"),
    Metric("sales", "Sales", "USD"),
    Metric("proceeds", "Proceeds", "USD"),
)
METRIC_MAP = {m.key: m for m in METRICS}
PRESETS = {
    "Acquisition": [m.key for m in METRICS[:5]],
    "Monetization": ["units", "iap", "sales", "proceeds"],
    "All metrics": list(METRIC_MAP),
}


class Availability(StrEnum):
    READY = "Ready"
    MISSING = "Missing"
    SUPPRESSED = "Below threshold"
    UNPUBLISHED = "Unpublished"
    DENIED = "Access denied"
    FAILED = "Failed"
    NOT_LIVE = "Not live"


@dataclass(frozen=True)
class Context:
    apple_id: str
    provider_id: str
    api_profile: str = "default"

    @property
    def key(self) -> str:
        return f"{self.apple_id.casefold()}|{self.provider_id}|{self.api_profile}"


@dataclass(frozen=True)
class AppInfo:
    id: str
    name: str


@dataclass
class MetricSeries:
    key: str
    total: float | None
    points: dict[str, float | None] = field(default_factory=dict)
    aggregation: str = "COUNT"
    availability: Availability = Availability.READY
    unit: str = "count"


@dataclass
class Dataset:
    app_id: str
    endpoint: str
    period: Period
    payload: dict[str, Any] = field(default_factory=dict)
    status: Availability = Availability.READY
    message: str = ""
    fetched_at: str = ""
    cached: bool = False
    stale: bool = False

    def series(self) -> dict[str, MetricSeries]:
        result_object = self.payload.get("result") or {}
        records = (
            result_object.get("results", [])
            if self.endpoint == "metrics" and isinstance(result_object, dict)
            else self.payload.get("summary", [])
        )
        if not isinstance(records, list):
            records = []
        result = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            key = record.get("measure")
            if not key:
                continue
            suppressed = record.get("meetsThreshold", True) is False
            unit = (
                record.get("currency")
                or record.get("currencyCode")
                or METRIC_MAP.get(key, Metric(key, key)).unit
            )
            points = {}
            for point in record.get("data") or []:
                if isinstance(point, dict) and isinstance(point.get("date"), str):
                    points[point["date"][:10]] = None if suppressed else numeric(point.get("value"))
            result[key] = MetricSeries(
                key,
                None if suppressed else numeric(record.get("total")),
                points,
                record.get("type", "COUNT"),
                Availability.SUPPRESSED
                if suppressed
                else Availability.READY
                if numeric(record.get("total")) is not None
                else Availability.MISSING,
                unit,
            )
        return result


def numeric(value: Any) -> float | None:
    import math

    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def format_value(value: float | None, unit: str = "count", aggregation: str | None = None) -> str:
    if value is None:
        return "—"
    if unit == "percent":
        return f"{value:,.2f}%"
    if unit == "USD":
        return f"${value:,.2f}"
    if unit != "count":
        return f"{value:,.2f} {unit}"
    return f"{value:,.2f}" if aggregation == "AVERAGE" else f"{value:,.0f}"


def change(current: float | None, previous: float | None, unit: str = "count") -> str:
    if current is None or previous is None:
        return "—"
    delta = current - previous
    if unit == "percent":
        return f"{delta:+.2f} pp"
    if previous == 0:
        return "New" if current > 0 else ("0%" if current == 0 else "—")
    return f"{delta / abs(previous) * 100:+.1f}%"


def portfolio_summary(
    apps: list[AppInfo], data: dict[str, Dataset], key: str
) -> tuple[float | None, int, str]:
    metric = METRIC_MAP[key]
    if not metric.additive:
        return None, 0, metric.unit
    series = [data[a.id].series().get(key) for a in apps if a.id in data]
    available = [s for s in series if s is not None and s.total is not None]
    units = {s.unit for s in available}
    if len(units) > 1 or not available:
        return None, 0, metric.unit
    return sum(s.total for s in available), len(available), next(iter(units))


def comparable_apps(
    apps: list[AppInfo], current: dict[str, Dataset], previous: dict[str, Dataset], key: str
) -> list[AppInfo]:
    result = []
    for app in apps:
        a = current.get(app.id)
        b = previous.get(app.id)
        sa = a.series().get(key) if a else None
        sb = b.series().get(key) if b else None
        if sa and sb and sa.total is not None and sb.total is not None and sa.unit == sb.unit:
            result.append(app)
    return result
