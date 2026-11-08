from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import sys
from dataclasses import asdict
from datetime import date

from rich.console import Console
from rich.table import Table

from .adapter import AscClient, AscError, Repository
from .models import (
    METRICS,
    Availability,
    Period,
    format_value,
    portfolio_summary,
    preset_period,
    reporting_today,
)
from .storage import Store


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Pomme · App Store analytics in your terminal")
    result.add_argument(
        "command", nargs="?", choices=["stats"], help="Print the noninteractive metrics table"
    )
    result.add_argument("--offline", action="store_true", help="Use cached data only (TUI)")
    result.add_argument("--apple-id", help="Apple Account for the asc web session")
    result.add_argument("--provider-id", help="Current web provider/team ID")
    result.add_argument(
        "--date", type=date.fromisoformat, help="Reporting date for stats (YYYY-MM-DD)"
    )
    result.add_argument(
        "--today", action="store_true", help="Explain why live daily totals are unavailable"
    )
    result.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)
    return result


async def stats(args, store: Store) -> int:
    day = args.date or preset_period("Yesterday").start
    period = Period(day, day)
    client = AscClient(args.apple_id, args.provider_id)
    context = await client.connect()
    settings = store.settings()
    settings["context"] = asdict(context)
    store.save_settings(settings)
    apps = await client.apps()
    if not apps:
        raise AscError("No apps were found in App Store Connect")
    store.catalog(context, apps)
    repo = Repository(client, store, context)
    print(f"Fetching {day} metrics for {len(apps)} apps…", file=sys.stderr)
    try:
        datasets = await asyncio.gather(
            *(repo.get(app.id, "metrics", period, refresh=True) for app in apps)
        )
    finally:
        await repo.cancel()
    data = {d.app_id: d for d in datasets}
    table = Table(title=f"App Store stats · {day} · UTC reporting date")
    table.add_column("App")
    for metric in METRICS:
        table.add_column(metric.label, justify="right")
    partial = False
    for app in apps:
        ds = data[app.id]
        values = ds.series()
        table.add_row(
            app.name,
            *(
                format_value(values[m.key].total, values[m.key].unit) if m.key in values else "—"
                for m in METRICS
            ),
        )
        if (
            ds.status not in (Availability.READY, Availability.NOT_LIVE)
            or ds.stale
            or (
                ds.status == Availability.READY
                and any(m.key not in values or values[m.key].total is None for m in METRICS)
            )
        ):
            partial = True
        if ds.message:
            print(f"{app.name}: {ds.message}", file=sys.stderr)
    totals = []
    for m in METRICS:
        value, coverage, unit = portfolio_summary(apps, data, m.key)
        totals.append(
            format_value(value, unit)
            + (f" ({coverage}/{len(apps)})" if m.additive and coverage < len(apps) else "")
        )
    table.add_section()
    table.add_row("TOTAL", *totals)
    Console(width=max(Console().width, 180)).print(table)
    print(
        "Conversion rates are Apple's reported percentages and are not summed. USD amounts are Apple's estimates."
    )
    print(
        "Recent analytics may be revised. Unavailable values are excluded; totals show incomplete coverage."
    )
    return 2 if partial else 0


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    if args.date and args.today:
        p.error("use either --date or --today")
    if args.today or (args.date and args.date >= reporting_today()):
        print(
            "Pomme currently reads daily App Analytics, not hourly Sales and Trends; unavailable data does not mean zero activity. For recent units, sales and proceeds, use App Store Connect > Sales and Trends > UTC > Last 24 Hours (generally delayed by up to two hours). Use pomme stats for yesterday's daily analytics, which may still be delayed or revised.",
            file=sys.stderr,
        )
        return 2
    if args.offline and (args.command or args.cli or args.date):
        p.error("--offline is supported by the TUI; run pomme --offline")
    if not args.offline and not shutil.which("asc"):
        p.error("asc CLI is not installed or is not on PATH")
    store = None
    try:
        store = Store()
        if args.command == "stats" or args.cli or args.date:
            return asyncio.run(stats(args, store))
        from .app import Pomme

        Pomme(store, AscClient(args.apple_id, args.provider_id), args.offline).run()
        return 0
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        if store:
            store.close()
