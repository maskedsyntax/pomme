from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, HorizontalScroll, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    Tab,
    Tabs,
)

from .adapter import AscClient, AscError, Repository
from .dialogs import (
    ConfirmDialog,
    DateDialog,
    DayDialog,
    HelpDialog,
    InfoDialog,
    SettingsDialog,
    TextDialog,
)
from .exporting import export_file
from .models import (
    METRIC_MAP,
    METRICS,
    PRESETS,
    AppInfo,
    Availability,
    Context,
    Dataset,
    Period,
    change,
    comparable_apps,
    format_value,
    numeric,
    portfolio_summary,
    preset_period,
    reporting_today,
)
from .storage import Store, now
from .widgets import TrendChart


class Pomme(App):
    TITLE = "Pomme"
    CSS = """
    Screen { background: $background; }
    #identity, #dates, #status { height: auto; padding: 0 1; color: $text-muted; }
    #status { background: $surface; }
    #warning { height: auto; color: $warning; padding: 0 1; }
    #resize { display: none; height: 1fr; content-align: center middle; }
    #body { height: 1fr; }
    #home-note, #home-hint { height: auto; padding: 0 1; color: $text-muted; }
    #home-controls Button { min-width: 9; padding: 0 1; margin-right: 1; }
    .toolbar { height: 3; }
    .toolbar Button { min-width: 9; margin-right: 1; }
    .toolbar Select { width: 26; }
    #search { width: 25; min-width: 20; }
    #columns { width: 22; }
    #app-summary { height: auto; padding: 0 1; }
    #observations { height: 9; margin-top: 1; }
    #metric-hint { height: auto; padding: 0 1; color: $text-muted; }
    #cards { height: auto; min-height: 4; }
    .card { width: 1fr; height: auto; min-height: 4; border: round $primary-muted; padding: 0 1; }
    #table { height: 15; min-height: 5; }
    #movers { height: 1; padding: 0 1; }
    #detail-app { width: 48; }
    #compare-controls Select { width: 28; }
    #saved-controls Button { min-width: 12; }
    .hidden { display: none; }
    .small #cards { layout: vertical; }
    .small .card { width: 1fr; min-height: 3; }
    .reduced .card, .reduced TrendChart { border: solid $foreground; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("slash", "search", "Search"),
        ("d", "dates", "Dates"),
        ("r", "refresh", "Refresh"),
        ("e", "export", "Export"),
        ("question_mark", "help", "Help"),
        ("escape", "back", "Back"),
        ("f", "favorite", "Favorite"),
        ("a", "add_compare", "Compare app"),
        ("s", "save_view", "Save view"),
        ("x", "explore", "Explore"),
    ]

    def __init__(
        self,
        store: Store,
        client: AscClient,
        offline: bool = False,
        repository: Repository | None = None,
        apps: list[AppInfo] | None = None,
    ):
        super().__init__()
        self.store, self.client, self.offline = store, client, offline
        self.repo = repository
        self.apps = apps or []
        self.settings = store.settings()
        self.view_state = self.settings.get("last_view", {})
        # Opening Pomme always gives a clean daily view, regardless of advanced workspace state.
        self.preset = "Yesterday"
        self.period = preset_period("Yesterday")
        self.comparison = None
        self.current: dict[str, Dataset] = {}
        self.previous: dict[str, Dataset] = {}
        self.extra: dict[str, Dataset] = {}
        self.extra_previous: dict[str, Dataset] = {}
        self.screen_name = "home"
        self.detail_tab = "overview"
        self.selected_app: str | None = self.view_state.get("selected_app")
        self.compare_ids: list[str] = self.view_state.get("compare_ids", [])[:4]
        self.metric = self.view_state.get("metric", "units")
        if self.metric not in METRIC_MAP:
            self.metric = "units"
        self.columns = self.view_state.get("columns", "Acquisition")
        if self.columns not in PRESETS:
            self.columns = "Acquisition"
        self.sort_key, self.reverse = (
            self.view_state.get("sort_key", "name"),
            self.view_state.get("reverse", False),
        )
        self.portfolio_position = (0, 0, 0)
        self.generation = 0
        self.export_rows: list[dict] = []
        self.table_keys: list[str] = []
        self.rendering = False
        self.progress = "Starting…"
        self.login_needed = False
        self.theme = self.settings.get("theme", "textual-dark")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Checking API credentials and Apple web session…", id="identity", markup=False)
        yield Static("", id="dates", markup=False)
        yield Tabs(
            Tab("Home", id="home"),
            Tab("Portfolio", id="portfolio"),
            Tab("App Details", id="details"),
            Tab("Compare", id="compare"),
            Tab("Saved Views", id="saved"),
            id="navigation",
        )
        with HorizontalScroll(classes="toolbar", id="home-controls"):
            yield Button("< Day", id="previous-day")
            yield Button("Yesterday", id="yesterday-button")
            yield Button("Day >", id="next-day")
            yield Button("Date", id="home-date")
            yield Button("Refresh", id="home-refresh")
            yield Button("Explore", id="explore-button", variant="primary")
            yield Button("Today?", id="today-button")
        with HorizontalScroll(classes="toolbar", id="global-controls"):
            yield Button("Dates", id="dates-button")
            yield Button("Refresh", id="refresh-button")
            yield Button("Retry failed", id="retry-button")
            yield Button("Export", id="export-button")
            yield Button("Save view", id="save-button")
            yield Button("Settings", id="settings-button")
            yield Button("Sign in", id="login-button")
            yield Button("★ Favorite", id="favorite-button")
            yield Button("Add to compare", id="add-compare-button")
            yield Button("Help", id="help-button")
        yield Static("", id="warning", markup=False)
        yield Static("Resize to at least 80 × 24 · q Quit · ? Help", id="resize")
        with VerticalScroll(id="body"):
            with HorizontalScroll(classes="toolbar", id="detail-controls"):
                yield Select([], prompt="Choose app", id="detail-app")
            yield Tabs(
                Tab("Overview", id="overview"),
                Tab("Acquisition", id="acquisition"),
                Tab("Sales", id="sales"),
                Tab("Subscriptions", id="subscriptions"),
                id="detail-tabs",
            )
            with HorizontalScroll(classes="toolbar", id="compare-controls"):
                for i in range(4):
                    yield Select([], prompt=f"App {i + 1}", id=f"compare-{i}")
            with HorizontalScroll(classes="toolbar", id="saved-controls"):
                yield Button("Load", id="load-saved")
                yield Button("Rename", id="rename-saved")
                yield Button("Delete", id="delete-saved")
            with Horizontal(id="cards"):
                for key in ("units", "impressionsTotal", "sales", "proceeds"):
                    yield Static("—", classes="card", id=f"card-{key}", markup=False)
            yield Static("", id="app-summary", markup=False)
            with HorizontalScroll(classes="toolbar", id="metric-controls"):
                yield Input(
                    self.view_state.get("search", ""), placeholder="Search apps… (/)", id="search"
                )
                yield Select(
                    [(v, v) for v in PRESETS], value=self.columns, allow_blank=False, id="columns"
                )
                yield Select(
                    [(m.label, m.key) for m in METRICS],
                    value=self.metric,
                    allow_blank=False,
                    id="metric",
                )
                yield Checkbox(
                    "Favorites only",
                    value=self.view_state.get("favorites_only", False),
                    id="favorites-only",
                )
                yield Checkbox(
                    "Hide unavailable",
                    value=self.view_state.get("hide_unavailable", False),
                    id="hide-unavailable",
                )
            yield Static("Select a metric to chart · Select a row to inspect", id="metric-hint")
            yield Static("", id="home-note", markup=False)
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="movers", markup=False)
            yield TrendChart(id="chart")
            yield DataTable(id="observations", cursor_type="row", zebra_stripes=True)
        yield Static(self.progress, id="status", markup=False)
        yield Static(
            "Enter: app details   r: refresh   d: date   x: explore   q: quit", id="home-hint"
        )
        yield Footer()

    def ui(self, selector, expect_type=None):
        return self.screen_stack[0].query_one(selector, expect_type)

    def on_mount(self):
        self.set_class(self.settings.get("reduced_color", False), "reduced")
        self.render_view()
        self.initialize()

    def fit_portfolio(self):
        if self.screen_name == "home":
            self.ui("#table").styles.height = max(
                5, self.ui("#body").size.height - self.ui("#home-note").size.height
            )
        elif self.screen_name == "portfolio":
            body_height = self.ui("#body").size.height
            self.ui("#table").styles.height = max(
                5, body_height - self.ui("#cards").size.height - 14
            )

    def on_resize(self):
        if not self.is_mounted:
            return
        self.set_class(self.size.width < 110, "small")
        too_small = self.size.width < 80 or self.size.height < 24
        self.ui("#resize").display = too_small
        self.ui("#body").display = not too_small
        self.call_after_refresh(self.fit_portfolio)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in {
            "quit",
            "dates",
            "refresh",
            "export",
            "favorite",
            "add_compare",
            "save_view",
            "search",
            "help",
            "explore",
        } and isinstance(self.focused, Input):
            return False
        return True

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        for title, description, callback in [
            ("Reporting dates", "Choose reporting and comparison periods", self.action_dates),
            ("Refresh analytics", "Refresh the current view", self.action_refresh),
            ("Export current table", "Save CSV or JSON", self.action_export),
            ("Save current view", "Name this analytics view", self.action_save_view),
            ("Pomme help", "Keyboard shortcuts and metric meanings", self.action_help),
        ]:
            yield SystemCommand(title, description, callback)

    @work(exclusive=True, group="startup")
    async def initialize(self):
        try:
            if self.repo is None:
                if self.offline:
                    raw = self.settings.get("context")
                    if not raw:
                        raise AscError(
                            "No cached account. Connect online once before using offline mode."
                        )
                    context = Context(**raw)
                    if (
                        self.client.apple_id
                        and self.client.apple_id.casefold() != context.apple_id.casefold()
                    ):
                        raise AscError(
                            "Requested account does not match the cached offline account"
                        )
                    if self.client.provider_id and self.client.provider_id != context.provider_id:
                        raise AscError("Requested team does not match the cached offline team")
                else:
                    context = await self.client.connect()
                self.repo = Repository(self.client, self.store, context, self.offline)
            context = self.repo.context
            self.settings["context"] = asdict(context)
            self.store.save_settings(self.settings)
            self.ui("#identity", Static).update(
                f"{context.apple_id} · Team {context.provider_id} · API key verified / Web session {'offline' if self.offline else 'authenticated'}"
            )
            if not self.apps:
                self.apps = self.store.catalog(context)
                if not self.apps and self.offline:
                    raise AscError("No cached app catalog for this account/team")
                if not self.offline:
                    try:
                        self.apps = await self.client.apps()
                        self.store.catalog(context, self.apps)
                    except AscError:
                        if not self.apps:
                            raise
                        self.notify(
                            "App discovery failed; using cached catalog", severity="warning"
                        )
            self.configure_apps()
            self.ui("#navigation", Tabs).active = "home"
            self.login_needed = False
            self.render_view()
            self.ui("#warning", Static).update(
                "" if self.apps else "No apps were found for this account/team"
            )
            self.load_core()
        except AscError as exc:
            self.login_needed = True
            self.ui("#global-controls").display = True
            self.ui("#login-button").display = True
            self.ui("#identity", Static).update("Connection unavailable")
            self.ui("#warning", Static).update(str(exc))
            self.progress = (
                "Use Sign in for a web session, or configure asc auth login for API credentials."
            )
            self.update_status()

    def configure_apps(self):
        choices = [(Text(a.name), a.id) for a in self.apps]
        self.ui("#detail-app", Select).set_options(choices)
        if self.selected_app not in {a.id for a in self.apps}:
            self.selected_app = self.apps[0].id if self.apps else None
        if self.selected_app:
            self.ui("#detail-app", Select).value = self.selected_app
        self.compare_ids = [v for v in self.compare_ids if v in {a.id for a in self.apps}]
        for i in range(4):
            select = self.ui(f"#compare-{i}", Select)
            select.set_options(choices)
            select.value = self.compare_ids[i] if i < len(self.compare_ids) else Select.NULL

    @work(exclusive=True, group="core")
    async def load_core(self, refresh: bool = False, failed_only: bool = False):
        if not self.repo:
            return
        self.generation += 1
        generation = self.generation
        period, comparison = self.period, self.comparison
        old_current, old_previous = self.current, self.previous
        self.current, self.previous = {}, {}
        jobs = [
            (app.id, p, side)
            for app in self.apps
            for p, side in [(period, "current"), (comparison, "previous")]
            if p is not None
        ]
        total, done = len(jobs), 0

        async def one(app_id, p, side):
            old = (old_current if side == "current" else old_previous).get(app_id)
            retry = (
                failed_only and old is not None and (old.stale or old.status != Availability.READY)
            )
            ds = await self.repo.get(
                app_id, "metrics", p, refresh=(refresh and not failed_only) or retry
            )
            return app_id, side, ds

        tasks = [asyncio.create_task(one(*job)) for job in jobs]
        self.progress = f"Loading 0/{total} datasets…"
        self.update_status()
        try:
            for task in asyncio.as_completed(tasks):
                app_id, side, dataset = await task
                if generation != self.generation:
                    return
                (self.current if side == "current" else self.previous)[app_id] = dataset
                done += 1
                self.progress = f"Loading {done}/{total} datasets…"
                self.render_view()
            self.progress = "Ready" if total else "No apps"
            self.render_view()
            self.load_extra(refresh=refresh)
        except (AscError, OSError) as exc:
            self.progress = str(exc)
            self.update_status()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @work(exclusive=True, group="extra")
    async def load_extra(self, refresh: bool = False):
        if (
            not self.repo
            or self.screen_name != "details"
            or not self.selected_app
            or self.detail_tab == "overview"
        ):
            return
        app_id, endpoint, period, comparison = (
            self.selected_app,
            self.endpoint(),
            self.period,
            self.comparison,
        )
        token = (app_id, endpoint, period, comparison)
        self.progress = f"Loading {endpoint}…"
        self.update_status()
        self.extra, self.extra_previous = {}, {}
        periods = [period] + ([comparison] if comparison else [])
        try:
            datasets = await asyncio.gather(
                *(self.repo.get(app_id, endpoint, p, refresh=refresh) for p in periods)
            )
            if token != (self.selected_app, self.endpoint(), self.period, self.comparison):
                return
            self.extra[endpoint] = datasets[0]
            if comparison:
                self.extra_previous[endpoint] = datasets[1]
            self.progress = "Ready"
            self.render_view()
        except (AscError, OSError) as exc:
            self.progress = str(exc)
            self.update_status()

    def endpoint(self):
        return "sources" if self.detail_tab == "acquisition" else self.detail_tab

    def visible_apps(self):
        text = self.ui("#search", Input).value.casefold()
        favorites = set(self.settings.get("favorites", []))
        only = self.ui("#favorites-only", Checkbox).value
        hide = self.ui("#hide-unavailable", Checkbox).value
        return [
            a
            for a in self.apps
            if text in a.name.casefold()
            and (not only or a.id in favorites)
            and (
                not hide
                or (
                    a.id in self.current
                    and any(s.total is not None for s in self.current[a.id].series().values())
                )
            )
        ]

    def series_for(self, app_id, key, previous=False):
        ds = (self.previous if previous else self.current).get(app_id)
        return ds.series().get(key) if ds else None

    def status_for(self, ds: Dataset | None):
        if not ds:
            return "Loading"
        if ds.stale:
            return "Stale · " + ds.message
        if ds.status != Availability.READY:
            return f"{ds.status} · {ds.message}".strip(" ·")
        if not any(s.total is not None for s in ds.series().values()) and ds.endpoint == "metrics":
            return "Below threshold / missing"
        available = sum(s.total is not None for k, s in ds.series().items() if k in METRIC_MAP)
        prefix = (
            f"Partial {available}/9 · "
            if ds.endpoint == "metrics" and available < len(METRICS)
            else ""
        )
        return prefix + ("Cached " if ds.cached else "Fetched ") + ds.fetched_at

    def fill_table(
        self, columns: list[tuple[str, str]], rows: list[tuple[str, list]], exports: list[dict]
    ):
        table = self.ui("#table", DataTable)
        old_key = (
            self.table_keys[table.cursor_row] if table.cursor_row < len(self.table_keys) else None
        )
        old_x, old_y = table.scroll_x, table.scroll_y
        table.clear(columns=True)
        home_widths = {
            "name": max(16, min(40, self.size.width - 61)),
            "units": 9,
            "iap": 4,
            "sales": 10,
            "proceeds": 10,
            "status": 11,
        }
        for label, key in columns:
            table.add_column(
                label, key=key, width=home_widths.get(key) if self.screen_name == "home" else None
            )
        self.table_keys = []
        for key, cells in rows:
            table.add_row(
                *(
                    Text(c, style="bold" if key == "__total__" else "") if isinstance(c, str) else c
                    for c in cells
                ),
                key=key,
            )
            self.table_keys.append(key)
        if old_key in self.table_keys:
            table.move_cursor(row=self.table_keys.index(old_key), animate=False, scroll=False)
        table.scroll_to(x=old_x, y=old_y, animate=False, force=True)
        self.export_rows = exports

    def render_view(self):
        if not self.is_mounted:
            return
        self.rendering = True
        try:
            home = self.screen_name == "home"
            portfolio, details = self.screen_name == "portfolio", self.screen_name == "details"
            for selector, visible in [
                ("#home-controls", home),
                ("#home-note", home),
                ("#home-hint", home),
                ("#identity", not home),
                ("#navigation", not home),
                ("#global-controls", not home or self.login_needed),
                ("Footer", not home),
                ("#search", portfolio),
                ("#columns", portfolio),
                ("#favorites-only", portfolio),
                ("#hide-unavailable", portfolio),
                ("#app-summary", details and self.detail_tab == "overview"),
                ("#metric-hint", details),
                ("#observations", details or self.screen_name == "compare"),
                ("#detail-controls", details),
                ("#detail-tabs", details),
                ("#compare-controls", self.screen_name == "compare"),
                ("#saved-controls", self.screen_name == "saved"),
                ("#cards", portfolio or details),
                (
                    "#metric-controls",
                    self.screen_name in ("portfolio", "compare")
                    or (details and self.detail_tab in ("overview", "sales")),
                ),
                ("#chart", self.screen_name not in ("home", "saved")),
                ("#movers", portfolio),
            ]:
                self.ui(selector).display = visible
            for button in self.screen_stack[0].query("#global-controls Button"):
                button.display = not home or button.id == "login-button"
            self.ui("#dates", Static).update(
                f"{self.period.start} → {self.period.end} · UTC reporting dates"
                + (
                    f" · vs {self.comparison.start} → {self.comparison.end}"
                    if self.comparison
                    else " · Comparison off"
                )
            )
            if home:
                self.render_home()
            elif portfolio:
                self.render_portfolio()
            elif details:
                self.render_details()
            elif self.screen_name == "compare":
                self.render_compare()
            else:
                self.render_saved()
            body_height = self.ui("#body").size.height
            if portfolio and body_height > 0:
                self.ui("#table").styles.height = max(
                    5, body_height - self.ui("#cards").size.height - 14
                )
            else:
                self.ui("#table").styles.height = 12
            if home:
                label = (
                    "Yesterday"
                    if self.period == preset_period("Yesterday")
                    else "Daily stats"
                    if self.period.days == 1
                    else "Selected period"
                )
                self.ui("#dates", Static).update(
                    f"{label} · {self.period.start}"
                    + (f" → {self.period.end}" if self.period.days > 1 else "")
                    + " · UTC"
                )
                self.ui("#next-day", Button).disabled = (
                    self.period.end >= reporting_today() - timedelta(days=1)
                )
            self.ui("#login-button").display = self.login_needed
            self.update_status()
            self.view_state = self.state()
            self.call_after_refresh(self.fit_portfolio)
        finally:
            self.rendering = False

    def render_home(self):
        keys = ["units", "iap", "sales", "proceeds"]
        rows, exports = [], []
        for app in self.apps:
            ds = self.current.get(app.id)
            values = ds.series() if ds else {}
            status = (
                "Loading"
                if ds is None
                else "Stale"
                if ds.stale
                else "Unavailable"
                if ds.status != Availability.READY
                else "Partial"
                if any(k not in values or values[k].total is None for k in keys)
                else "Cached"
                if ds.cached
                else "Ready"
            )
            rows.append(
                (
                    app.id,
                    [app.name]
                    + [
                        format_value(values[k].total, values[k].unit) if k in values else "—"
                        for k in keys
                    ]
                    + [status],
                )
            )
            for key in keys:
                series = values.get(key)
                exports.append(
                    {
                        "app_id": app.id,
                        "app": app.name,
                        "metric": key,
                        "value": series.total if series else None,
                        "unit": series.unit if series else METRIC_MAP[key].unit,
                        "availability": series.availability
                        if series
                        else ds.status
                        if ds
                        else "Loading",
                        "fetched_at": ds.fetched_at if ds else None,
                    }
                )
        totals, coverage = [], []
        for key in keys:
            value, count, unit = portfolio_summary(self.apps, self.current, key)
            totals.append(format_value(value, unit))
            coverage.append(count)
        rows.append(
            (
                "__total__",
                [
                    "TOTAL",
                    *totals,
                    "Partial" if any(n < len(self.apps) for n in coverage) else "All apps",
                ],
            )
        )
        self.fill_table(
            [
                ("App", "name"),
                ("Downloads", "units"),
                ("IAPs", "iap"),
                ("Sales", "sales"),
                ("Proceeds", "proceeds"),
                ("Status", "status"),
            ],
            rows,
            exports,
        )
        note = (
            "Apple may still be publishing yesterday's data; recent figures can change. "
            if self.period.end == reporting_today() - timedelta(days=1)
            else ""
        )
        self.ui("#home-note", Static).update(
            note + "— means unavailable, not zero. Totals include available values only."
        )

    def render_cards(self, apps, detail=False):
        for key in ("units", "impressionsTotal", "sales", "proceeds"):
            total, coverage, unit = portfolio_summary(apps, self.current, key)
            matching = comparable_apps(apps, self.current, self.previous, key)
            cur, _, _ = portfolio_summary(matching, self.current, key)
            prev, _, _ = portfolio_summary(matching, self.previous, key)
            delta = change(cur, prev, unit) if self.comparison else ""
            absolute = (
                format_value(cur - prev, unit) if cur is not None and prev is not None else "—"
            )
            suffix = (
                f"{delta} · Δ {absolute} · paired {len(matching)}/{len(apps)}"
                if self.comparison
                else "Comparison off"
            )
            card = self.ui(f"#card-{key}", Static)
            label = "Downloads" if key == "units" else METRIC_MAP[key].label
            card.update(
                f"{label} · {coverage}/{len(apps)}\n{format_value(total, unit)} {delta}\nΔ {absolute} · pair {len(matching)}/{len(apps)}"
                if self.comparison
                else f"{label} · {coverage}/{len(apps)}\n{format_value(total, unit)}"
            )
            card.tooltip = suffix

    def render_portfolio(self):
        apps = self.visible_apps()
        key, metric = self.metric, METRIC_MAP[self.metric]

        def sort_value(a):
            if self.sort_key == "name":
                return a.name.casefold()
            if self.sort_key == "status":
                return self.status_for(self.current.get(a.id))
            series = self.series_for(a.id, self.sort_key if self.sort_key in METRIC_MAP else key)
            value = series.total if series else None
            if self.sort_key == "change":
                prev = self.series_for(a.id, key, True)
                if (
                    value is None
                    or not prev
                    or prev.total is None
                    or not series
                    or series.unit != prev.unit
                ):
                    value = None
                elif series.unit == "percent":
                    value -= prev.total
                elif prev.total == 0:
                    value = float("inf") if value > 0 else 0
                else:
                    value = (value - prev.total) / abs(prev.total)
            return (value is not None, value or 0)

        apps.sort(key=sort_value, reverse=self.reverse)
        self.render_cards(apps)
        keys = PRESETS[self.columns]
        columns = (
            [("App", "name")]
            + [(METRIC_MAP[k].label, k) for k in keys]
            + [(f"Δ {metric.label}", "change"), ("Status / fetched UTC", "status")]
        )
        rows, exports = [], []
        favorites = self.settings.get("favorites", [])
        movers = []
        mover_units = set()
        for app in apps:
            values = [self.series_for(app.id, k) for k in keys]
            cur, prev = self.series_for(app.id, key), self.series_for(app.id, key, True)
            compatible = cur and prev and cur.unit == prev.unit
            delta = (
                change(cur.total, prev.total, cur.unit) if compatible and self.comparison else "—"
            )
            rows.append(
                (
                    app.id,
                    [("★ " if app.id in favorites else "") + app.name]
                    + [format_value(s.total, s.unit) if s else "—" for s in values]
                    + [delta, self.status_for(self.current.get(app.id))],
                )
            )
            for m in (METRIC_MAP[k] for k in dict.fromkeys(keys + [key])):
                s, p = self.series_for(app.id, m.key), self.series_for(app.id, m.key, True)
                exports.append(
                    {
                        "app_id": app.id,
                        "app": app.name,
                        "metric": m.key,
                        "unit": s.unit if s else m.unit,
                        "value": s.total if s else None,
                        "previous": p.total if p else None,
                        "absolute_change": s.total - p.total
                        if s
                        and p
                        and s.total is not None
                        and p.total is not None
                        and s.unit == p.unit
                        else None,
                        "change": change(s.total, p.total, s.unit)
                        if s and p and s.unit == p.unit
                        else None,
                        "availability": s.availability
                        if s
                        else self.current.get(app.id).status
                        if app.id in self.current
                        else "Loading",
                        "fetched_at": self.current[app.id].fetched_at
                        if app.id in self.current
                        else None,
                    }
                )
            if compatible and cur.total is not None and prev.total is not None:
                movers.append((cur.total - prev.total, app.name))
                mover_units.add(cur.unit)
        self.fill_table(columns, rows, exports)
        self.portfolio_chart(apps)
        if len(mover_units) > 1:
            movers = []
        increases = sorted((m for m in movers if m[0] > 0), reverse=True)[:3]
        decreases = sorted(m for m in movers if m[0] < 0)[:3]
        self.ui("#movers", Static).update(
            "Biggest movers · "
            + (
                " · ".join(
                    f"{name}: {delta:+,.2f}{' pp' if metric.unit == 'percent' else ''}"
                    for delta, name in increases + decreases
                )
                or "No comparable changes"
            )
        )

    def portfolio_chart(self, apps):
        metric = METRIC_MAP[self.metric]
        chart = self.ui(TrendChart)
        if not metric.additive:
            chart.set_series("Portfolio conversion rate is not additive · open an app to view", [])
            return
        matching = (
            comparable_apps(apps, self.current, self.previous, self.metric)
            if self.comparison
            else apps
        )
        lines = []
        units = {
            s.unit
            for a in matching
            if (s := self.series_for(a.id, self.metric)) and s.total is not None
        }
        if len(units) > 1:
            chart.set_series("Mixed currencies · portfolio trend unavailable", [])
            return
        for previous, period, label in [
            (False, self.period, "Current"),
            (True, self.comparison, "Comparison"),
        ]:
            if period is None:
                continue
            points = []
            for day in period.dates():
                observations = [
                    s.points.get(day.isoformat())
                    if (s := self.series_for(a.id, self.metric, previous))
                    else None
                    for a in matching
                ]
                points.append(
                    sum(observations)
                    if observations and all(v is not None for v in observations)
                    else None
                )
            lines.append((label, points))
        chart.set_series(
            f"{metric.label} · {len(matching)}/{len(apps)} apps · gaps indicate incomplete observations",
            lines,
            next(iter(units), metric.unit),
        )

    def render_details(self):
        app = next((a for a in self.apps if a.id == self.selected_app), None)
        if not app:
            self.fill_table([("App", "app")], [], [])
            self.ui(TrendChart).set_series("Choose an app", [])
            return
        self.ui("#observations", DataTable).clear(columns=True)
        self.render_cards([app], detail=True)
        summaries = []
        for metric in METRICS:
            a, b = self.series_for(app.id, metric.key), self.series_for(app.id, metric.key, True)
            delta = change(a.total, b.total, a.unit) if a and b and a.unit == b.unit else "—"
            summaries.append(
                f"{metric.label}: {format_value(a.total, a.unit) if a else chr(8212)} ({delta})"
            )
        self.ui("#app-summary", Static).update(
            "\n".join("   ·   ".join(summaries[i : i + 3]) for i in range(0, 9, 3))
        )
        if self.detail_tab == "overview":
            self.render_daily(app)
            return
        ds = self.extra.get(self.endpoint())
        if not ds:
            self.fill_table(
                [("Status", "status")], [("loading", ["Open tab to load analytics…"])], []
            )
            self.ui(TrendChart).set_series("Loading tab…", [])
            return
        if ds.status != Availability.READY:
            self.fill_table(
                [("Status", "status")],
                [("error", [self.status_for(ds)])],
                [{"app": app.name, "status": ds.status, "message": ds.message}],
            )
            self.ui(TrendChart).set_series(self.status_for(ds), [])
            return
        rows, exports = [], []
        lines = []
        prior = self.extra_previous.get(self.endpoint())
        previous_series = prior.series() if prior else {}
        for key, series in ds.series().items():
            p = previous_series.get(key)
            label = (
                METRIC_MAP[key].label
                if key in METRIC_MAP
                else key.replace("subscription-state-", "").replace("-", " ").title()
            )
            delta = (
                change(series.total, p.total, series.unit) if p and p.unit == series.unit else "—"
            )
            rows.append(
                (
                    f"metric:{key}",
                    [label, format_value(series.total, series.unit), series.aggregation, delta],
                )
            )
            exports.append(
                {
                    "app": app.name,
                    "section": "summary",
                    "metric": key,
                    "value": series.total,
                    "unit": series.unit,
                    "aggregation": series.aggregation,
                    "previous": p.total if p else None,
                    "change": delta,
                    "availability": series.availability,
                }
            )
            if series.points and len(lines) < 4:
                lines.append(
                    (label, [series.points.get(d.isoformat()) for d in self.period.dates()])
                )
        groups = self.breakdown_rows(ds)
        maximum = max((abs(g[1]) for g in groups if g[1] is not None), default=0)
        for i, group in enumerate(groups):
            label, value, aggregation, points, unit = group
            bar = "█" * round(abs(value or 0) / maximum * 12) if maximum else ""
            rows.append(
                (f"group:{i}", [label, format_value(value, unit, aggregation), aggregation, bar])
            )
            exports.append(
                {
                    "app": app.name,
                    "section": "breakdown",
                    "label": label,
                    "value": value,
                    "aggregation": aggregation,
                    "unit": unit,
                    "daily": points,
                }
            )
        if not groups:
            rows.append(("no-breakdown", ["Breakdown unavailable", "—", "", ""]))
        if not rows:
            rows.append(("empty", ["No published data", "—", "", ""]))
        if self.detail_tab == "sales":
            candidates = ds.series()
            selected = (
                self.metric
                if self.metric in candidates
                else "proceeds"
                if "proceeds" in candidates
                else next(iter(candidates), None)
            )
            selected_unit = candidates[selected].unit if selected else "USD"
            lines = [
                (
                    METRIC_MAP[k].label if k in METRIC_MAP else k,
                    [s.points.get(d.isoformat()) for d in self.period.dates()],
                )
                for k, s in candidates.items()
                if s.unit == selected_unit and k == selected
            ]
            previous_metric = previous_series.get(selected)
            if self.comparison and previous_metric and previous_metric.unit == selected_unit:
                lines.append(
                    (
                        "Comparison",
                        [
                            previous_metric.points.get(d.isoformat())
                            for d in self.comparison.dates()
                        ],
                    )
                )
        if self.detail_tab == "acquisition":
            self.ui("#metric-hint", Static).update(
                "Unique page views by source · averages are not period totals"
            )
            if groups:
                group = groups[0]
                lines = [(group[0], [group[3].get(d.isoformat()) for d in self.period.dates()])]
        else:
            self.ui("#metric-hint", Static).update(
                "Apple summaries · select a breakdown row to inspect"
            )
        self.fill_table(
            [
                ("Metric / breakdown", "label"),
                ("Value", "value"),
                ("Aggregation", "aggregation"),
                ("Change / relative size", "change"),
            ],
            rows,
            exports,
        )
        unit = "count" if self.detail_tab in ("acquisition", "subscriptions") else selected_unit
        self.ui(TrendChart).set_series(
            f"{app.name} · {self.detail_tab} · {self.status_for(ds)}", lines, unit
        )
        self.render_observations(lines, unit)

    def breakdown_rows(self, ds):
        groups = []

        def visit(obj, title=""):
            if isinstance(obj, list):
                for item in obj:
                    visit(item, title)
            elif isinstance(obj, dict):
                totals = obj.get("totals")
                group = obj.get("group", {})
                if isinstance(totals, dict) and (group or obj.get("data")):
                    label = str(
                        group.get("title")
                        or group.get("key")
                        or str(totals.get("key", title)).replace("-", " ").title()
                        or "Timeline"
                    )
                    measure = totals.get("key", ds.payload.get("measure", "value"))
                    unit = (
                        obj.get("currencyCode")
                        or obj.get("currency")
                        or ("USD" if measure in ("sales", "proceeds") else "count")
                    )
                    points = {
                        p["date"][:10]: numeric(p.get(measure, p.get("value")))
                        for p in obj.get("data", [])
                        if isinstance(p, dict) and p.get("date")
                    }
                    if obj.get("meetsThreshold", True) is False:
                        points = {k: None for k in points}
                        totals = {**totals, "value": None}
                    groups.append(
                        (
                            label,
                            numeric(totals.get("value")),
                            totals.get("type", "Unknown"),
                            points,
                            unit,
                        )
                    )
                    return
                for key, value in obj.items():
                    if key not in (
                        "summary",
                        "downloadToPaid",
                        "proceedsPerDownload",
                        "subscriptionRetention",
                    ):
                        visit(value, obj.get("name", key))

        visit(ds.payload)
        return sorted(groups, key=lambda g: (g[1] is not None, g[1] or 0), reverse=True)

    def render_daily(self, app):
        current, previous = (
            self.series_for(app.id, self.metric),
            self.series_for(app.id, self.metric, True),
        )
        metric = METRIC_MAP[self.metric]
        unit = current.unit if current else metric.unit
        rows, exports = [], []
        self.ui("#metric-hint", Static).update(self.status_for(self.current.get(app.id)))
        for i, day in enumerate(self.period.dates()):
            prior_day = self.comparison.start + timedelta(days=i) if self.comparison else None
            value = current.points.get(day.isoformat()) if current else None
            prior = previous.points.get(prior_day.isoformat()) if previous and prior_day else None
            delta = change(value, prior, unit)
            rows.append(
                (
                    day.isoformat(),
                    [
                        str(day),
                        format_value(value, unit),
                        str(prior_day or "—"),
                        format_value(prior, unit),
                        delta,
                    ],
                )
            )
            exports.append(
                {
                    "app_id": app.id,
                    "app": app.name,
                    "date": str(day),
                    "metric": self.metric,
                    "unit": unit,
                    "value": value,
                    "previous_date": str(prior_day) if prior_day else None,
                    "previous": prior,
                    "change": delta,
                    "availability": current.availability
                    if current
                    else self.current[app.id].status
                    if app.id in self.current
                    else "Loading",
                }
            )
        self.fill_table(
            [
                ("Date", "date"),
                (metric.label, "value"),
                ("Comparison date", "previous_date"),
                ("Comparison value", "previous"),
                ("Change", "change"),
            ],
            rows,
            exports,
        )
        lines = [
            (
                "Current",
                [
                    current.points.get(d.isoformat()) if current else None
                    for d in self.period.dates()
                ],
            )
        ]
        if self.comparison:
            lines.append(
                (
                    "Comparison",
                    [
                        previous.points.get(d.isoformat()) if previous else None
                        for d in self.comparison.dates()
                    ],
                )
            )
        self.ui(TrendChart).set_series(f"{app.name} · {metric.label}", lines, unit)
        self.render_observations(lines, unit)

    def render_compare(self):
        rows, exports, lines, units = [], [], [], set()
        for app in self.apps:
            if app.id not in self.compare_ids:
                continue
            series, prev = (
                self.series_for(app.id, self.metric),
                self.series_for(app.id, self.metric, True),
            )
            value, prior = series.total if series else None, prev.total if prev else None
            unit = series.unit if series else METRIC_MAP[self.metric].unit
            units.add(unit)
            compatible = not prev or not series or prev.unit == series.unit
            delta = (
                value - prior if value is not None and prior is not None and compatible else None
            )
            rows.append(
                (
                    app.id,
                    [
                        app.name,
                        format_value(value, unit),
                        format_value(prior, unit),
                        f"{delta:+.2f} pp"
                        if delta is not None and unit == "percent"
                        else format_value(delta, unit),
                        change(value, prior, unit) if compatible else "—",
                    ],
                )
            )
            exports.append(
                {
                    "app_id": app.id,
                    "app": app.name,
                    "metric": self.metric,
                    "value": value,
                    "unit": unit,
                    "previous": prior,
                    "absolute_change": delta,
                    "change": change(value, prior, unit) if compatible else None,
                    "status": self.status_for(self.current.get(app.id)),
                }
            )
            lines.append(
                (
                    app.name,
                    [
                        series.points.get(d.isoformat()) if series else None
                        for d in self.period.dates()
                    ],
                )
            )
        self.fill_table(
            [
                ("App", "app"),
                ("Current", "current"),
                ("Previous", "previous"),
                ("Absolute change", "delta"),
                ("Change", "change"),
            ],
            rows,
            exports,
        )
        self.ui("#metric-hint", Static).update(
            "Choose two to four distinct apps · shared units and reporting dates"
        )
        self.render_observations(lines, next(iter(units), "count"))
        self.ui(TrendChart).set_series(
            METRIC_MAP[self.metric].label
            if len(units) <= 1
            else "Mixed currencies · chart unavailable",
            lines if len(units) <= 1 else [],
            next(iter(units), "count"),
        )

    def saved_views(self):
        context_key = self.repo.context.key if self.repo else ""
        return self.settings.setdefault("views", {}).setdefault(context_key, {})

    def render_observations(self, lines, unit):
        table = self.ui("#observations", DataTable)
        table.clear(columns=True)
        table.add_column("Reporting date")
        for label, _ in lines:
            table.add_column(Text(label))
        for i, day in enumerate(self.period.dates()):
            table.add_row(
                str(day),
                *(
                    format_value(points[i] if i < len(points) else None, unit)
                    for _, points in lines
                ),
            )

    def render_saved(self):
        rows, exports = [], []
        for name, state in sorted(self.saved_views().items()):
            rows.append(
                (
                    name,
                    [
                        name,
                        state.get("screen", "portfolio"),
                        state.get("preset", "Custom"),
                        METRIC_MAP.get(state.get("metric"), METRICS[0]).label,
                    ],
                )
            )
            exports.append({"name": name, **state})
        self.fill_table(
            [("Name", "name"), ("Screen", "screen"), ("Period", "period"), ("Metric", "metric")],
            rows,
            exports,
        )

    def update_status(self):
        if not self.is_mounted:
            return
        datasets = list(self.current.values()) + list(self.previous.values())
        cached = sum(d.cached for d in datasets)
        failed = sum(
            d.status != Availability.READY
            or d.stale
            or sum(s.total is not None for k, s in d.series().items() if k in METRIC_MAP)
            < len(METRICS)
            for d in datasets
        )
        if self.screen_name == "home":
            available = sum(
                any(s.total is not None for s in d.series().values()) for d in self.current.values()
            )
            self.ui("#status", Static).update(
                f"{'Offline · ' if self.offline else ''}{self.progress} · {available}/{len(self.apps)} apps with data"
                + (f" · {cached} cached; Refresh to update" if cached else "")
                + (f" · {failed} incomplete/unavailable" if failed else "")
            )
            return
        self.ui("#status", Static).update(
            f"{'Offline · ' if self.offline else ''}{self.progress} · {len(self.current)}/{len(self.apps)} apps · {cached} cached · {failed} unavailable/stale · Recent days may be revised"
        )

    def on_tabs_tab_activated(self, event: Tabs.TabActivated):
        if event.tabs.id == "navigation":
            table = self.ui("#table", DataTable)
            if self.screen_name == "portfolio":
                self.portfolio_position = (table.cursor_row, table.scroll_x, table.scroll_y)
            self.screen_name = event.tab.id
            self.render_view()
            if self.screen_name == "portfolio":
                row, x, y = self.portfolio_position
                table.move_cursor(
                    row=min(row, max(0, table.row_count - 1)), animate=False, scroll=False
                )
                table.scroll_to(x=x, y=y, animate=False, force=True)
            self.load_extra()
        elif event.tabs.id == "detail-tabs":
            self.detail_tab = event.tab.id
            self.extra, self.extra_previous = {}, {}
            self.render_view()
            self.load_extra()

    def on_select_changed(self, event: Select.Changed):
        if not self.is_mounted:
            return
        ident = event.select.id
        if event.value is Select.NULL and not (ident and ident.startswith("compare-")):
            return
        if ident == "metric":
            self.metric = str(event.value)
        elif ident == "columns":
            self.columns = str(event.value)
        elif ident == "detail-app":
            if self.selected_app == str(event.value):
                return
            self.selected_app = str(event.value)
            self.extra, self.extra_previous = {}, {}
            self.load_extra()
        elif ident and ident.startswith("compare-"):
            ids = [
                str(s.value)
                for s in self.screen_stack[0].query("#compare-controls Select")
                if s.value is not Select.NULL
            ]
            if len(set(ids)) != len(ids):
                self.notify("Choose distinct apps", severity="warning")
                event.select.value = Select.NULL
                ids = [
                    str(s.value)
                    for s in self.screen_stack[0].query("#compare-controls Select")
                    if s.value is not Select.NULL
                ]
            self.compare_ids = list(dict.fromkeys(ids))
        self.render_view()

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search":
            self.render_view()

    def on_checkbox_changed(self, event: Checkbox.Changed):
        if event.checkbox.id in ("favorites-only", "hide-unavailable"):
            self.render_view()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected):
        if self.screen_name != "portfolio":
            return
        key = str(event.column_key.value)
        self.reverse = not self.reverse if key == self.sort_key else key != "name"
        self.sort_key = key
        self.render_view()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        key = str(event.row_key.value)
        if self.screen_name in ("home", "portfolio", "compare") and key in {
            a.id for a in self.apps
        }:
            self.selected_app = key
            self.ui("#detail-app", Select).value = key
            self.ui("#navigation", Tabs).active = "details"
        elif self.screen_name == "saved":
            self.load_saved(key)
        elif (
            self.screen_name == "details"
            and self.detail_tab != "overview"
            and key.startswith("group:")
        ):
            ds = self.extra.get(self.endpoint())
            if ds:
                group = self.breakdown_rows(ds)[int(key.split(":")[1])]
                lines = [(group[0], [group[3].get(d.isoformat()) for d in self.period.dates()])]
                self.ui(TrendChart).set_series(f"{group[0]} · {group[2]}", lines, group[4])
                self.render_observations(lines, group[4])

    def selected_key(self):
        table = self.ui("#table", DataTable)
        return (
            self.table_keys[table.cursor_row] if table.cursor_row < len(self.table_keys) else None
        )

    def on_button_pressed(self, event: Button.Pressed):
        actions = {
            "previous-day": lambda: self.move_day(-1),
            "next-day": lambda: self.move_day(1),
            "yesterday-button": self.action_yesterday,
            "home-date": self.action_dates,
            "home-refresh": self.action_refresh,
            "explore-button": self.action_explore,
            "today-button": self.action_today,
            "dates-button": self.action_dates,
            "refresh-button": self.action_refresh,
            "retry-button": self.retry_failed,
            "export-button": self.action_export,
            "save-button": self.action_save_view,
            "settings-button": self.action_settings,
            "login-button": self.sign_in,
            "help-button": self.action_help,
            "favorite-button": self.action_favorite,
            "add-compare-button": self.action_add_compare,
            "load-saved": lambda: self.load_saved(self.selected_key()),
            "rename-saved": self.rename_saved,
            "delete-saved": self.delete_saved,
        }
        if event.button.id in actions:
            actions[event.button.id]()

    def action_search(self):
        self.ui("#navigation", Tabs).active = "portfolio"
        self.ui("#search", Input).focus()

    def action_help(self):
        self.push_screen(HelpDialog())

    def action_back(self):
        self.ui("#navigation", Tabs).active = "home"
        self.ui("#table", DataTable).focus()

    def action_explore(self):
        self.ui("#navigation", Tabs).active = "portfolio"

    def action_today(self):
        self.push_screen(
            InfoDialog(
                "Today's data",
                "Pomme currently reads daily App Analytics, not hourly Sales and Trends.\n\nFor recent activity, open App Store Connect → Sales and Trends → select UTC → Last 24 Hours. Apple says hourly units, sales and proceeds are generally available within two hours.\n\nYesterday's daily analytics can be delayed or revised. A returned zero on a recent day is not a guarantee the day is finalized.",
            )
        )

    def set_day(self, day):
        self.period = Period(day, day)
        self.preset = "Yesterday" if self.period == preset_period("Yesterday") else "Custom"
        self.comparison = None
        self.extra, self.extra_previous = {}, {}
        self.current, self.previous = {}, {}
        self.render_view()
        self.load_core()

    def move_day(self, amount):
        day = self.period.end + timedelta(days=amount)
        if day >= reporting_today():
            self.action_today()
            return
        self.set_day(day)

    def action_yesterday(self):
        self.set_day(preset_period("Yesterday").start)

    def action_dates(self):
        if self.screen_name == "home":
            self.push_screen(
                DayDialog(self.period.end), lambda day: self.set_day(day) if day else None
            )
            return

        def apply(value):
            if value:
                self.period, self.comparison, self.preset = value
                self.extra, self.extra_previous = {}, {}
                self.render_view()
                self.load_core()

        self.push_screen(DateDialog(self.period, self.comparison, self.preset), apply)

    def retry_failed(self):
        if self.screen_name == "details" and self.detail_tab != "overview":
            self.load_extra(refresh=True)
        else:
            self.load_core(failed_only=True)

    def action_refresh(self):
        if self.offline:
            self.notify("Offline mode: showing cached data")
        if self.repo:
            if self.screen_name == "details" and self.detail_tab != "overview":
                self.load_extra(refresh=True)
            else:
                self.load_core(refresh=True)
        else:
            self.initialize()

    def action_favorite(self):
        app_id = self.selected_key() if self.screen_name == "portfolio" else self.selected_app
        if app_id not in {a.id for a in self.apps}:
            return
        favorites = self.settings.setdefault("favorites", [])
        favorites.remove(app_id) if app_id in favorites else favorites.append(app_id)
        self.store.save_settings(self.settings)
        self.render_view()

    def action_add_compare(self):
        app_id = self.selected_key() if self.screen_name == "portfolio" else self.selected_app
        if app_id and app_id not in self.compare_ids:
            if len(self.compare_ids) == 4:
                self.notify("Comparison supports up to four apps", severity="warning")
                return
            self.compare_ids.append(app_id)
            self.configure_apps()
        self.ui("#navigation", Tabs).active = "compare"

    def state(self):
        return {
            "screen": self.screen_name if self.screen_name != "saved" else "portfolio",
            "detail_tab": self.detail_tab,
            "preset": self.preset,
            "period": self.period.json(),
            "comparison_mode": "off"
            if self.comparison is None
            else "previous"
            if self.comparison == self.period.previous()
            else "custom",
            "comparison": self.comparison.json() if self.comparison else None,
            "selected_app": self.selected_app,
            "compare_ids": list(self.compare_ids),
            "metric": self.metric,
            "columns": self.columns,
            "search": self.ui("#search", Input).value,
            "favorites_only": self.ui("#favorites-only", Checkbox).value,
            "hide_unavailable": self.ui("#hide-unavailable", Checkbox).value,
            "sort_key": self.sort_key,
            "reverse": self.reverse,
        }

    def action_save_view(self):
        if not self.repo:
            return
        state = self.state()

        def save(name):
            if not name:
                return

            def commit(ok=True):
                if ok:
                    self.saved_views()[name] = state
                    self.store.save_settings(self.settings)
                    self.notify(f"Saved view: {name}")
                    self.render_view()

            if name in self.saved_views():
                self.push_screen(ConfirmDialog(f"Overwrite saved view ‘{name}’?"), commit)
            else:
                commit()

        self.push_screen(
            TextDialog("Save view", hint="Preset dates stay relative; custom dates stay fixed."),
            save,
        )

    def load_saved(self, name):
        state = self.saved_views().get(name)
        if not state:
            return
        try:
            self.preset = state["preset"]
            self.period = (
                Period.parse(state["period"])
                if self.preset == "Custom"
                else preset_period(self.preset)
            )
            self.comparison = (
                None
                if state["comparison_mode"] == "off"
                else Period.parse(state["comparison"])
                if state["comparison_mode"] == "custom"
                else self.period.previous()
            )
        except (ValueError, KeyError) as exc:
            self.notify(f"Cannot load dates: {exc}", severity="error")
            return
        available = {a.id for a in self.apps}
        requested = set(state.get("compare_ids", [])) | {state.get("selected_app")}
        if requested - available - {None}:
            self.notify(
                "Some saved apps are no longer accessible; loading remaining apps",
                severity="warning",
            )
        self.metric, self.columns = (
            state.get("metric", "units"),
            state.get("columns", "Acquisition"),
        )
        self.selected_app = state.get("selected_app")
        self.compare_ids = state.get("compare_ids", [])
        self.configure_apps()
        self.ui("#metric", Select).value = self.metric
        self.ui("#columns", Select).value = self.columns
        self.ui("#search", Input).value = state.get("search", "")
        for selector, field in [
            ("#favorites-only", "favorites_only"),
            ("#hide-unavailable", "hide_unavailable"),
        ]:
            self.ui(selector, Checkbox).value = state.get(field, False)
        self.sort_key, self.reverse = state.get("sort_key", "name"), state.get("reverse", False)
        self.ui("#detail-tabs", Tabs).active = state.get("detail_tab", "overview")
        self.ui("#navigation", Tabs).active = state.get("screen", "portfolio")
        self.extra, self.extra_previous = {}, {}
        self.load_core()

    def rename_saved(self):
        name = self.selected_key()
        if name not in self.saved_views():
            return

        def rename(new):
            if not new or new == name:
                return

            def commit(ok=True):
                if ok:
                    self.saved_views()[new] = self.saved_views().pop(name)
                    self.store.save_settings(self.settings)
                    self.render_view()

            if new in self.saved_views():
                self.push_screen(ConfirmDialog(f"Overwrite saved view ‘{new}’?"), commit)
            else:
                commit()

        self.push_screen(TextDialog("Rename view", name), rename)

    def delete_saved(self):
        name = self.selected_key()
        if name not in self.saved_views():
            return

        def delete(ok):
            if ok:
                self.saved_views().pop(name, None)
                self.store.save_settings(self.settings)
                self.render_view()

        self.push_screen(ConfirmDialog(f"Delete saved view ‘{name}’?"), delete)

    def action_export(self):
        if not self.repo:
            return
        datasets = (
            list(self.extra.values())
            if self.screen_name == "details" and self.detail_tab != "overview"
            else list(self.current.values())
        )
        times = {d.app_id: d.fetched_at for d in datasets}
        rows = [
            {"fetched_at": times.get(row.get("app_id", self.selected_app)), **row}
            for row in self.export_rows
        ]
        metadata = {
            "account": self.repo.context.apple_id,
            "team": self.repo.context.provider_id,
            "screen": self.screen_name,
            "exported_at": now(),
            "start": str(self.period.start),
            "end": str(self.period.end),
            "comparison_start": str(self.comparison.start) if self.comparison else None,
            "comparison_end": str(self.comparison.end) if self.comparison else None,
        }

        def save(value):
            if not value:
                return
            path = Path(value).expanduser()

            def write(overwrite=False):
                try:
                    export_file(path, metadata, rows, overwrite)
                    self.notify(f"Exported {path}")
                except (OSError, ValueError) as exc:
                    self.notify(f"Export failed: {exc}", severity="error")

            if path.exists():
                self.push_screen(
                    ConfirmDialog(f"Overwrite {path}?"), lambda ok: write(True) if ok else None
                )
            else:
                write()

        self.push_screen(
            TextDialog(
                "Export current table",
                str(Path.cwd() / "pomme-export.csv"),
                "Use .csv or .json. Existing files require confirmation.",
            ),
            save,
        )

    def action_settings(self):
        def apply(value):
            if not value:
                return
            if value.get("clear"):

                def clear(ok):
                    if ok and self.repo:
                        self.store.clear(self.repo.context)
                        self.notify("Cleared analytics cache for this account/team")

                self.push_screen(
                    ConfirmDialog(
                        "Clear cached analytics for the current account/team? Saved views remain."
                    ),
                    clear,
                )
            else:
                self.settings.update(value)
                self.theme = value["theme"]
                self.set_class(value["reduced_color"], "reduced")
                self.store.save_settings(self.settings)

        count = self.store.size(self.repo.context) if self.repo else 0
        self.push_screen(SettingsDialog(self.settings, count), apply)

    @work(exclusive=True, group="login")
    async def sign_in(self):
        if self.offline:
            self.notify("Restart without --offline to sign in", severity="warning")
            return
        import subprocess

        args = ["asc", "web", "auth", "login"] + (
            ["--apple-id", self.client.apple_id] if self.client.apple_id else []
        )
        try:
            with self.suspend():
                code = await asyncio.to_thread(lambda: subprocess.run(args, check=False).returncode)
            if code:
                self.notify("Sign-in failed; retry from a terminal", severity="error")
                return
            self.repo = None
            self.initialize()
        except Exception as exc:
            self.notify(f"Cannot run interactive sign-in: {type(exc).__name__}", severity="error")

    async def on_unmount(self):
        if self.repo:
            await self.repo.cancel()
        self.settings["last_view"] = self.view_state
        self.store.save_settings(self.settings)
