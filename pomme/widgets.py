from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class TrendChart(Static):
    """A gap-preserving terminal plot, accompanied by the exact data table."""

    DEFAULT_CSS = "TrendChart { height: 10; border: round $primary-muted; padding: 0 1; }"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.lines: list[tuple[str, list[float | None]]] = []
        self.caption = "Daily trend"
        self.unit = "count"

    def set_series(
        self, caption: str, lines: list[tuple[str, list[float | None]]], unit: str = "count"
    ):
        self.caption, self.lines, self.unit = caption, lines, unit
        self.refresh()

    def render(self) -> Text:
        from .models import format_value

        width = max(10, self.size.width - 18)
        height = 4
        values = [v for _, points in self.lines for v in points if v is not None]
        result = Text(self.caption + "\n", style="bold")
        if not values:
            result.append("No available daily observations\n", style="dim")
            return result
        low, high = min(0, min(values)), max(values)
        if low == high:
            high = low + 1
        grid: dict[tuple[int, int], tuple[str, str]] = {}
        colors = ["cyan", "yellow", "magenta", "green"]
        if getattr(self.app, "settings", {}).get("reduced_color"):
            colors = ["default"] * 4
        symbols = ["●", "◇", "×", "+"]
        n = max((len(points) for _, points in self.lines), default=1)
        for index, (_, points) in enumerate(self.lines):
            for day, value in enumerate(points):
                if value is None:
                    continue
                x = round(day / max(n - 1, 1) * (width - 1))
                y = round((high - value) / (high - low) * (height - 1))
                grid[x, y] = (symbols[index % 4], colors[index % 4])
        for y in range(height):
            label = format_value(high if y == 0 else low, self.unit) if y in (0, height - 1) else ""
            result.append(f"{label:>13} │", style="dim")
            for x in range(width):
                symbol, color = grid.get((x, y), (" ", "default"))
                result.append(symbol, style=color)
            result.append("\n")
        result.append(" " * 14 + f"1{' ' * max(1, width - 10)}Day {n}\n", style="dim")
        for i, (label, _) in enumerate(self.lines):
            result.append(f" {symbols[i % 4]} {label}  ", style=colors[i % 4])
        return result
