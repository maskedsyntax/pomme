from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from .models import Period, preset_period


class Dialog(ModalScreen):
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $background 70%; }
    Dialog > Vertical, Dialog > VerticalScroll { width: 74; max-width: 95%; height: auto; max-height: 95%; border: thick $primary; background: $surface; padding: 1 2; }
    Dialog Input, Dialog Select { margin-bottom: 1; }
    Dialog Horizontal { height: auto; align-horizontal: right; }
    Dialog Button { margin-left: 1; }
    Dialog .error { color: $error; height: auto; }
    """
    BINDINGS = [("escape", "dismiss(None)", "Cancel")]


class DateDialog(Dialog):
    def __init__(self, period: Period, comparison: Period | None, preset: str):
        super().__init__()
        self.period, self.comparison, self.preset = period, comparison, preset

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Reporting dates · UTC")
            yield Select(
                [
                    (v, v)
                    for v in [
                        "Yesterday",
                        "Last 7 days",
                        "Last 30 days",
                        "Month to date",
                        "Previous month",
                        "Custom",
                    ]
                ],
                value=self.preset,
                id="preset",
            )
            yield Input(self.period.start.isoformat(), placeholder="Start YYYY-MM-DD", id="start")
            yield Input(self.period.end.isoformat(), placeholder="End YYYY-MM-DD", id="end")
            mode = (
                "Off"
                if self.comparison is None
                else ("Previous period" if self.comparison == self.period.previous() else "Custom")
            )
            yield Select(
                [(v, v) for v in ["Previous period", "Custom", "Off"]], value=mode, id="mode"
            )
            yield Input(
                self.comparison.start.isoformat() if self.comparison else "",
                placeholder="Comparison start YYYY-MM-DD",
                id="compare-start",
            )
            yield Input(
                self.comparison.end.isoformat() if self.comparison else "",
                placeholder="Comparison end YYYY-MM-DD",
                id="compare-end",
            )
            yield Static("Ranges are inclusive; today is not a completed reporting day.")
            yield Static("", classes="error", id="error")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Apply", variant="primary", id="apply")

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "preset" and event.value != "Custom":
            try:
                p = preset_period(str(event.value))
                self.query_one("#start", Input).value = p.start.isoformat()
                self.query_one("#end", Input).value = p.end.isoformat()
            except ValueError as exc:
                self.query_one("#error", Static).update(str(exc))

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            p = Period(
                date.fromisoformat(self.query_one("#start", Input).value),
                date.fromisoformat(self.query_one("#end", Input).value),
            )
            preset = str(self.query_one("#preset", Select).value)
            if preset != "Custom" and preset_period(preset) != p:
                preset = "Custom"
            mode = self.query_one("#mode", Select).value
            comparison = None if mode == "Off" else p.previous()
            if mode == "Custom":
                comparison = Period(
                    date.fromisoformat(self.query_one("#compare-start", Input).value),
                    date.fromisoformat(self.query_one("#compare-end", Input).value),
                )
                if comparison.days != p.days:
                    raise ValueError("Comparison must contain the same number of days")
            self.dismiss((p, comparison, preset))
        except ValueError as exc:
            self.query_one("#error", Static).update(str(exc))


class TextDialog(Dialog):
    def __init__(self, title: str, value: str = "", hint: str = ""):
        super().__init__()
        self.title_text, self.value, self.hint = title, value, hint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            yield Static(self.hint, markup=False)
            yield Input(self.value, id="value")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="save")

    def on_input_submitted(self):
        self.submit()

    def on_button_pressed(self, event: Button.Pressed):
        self.submit() if event.button.id == "save" else self.dismiss(None)

    def submit(self):
        value = self.query_one(Input).value.strip()
        if value:
            self.dismiss(value)


class ConfirmDialog(Dialog):
    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self):
        with Vertical():
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Confirm", variant="warning", id="confirm")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm")


class HelpDialog(Dialog):
    def compose(self):
        with VerticalScroll():
            yield Static(
                "Pomme · Keyboard help\n\nArrow keys: navigate tables · Enter: open app/view\nTab / Shift+Tab: move focus · Escape: back/close\n/: search · d: dates · r: refresh · e: export\nf: favorite selected app · a: add selected app to comparison\ns: save view · Ctrl+p: command palette · q: quit\n\nDates use UTC reporting days. Recent data may be revised.\n— means unavailable, never zero. Totals show available coverage.\nConversion rates are Apple’s reported percentages; pp means percentage points.\nSources show unique page views, not attributed downloads.\nUSD amounts are Apple estimates, not financial statements.\n\nCached queries reopen without a network request. Refresh explicitly.\nAcquisition, sales, and subscriptions load when opened."
            )
            yield Button("Close", id="close")

    def on_button_pressed(self):
        self.dismiss(None)


class SettingsDialog(Dialog):
    def __init__(self, settings: dict, count: int):
        super().__init__()
        self.settings, self.count = settings, count

    def compose(self):
        with Vertical():
            yield Label("Settings")
            yield Select(
                [(v, v) for v in ["textual-dark", "textual-light", "nord", "gruvbox"]],
                value=self.settings.get("theme", "textual-dark"),
                id="theme-choice",
            )
            yield Checkbox(
                "Reduced color", value=self.settings.get("reduced_color", False), id="reduced"
            )
            yield Static(f"{self.count} cached datasets for this account/team")
            with Horizontal():
                yield Button("Clear cache", variant="warning", id="clear")
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="save")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "clear":
            self.dismiss({"clear": True})
        elif event.button.id == "save":
            self.dismiss(
                {
                    "theme": str(self.query_one("#theme-choice", Select).value),
                    "reduced_color": self.query_one("#reduced", Checkbox).value,
                }
            )
        else:
            self.dismiss(None)


class InfoDialog(Dialog):
    def __init__(self, title: str, message: str):
        super().__init__()
        self.title_text, self.message = title, message

    def compose(self):
        with VerticalScroll():
            yield Label(self.title_text)
            yield Static(self.message, markup=False)
            yield Button("Close", id="close")

    def on_button_pressed(self):
        self.dismiss(None)


class DayDialog(Dialog):
    def __init__(self, day: date):
        super().__init__()
        self.day = day

    def compose(self):
        with Vertical():
            yield Label("Choose a reporting day · UTC")
            yield Input(self.day.isoformat(), placeholder="YYYY-MM-DD", id="day")
            yield Static("", classes="error", id="error")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Apply", variant="primary", id="apply")

    def on_input_submitted(self):
        self.apply()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.apply()

    def apply(self):
        try:
            day = date.fromisoformat(self.query_one("#day", Input).value)
            Period(day, day)
            self.dismiss(day)
        except ValueError as exc:
            self.query_one("#error", Static).update(str(exc))
