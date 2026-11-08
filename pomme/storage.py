from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_cache_path, user_config_path

from .models import AppInfo, Context, Dataset, Period

SCHEMA = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, cache_dir: Path | None = None, config_dir: Path | None = None):
        self.cache_dir = cache_dir or Path(
            os.environ.get("POMME_CACHE_DIR") or user_cache_path("pomme")
        )
        self.config_dir = config_dir or Path(
            os.environ.get("POMME_CONFIG_DIR") or user_config_path("pomme")
        )
        for path in (self.cache_dir, self.config_dir):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.cache_dir / "analytics.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS datasets (context TEXT, query TEXT, payload TEXT, fetched TEXT, PRIMARY KEY(context,query))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS catalogs (context TEXT PRIMARY KEY, apps TEXT, fetched TEXT)"
        )
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA):
            self.db.close()
            raise RuntimeError(
                "Cache schema is newer than this Pomme version; use a different POMME_CACHE_DIR"
            )
        self.db.execute(f"PRAGMA user_version={SCHEMA}")
        self.db.commit()
        self.settings_path = self.config_dir / "settings.json"

    def close(self):
        self.db.close()

    def settings(self) -> dict:
        try:
            value = json.loads(self.settings_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self, settings: dict):
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.settings_path)

    @staticmethod
    def query(app_id: str, endpoint: str, period: Period) -> str:
        return json.dumps(
            [SCHEMA, app_id, endpoint, period.json(), "day", "core-nine"], sort_keys=True
        )

    def get(self, context: Context, app_id: str, endpoint: str, period: Period) -> Dataset | None:
        row = self.db.execute(
            "SELECT payload,fetched FROM datasets WHERE context=? AND query=?",
            (context.key, self.query(app_id, endpoint, period)),
        ).fetchone()
        if row:
            return Dataset(
                app_id, endpoint, period, json.loads(row[0]), fetched_at=row[1], cached=True
            )
        return None

    def put(self, context: Context, dataset: Dataset):
        self.db.execute(
            "INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)",
            (
                context.key,
                self.query(dataset.app_id, dataset.endpoint, dataset.period),
                json.dumps(dataset.payload),
                dataset.fetched_at,
            ),
        )
        self.db.commit()

    def catalog(self, context: Context, apps: list[AppInfo] | None = None) -> list[AppInfo]:
        if apps is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO catalogs VALUES (?,?,?)",
                (context.key, json.dumps([asdict(a) for a in apps]), now()),
            )
            self.db.commit()
            return apps
        row = self.db.execute(
            "SELECT apps FROM catalogs WHERE context=?", (context.key,)
        ).fetchone()
        return [AppInfo(**a) for a in json.loads(row[0])] if row else []

    def clear(self, context: Context):
        self.db.execute("DELETE FROM datasets WHERE context=?", (context.key,))
        self.db.commit()

    def size(self, context: Context) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM datasets WHERE context=?", (context.key,)
        ).fetchone()[0]
