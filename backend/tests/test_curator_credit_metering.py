"""The free curator allowance counts runs that actually worked, and only those.

Two defects met on the same line. The beat called `mark_run` before it asked any
question about the tick, so a tick that resolved as `skipped_credits`,
`skipped_no_credential`, or `skipped_no_changes` had already spent one of the ten
free runs. The gate then read that post-increment return, so the only way it
could refuse was to charge first. Measured on a copy of real data before the fix:
one curator's counter read 22 against 1 run that actually resolved, and the
account was told it had no runs left while nine it never used were still owed.

Nothing here patches `mark_run`, `agent_auth.resolve`, or the gate. The allowance
is reached by *earning* it — one dispatched run per tick through the real beat —
and metering is read off the credential the lane actually dials, which is the
single source of truth this card introduces: a run dialling the customer's own
OpenAI-compatible endpoint costs the platform no tokens, so it is never metered.
Credentials are connected through the real `POST /me/agent-credentials` route, so
the resolver sees a real row rather than a fake.

Only two things are faked, both upstream of every gate under test: the run-lock
broker (CI provisions no Redis, so `FakeRedis`, as elsewhere in this suite) and
the endpoint probe, which is an outbound dial and not a decision.
"""

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from backend.config import settings
from backend.database import get_pool
from backend.services import (
    agent_auth,
    agent_service,
    alert_service,
    memory_service,
    sprite_agent_service,
)
from backend.tasks import agent_schedules
from backend.tasks.agent_schedules import run_curator_now

from .conftest import FakeRedis
from .test_curator import _auth, _register

ALLOWANCE = settings.FREE_CURATOR_RUNS_PER_MONTH
BOX = "http://box-one:11434/v1"


@pytest.fixture(autouse=True)
def _run_lock_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: FakeRedis())


@pytest.fixture(autouse=True)
def _fernet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credential storage is Fernet-encrypted; CI has no INTEGRATIONS_ENCRYPTION_KEY."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
def sprite_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cloud lane's environment: a sprite box, so a connected cloud key
    resolves to its harness. `local` exec mode would answer `NeedsAuth` for a
    scope that owns no endpoint row, which is a different card's state."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")


async def _make_due(agent: dict) -> None:
    """Put the curator one cron tick behind. The beat consumes the tick up
    front, so a repeat has to rewind it — the same helper shape
    `test_curator_no_credential_signal` uses."""
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )


async def _counter(agent_id: uuid.UUID) -> int:
    return (
        await get_pool().fetchrow("SELECT month_run_count FROM agents WHERE id = $1", agent_id)
    )["month_run_count"]


async def _row(agent_id: uuid.UUID) -> dict:
    return await get_pool().fetchrow(
        "SELECT last_run_outcome, last_run_error, last_run_at, month_run_count "
        "FROM agents WHERE id = $1",
        agent_id,
    )


async def _curator_with_work(user_id: uuid.UUID) -> dict:
    """A curator on the beat right now with something to read, so `no_changes`
    can never be the reason a tick stops."""
    agent = await agent_service.get_or_create_curator(user_id)
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    await _make_due(agent)
    return agent


async def _connect_cloud(client: AsyncClient, key: str) -> None:
    """A paid cloud credential via the real connect route: this lane's turns
    cost the platform tokens, so the lane is metered."""
    r = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "anthropic", "api_key": "sk-ant-test-key"},
        headers=_auth(key),
    )
    assert r.status_code == 200, r.text


async def _connect_box(client: AsyncClient, key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The customer's own OpenAI-compatible server, connected for real. The
    probe is the only outbound dial in this file and is faked; storage, reading,
    and resolution all run for real, which is what makes the metering verdict
    the resolver's rather than the test's."""

    async def live(_base_url: str, _api_key: str | None) -> dict:
        return {"ok": True, "http_status": 200, "models": ["llama"], "error_detail": None}

    monkeypatch.setattr(agent_auth, "probe_local_endpoint", live)
    r = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": BOX, "model": "llama"},
        headers=_auth(key),
    )
    assert r.status_code == 200, r.text


async def _capture_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stub the celery task, capturing its arguments. The stub must take
    keywords: the metering answer is a keyword, and `*args` would silently
    swallow the value under test."""
    dispatched: list[tuple] = []

    def _stub(*args, **kwargs):
        dispatched.append((args, kwargs))

    monkeypatch.setattr(agent_schedules.run_scheduled_agent, "delay", _stub)
    return dispatched


async def _work_n_runs(agent: dict, monkeypatch: pytest.MonkeyPatch, wanted: int) -> int:
    """Spend the allowance honestly: drive the real beat `wanted` times, each
    tick dispatching a real run, so the counter says what the lane worked."""
    await _capture_dispatch(monkeypatch)
    for _ in range(wanted):
        await _make_due(agent)
        assert await agent_schedules._run_due() == 1
    return await _counter(agent["id"])


@pytest.mark.asyncio
async def test_a_credits_skip_never_spends_a_run(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC1/AC3 — the defect itself, across two consecutive due ticks.

    The allowance is reached by working for it, so the state under test is a lane
    that genuinely spent all ten. From there the beat must record the stop
    without moving the counter: today it charges first and refuses afterwards, so
    the counter reads 11 then 12 and this test is red before the fix. The
    eleventh tick's clock slot is still consumed, or the next cron tick would
    re-fire a minute later and page the same skip every minute.
    """
    key, uid = await _register(client)
    await _connect_cloud(client, key)
    agent = await _curator_with_work(uid)
    assert await _work_n_runs(agent, monkeypatch, ALLOWANCE) == ALLOWANCE

    await _make_due(agent)
    assert await agent_schedules._run_due() == 0
    after_first = await _row(agent["id"])
    assert after_first["last_run_outcome"] == "skipped_credits"
    assert after_first["month_run_count"] == ALLOWANCE, after_first["month_run_count"]
    tick_slot = after_first["last_run_at"]
    assert tick_slot is not None

    await _make_due(agent)
    assert await agent_schedules._run_due() == 0
    after_second = await _row(agent["id"])

    assert after_second["last_run_outcome"] == "skipped_credits"
    assert after_second["month_run_count"] == ALLOWANCE, after_second["month_run_count"]
    assert after_second["last_run_at"] > tick_slot, "the skipped tick must still consume its slot"


@pytest.mark.asyncio
async def test_a_box_lane_is_unmetered_and_a_cloud_lane_is_metered_on_one_beat(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC2 — one predicate, two opposite answers, on the same tick.

    Both curators are due with real input on one beat. The only difference
    between them is which credential their run dials, so the split in the
    counter can only come from `costs_platform_tokens`. The metering value is read
    off what the beat actually dispatched, and cross-checked against the predicate
    applied to the resolved auth — `mark_run` is never patched, so nothing here
    asserts a value the test itself supplied.
    """
    box_key, box_uid = await _register(client)
    await _connect_box(client, box_key, monkeypatch)
    box_agent = await _curator_with_work(box_uid)

    cloud_key, cloud_uid = await _register(client)
    await _connect_cloud(client, cloud_key)
    cloud_agent = await _curator_with_work(cloud_uid)

    dispatched = await _capture_dispatch(monkeypatch)
    assert await agent_schedules._run_due() == 2

    by_lane = {args[0]: kwargs for args, kwargs in dispatched}
    box_sent = by_lane[str(box_agent["id"])]
    cloud_sent = by_lane[str(cloud_agent["id"])]
    assert box_sent == {"metered": False}, box_sent
    assert cloud_sent == {"metered": True}, cloud_sent

    assert await _counter(box_agent["id"]) == 0, (
        "a lane the platform pays nothing for accrues nothing"
    )
    assert await _counter(cloud_agent["id"]) == 1, "a cloud lane accrues exactly one"
    # The unmetered tick still consumed its clock slot.
    assert (await _row(box_agent["id"]))["last_run_at"] is not None

    # Same predicate, same answers, computed here rather than trusted from above.
    assert agent_auth.costs_platform_tokens(await agent_auth.resolve(box_uid, None)) is False
    assert agent_auth.costs_platform_tokens(await agent_auth.resolve(cloud_uid, None)) is True


@pytest.mark.asyncio
async def test_every_designed_skip_is_net_zero_and_still_consumes_its_tick(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC4 — a designed skip costs nothing and is not a re-fire.

    A lane whose feed is permanently empty must never exhaust an allowance it
    never used, and a lane that cannot run must not buy a tick either. Both still
    consume the clock slot (or the beat re-reports the same skip every minute) and
    both clear a stale `last_run_error` — which is now this function's job, not
    `mark_run`'s, since a skip no longer passes through `mark_run` at all.
    """
    # A lane with nothing to read.
    idle_key, idle_uid = await _register(client)
    await _connect_cloud(client, idle_key)
    idle = await agent_service.get_or_create_curator(idle_uid)
    await get_pool().execute(
        "UPDATE agents SET month_run_count = 3, month_run_anchor = date_trunc('month', now())::date, "
        "last_run_error = 'an old failure nobody cleared' WHERE id = $1",
        idle["id"],
    )
    await _make_due(idle)
    assert await agent_schedules._run_due() == 0
    idle_row = await _row(idle["id"])
    assert idle_row["last_run_outcome"] == "skipped_no_changes"
    assert idle_row["month_run_count"] == 3
    assert idle_row["last_run_error"] is None
    assert idle_row["last_run_at"] is not None

    # A lane with input but nothing runnable to read it with.
    dark_key, dark_uid = await _register(client)
    dark = await _curator_with_work(dark_uid)
    await get_pool().execute(
        "UPDATE agents SET month_run_count = 3, month_run_anchor = date_trunc('month', now())::date, "
        "last_run_error = 'an old failure nobody cleared' WHERE id = $1",
        dark["id"],
    )
    await _make_due(dark)
    assert await agent_schedules._run_due() == 0
    dark_row = await _row(dark["id"])
    assert dark_row["last_run_outcome"] == "skipped_no_credential"
    assert dark_row["month_run_count"] == 3
    assert dark_row["last_run_error"] is None
    assert dark_row["last_run_at"] is not None


@pytest.mark.asyncio
async def test_a_failed_box_run_refunds_nothing_and_a_failed_cloud_run_refunds_one(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC5 — the refund follows the charge, not the crash.

    Threading `metered` down the dispatch is what makes this true: without it the
    run body assumed a metered run and refunded a credit a box lane never paid,
    which would be a new accounting bug standing where the old one was. The cloud
    lane is the positive control — its refund is what
    `test_agent_schedule_alerts.py` already pins; here it is the same lane's own
    number, in and back out.
    """
    box_key, box_uid = await _register(client)
    await _connect_box(client, box_key, monkeypatch)
    box = await _curator_with_work(box_uid)

    cloud_key, cloud_uid = await _register(client)
    await _connect_cloud(client, cloud_key)
    cloud = await _curator_with_work(cloud_uid)

    await _capture_dispatch(monkeypatch)
    assert await agent_schedules._run_due() == 2
    assert await _counter(box["id"]) == 0
    assert await _counter(cloud["id"]) == 1

    async def dead_box(*args, **kwargs):
        raise RuntimeError("the endpoint is down")

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", dead_box)
    monkeypatch.setattr(alert_service, "alerts_enabled", lambda: False)

    await agent_schedules._run_scheduled_agent(box["id"], "202611131200", metered=False)
    await agent_schedules._run_scheduled_agent(cloud["id"], "202611131201", metered=True)

    box_row = await _row(box["id"])
    cloud_row = await _row(cloud["id"])
    assert box_row["last_run_outcome"] == "failed" and cloud_row["last_run_outcome"] == "failed"
    assert box_row["month_run_count"] == 0, (
        "an uncharged lane cannot be refunded below what it paid"
    )
    assert cloud_row["month_run_count"] == 0, "the one credit the cloud lane took comes back"


def test_the_run_guard_threads_the_metering_answer_to_the_escape_guard(
    monkeypatch: pytest.MonkeyPatch,
):
    """AC5, second half — a crash that escapes the coroutine still knows who paid.

    `SoftTimeLimitExceeded` lands outside every frame of the run body, so the
    guard is the only code that resolves such a lane. It owns no opinion about
    metering — it forwards the value it was dispatched with, and this pins that it
    forwards rather than defaults.
    """
    seen: list[bool] = []

    def fake_guard_resolution(agent_uuid, exc, *, metered):
        seen.append(metered)

    async def never_runs():
        return None

    def fake_run_async(coro):
        coro.close()  # the escape is the subject; no database is touched here
        raise KeyboardInterrupt("soft time limit")

    monkeypatch.setattr(agent_schedules, "_resolve_escaped_run", fake_guard_resolution)
    monkeypatch.setattr(agent_schedules, "run_async", fake_run_async)

    with pytest.raises(KeyboardInterrupt):
        agent_schedules._run_guarded(uuid.uuid4(), False, never_runs())
    assert seen == [False]


@pytest.mark.asyncio
async def test_an_escaped_run_returns_only_the_credit_its_lane_took(client: AsyncClient, _db_pool):
    """AC5, third quarter — the shared resolution write honours the same flag.

    The escape guard and the stranded-run sweep resolve a `started` lane through
    one statement, so the flag has to mean the same thing in both: refund a lane
    that charged, leave a lane that never did.
    """
    key, uid = await _register(client)
    agent = await _curator_with_work(uid)

    await get_pool().execute(
        "UPDATE agents SET month_run_count = 4, month_run_anchor = date_trunc('month', now())::date, "
        "last_run_outcome = 'started' WHERE id = $1",
        agent["id"],
    )
    await agent_service.resolve_started_runs([agent["id"]], "aborted", metered=False)
    assert await _counter(agent["id"]) == 4
    assert (await _row(agent["id"]))["last_run_outcome"] == "failed"

    # The write is a compare-and-set on `started`, so the second rescuer has to be
    # handed a lane that is started again — the same row, the other flag.
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = 'started' WHERE id = $1", agent["id"]
    )
    await agent_service.resolve_started_runs([agent["id"]], "aborted", metered=True)
    assert await _counter(agent["id"]) == 3


@pytest.mark.asyncio
async def test_a_spent_allowance_with_no_runnable_credential_reports_the_credential(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch
):
    """AC10 — the one reason this card changes, pinned as a decision.

    Both states are true at once here, and they used to report differently: the
    credit gate stood first in the beat, so a lane with no credential at all was
    told its allowance was spent (real data: 9 of the 11 past-cap rows on the
    copied database own zero credential rows). The metering answer has to be
    resolved before the gate can run at all, so the actionable reason now wins.
    The counter does not move either way — this is a reporting change and nothing
    else.
    """
    key, uid = await _register(client)
    agent = await _curator_with_work(uid)
    await get_pool().execute(
        "UPDATE agents SET month_run_count = $2, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        agent["id"],
        ALLOWANCE,
    )

    await _make_due(agent)
    assert await agent_schedules._run_due() == 0

    row = await _row(agent["id"])
    assert row["last_run_outcome"] == "skipped_no_credential"
    assert row["month_run_count"] == ALLOWANCE, "neither refusal charges"


@pytest.mark.asyncio
async def test_the_run_now_route_names_the_credential_when_both_are_true(
    client: AsyncClient, _db_pool
):
    """AC10, route half — the same reorder, visible in a response body.

    The status code stays 402 for both refusals, so the assertion has to be on the
    text: the user with no credential and no allowance used to be told to upgrade
    to Pro, which is not what is wrong.
    """
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    await client.post(
        "/api/v1/me/pages/new", json={"name": "N", "content": "x"}, headers=_auth(key)
    )
    await get_pool().execute(
        "UPDATE agents SET month_run_count = $2, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        UUID(curator["id"]),
        ALLOWANCE,
    )

    r = await client.post("/api/v1/me/memory/recompute", headers=_auth(key))
    assert r.status_code == 402
    assert "Connect a Claude, Codex, or OpenRouter key" in r.json()["detail"]
    assert "Free accounts get" not in r.json()["detail"]
    assert await _counter(curator["id"]) == ALLOWANCE


@pytest.mark.asyncio
async def test_a_manual_run_on_a_box_lane_spends_no_allowance(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """The route answers the metering question with the same predicate the beat uses.

    A curator already at the cap that costs the platform nothing is still allowed
    to run: the allowance exists to ration tokens the platform buys, and this is
    the same decision as AC2 arriving through the other door.
    """
    key, uid = await _register(client)
    await _connect_box(client, key, monkeypatch)
    curator = await _curator_with_work(uid)
    await get_pool().execute(
        "UPDATE agents SET month_run_count = $2, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        UUID(curator["id"]),
        ALLOWANCE,
    )

    calls: list[tuple[tuple, dict]] = []

    def stub(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(run_curator_now, "delay", stub)
    r = await client.post("/api/v1/me/memory/recompute", headers=_auth(key))

    assert r.status_code == 202, r.text
    assert calls == [((str(curator["id"]),), {"metered": False})], calls
    assert await _counter(curator["id"]) == ALLOWANCE


@pytest.mark.asyncio
async def test_a_box_run_on_a_paused_curator_works_unmetered_and_a_platform_pass_does_not(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC11 — the guard reads the trigger, so an unmetered user run is not a no-op.

    `_run_curator_locked` used to infer "the platform triggered this" from
    `metered=False`. Once a user's own manual run can be unmetered, that inference
    would make a legitimate run on a curator whose switch is off return success
    while doing nothing — the exact silent-success class this card exists to
    remove. Same row, same unmetered flag, opposite outcomes, decided only by
    `platform_initiated`.
    """
    key, uid = await _register(client)
    await _connect_box(client, key, monkeypatch)
    curator = await _curator_with_work(uid)
    await get_pool().execute("UPDATE agents SET run_mode = 'chat' WHERE id = $1", curator["id"])
    agent = await agent_service.get_agent_by_id(curator["id"])
    assert agent["run_mode"] == "chat" and agent["is_curator"]

    async def idle_turn(*args, **kwargs):
        return None

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", idle_turn)
    monkeypatch.setattr(alert_service, "alerts_enabled", lambda: False)

    # The user's own run: unmetered, and it must actually run.
    await agent_schedules._run_curator_locked(agent, None, False, False, 0, False, False)
    assert (await _row(curator["id"]))["last_run_outcome"] == "ran"

    # The platform's pass over the same switched-off curator: still declines, and
    # declines without resolving the row as anything.
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = NULL, last_run_error = NULL WHERE id = $1",
        curator["id"],
    )
    await agent_schedules._run_curator_locked(agent, None, False, False, 0, False, True)
    assert (await _row(curator["id"]))["last_run_outcome"] is None


@pytest.mark.asyncio
async def test_a_non_curator_scheduled_agent_is_metered_by_the_same_predicate(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """AC15 — one counter meaning, no curator-only exception around it.

    The counter means worked runs for every scheduled agent; only the allowance
    GATE is curator-only, and it stayed where it was. The consequence for a
    non-curator on a box lane is that it stops accruing a number nothing gates on:
    on the copied database that is 0 rows (printed in the evidence file), so this
    pins the behaviour rather than repairing data.
    """
    box_key, box_uid = await _register(client)
    await _connect_box(client, box_key, monkeypatch)
    box_lane = await _curator_with_work(box_uid)
    await get_pool().execute(
        "UPDATE agents SET is_curator = false, schedule_prompt = 'stand up the daily note' "
        "WHERE id = $1",
        box_lane["id"],
    )

    cloud_key, cloud_uid = await _register(client)
    await _connect_cloud(client, cloud_key)
    cloud_lane = await _curator_with_work(cloud_uid)
    await get_pool().execute(
        "UPDATE agents SET is_curator = false, schedule_prompt = 'stand up the daily note' "
        "WHERE id = $1",
        cloud_lane["id"],
    )

    await _capture_dispatch(monkeypatch)
    assert await agent_schedules._run_due() == 2
    assert await _counter(box_lane["id"]) == 0
    assert await _counter(cloud_lane["id"]) == 1

    # And the allowance gate still binds only curators: a non-curator past the cap
    # dispatches, because nothing about its counter was ever a gate.
    await get_pool().execute(
        "UPDATE agents SET month_run_count = $2, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        box_lane["id"],
        ALLOWANCE * 3,
    )
    reloaded = await agent_service.get_agent_by_id(box_lane["id"])
    assert reloaded["is_curator"] is False
    await _make_due(box_lane)
    assert await agent_schedules._run_due() == 1
