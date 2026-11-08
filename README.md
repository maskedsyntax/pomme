# Pomme

A read-only terminal dashboard for App Store Connect analytics, powered by your existing `asc` CLI credentials.

## Run

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and `asc` 5.17.1 or newer on `PATH`.

```sh
uv sync --python 3.13
uv run pomme
```

If you already use `asc-stats.py`, your configured credentials are reused. The compatibility launcher also works directly after setup:

```sh
./asc-stats.py                    # TUI
./asc-stats.py --gui              # TUI (legacy alias)
./asc-stats.py --cli              # Yesterday's table
./asc-stats.py --date 2026-10-29   # Table for a specific reporting day
```

Other entry points:

```sh
uv run python -m pomme
uv run pomme stats --date 2026-10-29
uv run pomme --offline
uv run pomme --apple-id you@example.com --provider-id 123456
```

`--provider-id` must match the current web-session provider. Pomme does not switch accounts or teams. Without `uv`, install with Python 3.11+ using `python -m pip install -e .` and run `pomme`.

## Authentication

Pomme checks two existing authentication surfaces:

- API credentials for the app catalog: configure with `asc auth login` (see `asc auth login --help`).
- Your Apple web session for analytics: configure with `asc web auth login`, or use **Sign in** inside Pomme to complete Apple login/2FA in the terminal.

Before fetching, Pomme checks that the active API key is visible in the current web team's API-key listing. If the credentials do not match, or your account cannot inspect that listing, fetching stops with an explanation. Select matching credentials in `asc` and refresh. An explicit provider that differs from the current session is rejected.

Pomme never collects or stores passwords, private keys, session tokens, or verification codes. The `asc` CLI owns authentication and may refresh its own session cache during requests.

## Explore

**Home** always opens on **Yesterday (UTC)** with a simple table: app, first-time downloads, IAP count, sales, proceeds, and status, plus a total row. Use **< Day**, **Day >**, **Yesterday**, or **Date** to change the reporting day. **Refresh** updates it; Enter opens an app. Old filters and advanced saved workspace state do not affect Home.

**Today?** explains the current data limitation: Pomme reads daily App Analytics. For recent units, sales and proceeds, Apple’s website offers **Sales and Trends → UTC → Last 24 Hours**, generally available within two hours. Yesterday’s daily analytics may be delayed or revised; Pomme cannot infer that an all-zero response is finalized. See [Apple’s hourly data guide](https://developer.apple.com/help/app-store-connect/measure-app-performance/view-units-proceeds-sales-and-pre-orders).

**Explore** opens the advanced **Portfolio** workspace. Select a range and enable comparisons with Dates when needed. Four cards show downloads, impressions, sales, and proceeds. Hover for comparison details; card labels show current coverage and the paired app count. Search, favorite filters, and unavailable-app filters define the displayed portfolio and its totals.

Use column presets for acquisition, monetization, or all nine metrics. Click column headings to sort; the selected chart metric has its own change column. Scroll tables horizontally for additional columns. Select a metric for its portfolio daily chart; the biggest-movers line ranks absolute changes. Conversion rates are available per app, rather than aggregated across apps.

**App Details** opens with Enter or a row click. Its tabs provide:

| Tab | Data |
| --- | --- |
| Overview | All nine metric summaries, selected metric's daily table, and current/comparison chart |
| Acquisition | Unique product-page views by source, reported aggregation, ranked bars, and daily series |
| Sales | Reported sales/IAP/proceeds summaries, selected monetary series, and purchase/territory breakdowns when returned |
| Subscriptions | Reported active/paid plan summaries and plan/timeline breakdowns when returned |

Select a breakdown row and press Enter to inspect its chart and exact observations below it. Tabs fetch only when opened. Empty breakdowns are labeled unavailable even when Apple returns a zero summary. Charts and observations follow the table; smaller terminals can scroll the content vertically.

**Compare** supports up to four distinct apps. Select two to four apps from the selectors or add the highlighted portfolio app with `a`. Compare one core metric on a shared scale with totals, previous values, absolute changes, and percentage changes. Mixed-currency charts are disabled.

**Saved Views** supports creating, loading, renaming, replacing, and deleting views. Views remember the screen/tab, app selection, metric, filters, columns, sorting, and comparison settings. Preset dates remain relative; custom dates stay fixed. Views are isolated by account/team/API-key context. Missing apps are skipped with a notice.

**Dates** offers yesterday, 7/30 days, month to date, previous month, and custom inclusive ranges. Comparisons can use the preceding equal-length period, another equal-length range, or be disabled. Today and future dates are rejected; on the first of a month, month to date has no completed reporting days.

**Export** writes the current view's table as CSV or JSON. Exports include reporting/comparison dates, account/team context, units, fetch timestamps, and availability. JSON includes structured breakdown observations where available. Existing files require confirmation. Missing values remain empty CSV cells or JSON `null`.

**Settings** provides dark/light themes, a reduced-color presentation, cache counts, and scoped cache clearing. Home always opens on Yesterday. Use a saved view to reopen a particular advanced analysis.

## Keyboard

| Key | Action |
| --- | --- |
| Arrows / Enter | Navigate / open app, view, or breakdown |
| Tab / Shift+Tab | Move between controls |
| Escape | Close dialog or return to Home |
| `x` | Open Explore |
| `/` | Search apps |
| `d` / `r` / `e` | Dates / refresh / export |
| `f` / `a` | Favorite highlighted app / add to comparison |
| `s` | Save view |
| `Ctrl+p` | Command palette |
| `?` / `q` | Help / quit |

Text inputs retain normal typing behavior. Mouse controls work on tabs, buttons, selectors, checkboxes, rows, and column headings. Minimum terminal size is 80×24; 120×36 or larger is recommended.

## Data semantics and cache

- App Analytics dates use **UTC**, independently of your computer’s timezone. This corrects the original script’s Pacific-date assumption. Downloadable Sales and Trends reports have different timezone rules; they are not the source used here. See [Apple’s reporting comparison](https://developer.apple.com/help/app-store-connect/measure-app-performance/differences-in-reporting-tools/). Recent values can be revised by Apple.
- `—` means missing, unpublished, suppressed, denied, or failed; zero means a returned zero.
- Counts and compatible monetary units can be added across apps. Conversion rates use Apple's reported period values and are never summed or averaged by Pomme.
- Percentage-rate differences use percentage points (`pp`). Other percentage changes use the prior value as baseline. Positive values after a zero baseline show **New**.
- Current totals show available coverage. Portfolio comparisons use only apps available in both periods. The chart uses that same paired population and leaves gaps where daily observations are incomplete.
- Acquisition sources represent unique page views, not download attribution. A returned `AVERAGE` stays an average; it is not multiplied by the number of days.
- Monetary data without an explicit currency follows the original script's USD-estimate convention. Explicit currencies are preserved and incompatible currencies are not aggregated.
- Subscription summaries retain Apple's meaning; Pomme does not infer MRR or sum daily active-plan counts.

Successful queries are retained in SQLite in the platform user-cache directory. Preferences and saved views live in the platform user-config directory. On macOS these normally resolve to `~/Library/Caches/pomme` and `~/Library/Application Support/pomme`.

Cached queries display immediately after account verification; **Refresh** explicitly re-fetches them. Uncached queries fetch online automatically. **Retry failed** retries unavailable/stale datasets while reusing healthy cached data. A failed refresh retains prior successful data with a stale label. Offline mode performs no `asc` calls and requires a previously cached account and catalog.

Override storage locations with `POMME_CACHE_DIR` and `POMME_CONFIG_DIR`, useful for isolated testing. Cache keys include account, team, API key, app, endpoint, date range, daily frequency, metric selection, and schema version. Clearing analytics cache leaves preferences, app catalogs, and saved views intact.

## Development

```sh
uv sync --locked --python 3.13
uv run pytest -q
uv run ruff check pomme tests asc-stats.py
uv run ruff format --check pomme tests asc-stats.py
uv build
```

Tests use synthetic fixtures, temporary storage, mocked subprocesses, and Textual's headless Pilot. They make no Apple requests. Read-only live checks during development verified the configured 13-app portfolio across two seven-day periods, plus acquisition, sales, and subscription response shapes. Personal live responses are not committed.

The implementation separates domain models/calculations (`models`), subprocess/authentication and request coordination (`adapter`), SQLite/config persistence (`storage`), export formatting (`exporting`), and Textual screens/dialogs/chart rendering (`app`, `dialogs`, `widgets`). The noninteractive CLI uses the same adapter and calculations.

Retention/cohort explorers, benchmarks, campaigns, scheduled syncing, notifications, account switching, and App Store mutations remain outside this release.
