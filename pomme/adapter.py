from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
from typing import Any

from .models import METRICS, AppInfo, Availability, Context, Dataset, Period
from .storage import Store, now


class AscError(RuntimeError):
    def __init__(
        self, message: str, status: Availability = Availability.FAILED, transient: bool = False
    ):
        super().__init__(message)
        self.status = status
        self.transient = transient


def classify(detail: str) -> AscError:
    if re.search(r"(?:status|HTTP)\s*[:=]?\s*(429|500|502|503|504)", detail, re.I):
        return AscError("Apple is temporarily busy; retry this dataset", transient=True)
    if re.search(
        r"(?:status|HTTP)\s*[:=]?\s*401|session.*expired|not authenticated|sign.in", detail, re.I
    ):
        return AscError("Web session expired; use Sign in", Availability.DENIED)
    if re.search(r"(?:status|HTTP)\s*[:=]?\s*403", detail, re.I):
        return AscError("Apple denied analytics access (403)", Availability.DENIED)
    return AscError("asc request failed; check CLI authentication and permissions")


class AscClient:
    def __init__(self, apple_id: str | None = None, provider_id: str | None = None):
        self.apple_id = apple_id
        self.provider_id = provider_id
        self.limit = asyncio.Semaphore(4)

    @property
    def scope(self) -> list[str]:
        return (["--apple-id", self.apple_id] if self.apple_id else []) + (
            ["--provider-id", self.provider_id] if self.provider_id else []
        )

    async def run(self, *args: str, json_output: bool = True):
        if not shutil.which("asc"):
            raise AscError("asc is not installed or is not on PATH")
        async with self.limit:
            process = await asyncio.create_subprocess_exec(
                "asc", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 90)
            except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise AscError("asc request timed out", transient=True) from exc
            if process.returncode:
                raise classify(stderr.decode(errors="replace"))
            if not json_output:
                return stdout.decode(errors="replace")
            try:
                payload = json.loads(stdout)
                if not isinstance(payload, dict):
                    raise ValueError("Expected JSON object")
                return payload
            except (ValueError, TypeError) as exc:
                raise AscError("asc returned invalid JSON") from exc

    async def connect(self) -> Context:
        help_text = await self.run("web", "analytics", "--help", json_output=False)
        if not all(name in help_text for name in ("metrics", "sources", "sales", "subscriptions")):
            raise AscError("asc analytics commands are missing; install asc 5.17.1 or newer")
        api = await self.run("auth", "status", "--output", "json")
        web = await self.run(
            "web",
            "auth",
            "status",
            *(["--apple-id", self.apple_id] if self.apple_id else []),
            "--output",
            "json",
        )
        if web.get("authenticated") is not True:
            raise AscError(
                "API credentials checked; Apple web session needs Sign in", Availability.DENIED
            )
        actual_provider = str(web.get("providerId") or web.get("teamId") or "")
        if self.provider_id and self.provider_id != actual_provider:
            raise AscError(
                "Selected provider does not match the active web session. Configure asc for that team first."
            )
        self.apple_id = web.get("appleId") or self.apple_id
        self.provider_id = actual_provider
        if not self.apple_id or not actual_provider:
            raise AscError("asc did not identify the web account and team")
        credentials = api.get("credentials", [])
        selected = next((c for c in credentials if c.get("isDefault")), {})
        key_id = (
            os.environ.get("ASC_KEY_ID")
            if api.get("environmentCredentialsComplete")
            else selected.get("keyId")
        )
        if not key_id:
            raise AscError("API authentication unavailable. Configure asc auth login first.")
        try:
            keys = await self.run("web", "api-keys", "list", *self.scope, "--output", "json")
        except AscError as exc:
            raise AscError(
                "Cannot verify API key membership through asc web api-keys list; check matching credentials and key-list permissions",
                exc.status,
            ) from exc

        def contains_key(obj: Any) -> bool:
            if isinstance(obj, dict):
                return any(
                    str(v) == key_id
                    for k, v in obj.items()
                    if k.lower() in ("id", "keyid", "key_id")
                ) or any(contains_key(v) for v in obj.values())
            return isinstance(obj, list) and any(contains_key(v) for v in obj)

        if not contains_key(keys):
            raise AscError(
                "Cannot verify API key membership in the current web team. Select matching asc credentials before fetching."
            )
        return Context(self.apple_id, actual_provider, str(key_id))

    async def apps(self) -> list[AppInfo]:
        payload = await self.run("apps", "list", "--paginate", "--output", "json")
        try:
            return sorted(
                [AppInfo(a["id"], a["attributes"]["name"]) for a in payload["data"]],
                key=lambda a: a.name.casefold(),
            )
        except (KeyError, TypeError) as exc:
            raise AscError("Unexpected app catalog response") from exc

    async def fetch(self, app_id: str, endpoint: str, period: Period) -> Dataset:
        args = [
            "web",
            "analytics",
            endpoint,
            "--app",
            app_id,
            "--start",
            period.start.isoformat(),
            "--end",
            period.end.isoformat(),
            *self.scope,
            "--output",
            "json",
        ]
        if endpoint == "metrics":
            args += ["--frequency", "day", "--measures", ",".join(m.key for m in METRICS)]
        for attempt in range(3):
            try:
                payload = await self.run(*args)
                if (
                    str(payload.get("appId")) != app_id
                    or payload.get("startDate") != period.start.isoformat()
                    or payload.get("endDate") != period.end.isoformat()
                ):
                    raise AscError("asc returned a different app or reporting range")
                if endpoint == "metrics":
                    result = payload.get("result")
                    records = result.get("results") if isinstance(result, dict) else None
                    if not isinstance(records, list) or any(
                        not isinstance(r, dict) for r in records
                    ):
                        raise AscError("Unexpected metrics response")
                    for record in records:
                        points = record.get("data") or []
                        if not isinstance(points, list) or any(
                            not isinstance(p, dict) or not isinstance(p.get("date"), str)
                            for p in points
                        ):
                            raise AscError("Unexpected daily observations")
                dataset = Dataset(app_id, endpoint, period, payload, fetched_at=now())
                if endpoint == "metrics" and not dataset.series():
                    dataset.status = Availability.UNPUBLISHED
                    dataset.message = "No metrics published for this period"
                return dataset
            except AscError as exc:
                if exc.transient and attempt < 2:
                    await asyncio.sleep(2**attempt + random.uniform(0, 0.5))
                    continue
                if exc.status == Availability.DENIED and "403" in str(exc):
                    if await self.not_live(app_id):
                        return Dataset(
                            app_id,
                            endpoint,
                            period,
                            status=Availability.NOT_LIVE,
                            message="No released App Store version",
                        )
                return Dataset(app_id, endpoint, period, status=exc.status, message=str(exc))
        raise AssertionError("unreachable")

    async def not_live(self, app_id: str) -> bool:
        try:
            payload = await self.run(
                "versions", "list", "--app", app_id, "--paginate", "--output", "json"
            )
            versions = payload.get("data", payload.get("items", []))
            states = [
                v.get("attributes", {}).get(field)
                for v in versions
                for field in ("appVersionState", "appStoreState")
                if v.get("attributes", {}).get(field)
            ]
            # Only known prerelease states justify this explanation; unknown states stay denied.
            prerelease = {
                "PREPARE_FOR_SUBMISSION",
                "READY_FOR_REVIEW",
                "WAITING_FOR_REVIEW",
                "IN_REVIEW",
                "REJECTED",
                "DEVELOPER_REJECTED",
                "METADATA_REJECTED",
                "INVALID_BINARY",
                "PENDING_DEVELOPER_RELEASE",
                "PENDING_APPLE_RELEASE",
                "WAITING_FOR_EXPORT_COMPLIANCE",
            }
            return bool(states) and all(s in prerelease for s in states)
        except AscError:
            return False


class Repository:
    def __init__(self, client: AscClient, store: Store, context: Context, offline: bool = False):
        self.client, self.store, self.context, self.offline = client, store, context, offline
        self.pending: dict[tuple, asyncio.Task] = {}

    async def get(
        self, app_id: str, endpoint: str, period: Period, refresh: bool = False
    ) -> Dataset:
        cached = self.store.get(self.context, app_id, endpoint, period)
        if cached and (not refresh or self.offline):
            return cached
        if self.offline:
            return Dataset(
                app_id,
                endpoint,
                period,
                status=Availability.MISSING,
                message="Not cached for this period (offline)",
            )
        key = (app_id, endpoint, period)
        if key in self.pending and (
            self.pending[key].cancelled() or self.pending[key].cancelling()
        ):
            self.pending.pop(key, None)
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(self.client.fetch(app_id, endpoint, period))
        task = self.pending[key]
        try:
            result = await task
            if result.status == Availability.READY:
                self.store.put(self.context, result)
            elif cached:
                cached.stale = True
                cached.message = f"Refresh failed: {result.message or result.status}"
                return cached
            return result
        finally:
            if self.pending.get(key) is task:
                self.pending.pop(key, None)

    async def cancel(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
