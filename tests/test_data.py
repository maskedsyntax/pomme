import asyncio
import json

import pytest
from conftest import metrics_payload

from pomme.adapter import AscClient, AscError, Repository, classify
from pomme.exporting import export_file
from pomme.models import Availability, Context, Dataset
from pomme.storage import Store


async def test_validation_and_retries(period, monkeypatch):
    client = AscClient()
    calls = []

    async def run(*args):
        calls.append(args)
        if len(calls) == 1:
            raise AscError("busy", transient=True)
        return metrics_payload("1", period)

    async def sleep(*args):
        pass

    monkeypatch.setattr(client, "run", run)
    monkeypatch.setattr("pomme.adapter.asyncio.sleep", sleep)
    ds = await client.fetch("1", "metrics", period)
    assert ds.status == Availability.READY and len(calls) == 2
    assert "--frequency" in calls[0]
    wrong = await client.fetch("2", "metrics", period)
    assert wrong.status == Availability.FAILED
    assert "different app" in wrong.message
    assert classify("HTTP status 429").transient
    assert classify("status 403").status == Availability.DENIED


async def test_cache_offline_isolation_and_failed_refresh(store, context, period):
    client = AscClient()

    async def fetch(*args):
        return Dataset("1", "metrics", period, status=Availability.FAILED, message="timeout")

    client.fetch = fetch
    ds = Dataset("1", "metrics", period, metrics_payload("1", period), fetched_at="then")
    store.put(context, ds)
    repo = Repository(client, store, context, offline=True)
    assert (await repo.get("1", "metrics", period)).cached
    assert (await repo.get("2", "metrics", period)).status == Availability.MISSING
    other = Context("someone@example.test", "123", "KEY")
    assert store.get(other, "1", "metrics", period) is None
    repo.offline = False
    stale = await repo.get("1", "metrics", period, refresh=True)
    assert stale.stale and stale.series()["units"].total == 35
    assert store.get(context, "1", "metrics", period).payload == ds.payload


async def test_dedup_and_cancel(store, context, period):
    client = AscClient()
    started = asyncio.Event()
    calls = []

    async def fetch(*args):
        calls.append(args)
        started.set()
        await asyncio.sleep(100)

    client.fetch = fetch
    repo = Repository(client, store, context)
    a = asyncio.create_task(repo.get("1", "metrics", period))
    b = asyncio.create_task(repo.get("1", "metrics", period))
    await started.wait()
    await repo.cancel()
    await asyncio.gather(a, b, return_exceptions=True)
    assert len(calls) == 1 and not repo.pending


def test_export_and_settings(store, tmp_path):
    store.save_settings({"views": {"Named": {"metric": "units"}}})
    assert store.settings()["views"]["Named"]["metric"] == "units"
    path = tmp_path / "data.csv"
    export_file(path, {"start": "2026-01-01"}, [{"app": "=bad", "value": None}])
    assert "'=bad" in path.read_text()
    with pytest.raises(FileExistsError):
        export_file(path, {}, [])
    path = tmp_path / "data.json"
    export_file(path, {}, [{"value": None}])
    assert json.loads(path.read_text())["rows"][0]["value"] is None


def test_schema_rejects_future_version(tmp_path):
    store = Store(tmp_path / "c", tmp_path / "s")
    store.db.execute("PRAGMA user_version=99")
    store.close()
    with pytest.raises(RuntimeError):
        Store(tmp_path / "c", tmp_path / "s")


async def test_auth_team_verification_and_mismatch(monkeypatch):
    client = AscClient()

    async def run(*args, **kwargs):
        if "--help" in args:
            return "metrics sources sales subscriptions"
        if args[:2] == ("auth", "status"):
            return {"credentials": [{"keyId": "KEY", "isDefault": True}]}
        if args[:3] == ("web", "auth", "status"):
            return {"authenticated": True, "appleId": "a@example.test", "providerId": 123}
        return {"keys": [{"keyId": "KEY"}]}

    monkeypatch.setattr(client, "run", run)
    assert (await client.connect()).provider_id == "123"
    client.provider_id = "456"
    with pytest.raises(AscError, match="does not match"):
        await client.connect()


async def test_auth_expired_and_unverifiable(monkeypatch):
    client = AscClient()
    web = {"authenticated": False}

    async def run(*args, **kwargs):
        if "--help" in args:
            return "metrics sources sales subscriptions"
        if args[:2] == ("auth", "status"):
            return {"credentials": [{"keyId": "KEY", "isDefault": True}]}
        if args[:3] == ("web", "auth", "status"):
            return web
        return {"keys": []}

    monkeypatch.setattr(client, "run", run)
    with pytest.raises(AscError, match="Sign in"):
        await client.connect()
    web.update(authenticated=True, appleId="a@example.test", providerId=123)
    with pytest.raises(AscError, match="Cannot verify"):
        await client.connect()


async def test_process_limit_and_cancellation(monkeypatch):
    active, peak = 0, 0
    started = asyncio.Event()
    block = False
    processes = []

    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            nonlocal active, peak
            if self.killed:
                return b"{}", b""
            active += 1
            peak = max(peak, active)
            started.set()
            try:
                await asyncio.sleep(100 if block else 0.01)
                self.returncode = 0
                return b"{}", b""
            finally:
                active -= 1

        def kill(self):
            self.killed = True
            self.returncode = -9

    async def create(*args, **kwargs):
        p = Process()
        processes.append(p)
        return p

    monkeypatch.setattr("pomme.adapter.shutil.which", lambda _: "/fake/asc")
    monkeypatch.setattr("pomme.adapter.asyncio.create_subprocess_exec", create)
    client = AscClient()
    await asyncio.gather(*(client.run("test") for _ in range(9)))
    assert peak == 4
    block = True
    started.clear()
    task = asyncio.create_task(client.run("test"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[-1].killed


async def test_wrong_dates_and_empty_period(period, monkeypatch):
    client = AscClient()
    payload = metrics_payload("1", period)
    payload["endDate"] = "2026-01-09"

    async def run(*args):
        return payload

    monkeypatch.setattr(client, "run", run)
    assert (await client.fetch("1", "metrics", period)).status == Availability.FAILED
    payload["endDate"] = str(period.end)
    payload["result"]["results"] = []
    assert (await client.fetch("1", "metrics", period)).status == Availability.UNPUBLISHED
