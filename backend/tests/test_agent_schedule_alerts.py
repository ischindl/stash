"""Scheduled-agent failures must page an operator, not just log.

The Memory curator failed silently for four days in July 2026 (the managed
harness broke; the only trace was an ERROR line in celery logs nobody reads).
These tests pin the two alert paths that prevent a repeat: per-run failure
alerts, and the daily stale-watermark watchdog.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from backend.config import settings
from backend.database import get_pool
from backend.integrations.slack import client as slack_client
from backend.services import agent_auth, agent_service, alert_service, memory_service
from backend.tasks import agent_schedules

from .conftest import unique_name
from .test_source_sync_claims import _configure_alerts, _disable_alerts


async def _register(client: AsyncClient) -> uuid.UUID:
    name = unique_name("alerts")
    response = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@example.com"},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["id"])


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # Patching `send_alert` is a claim about configured mode: an installation
    # with no alert destination is never asked to deliver at all (STAS-268).
    # Declare the mode instead of reading the ambient value.
    _configure_alerts(monkeypatch)
    sent: list[str] = []

    async def fake_send_alert(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(alert_service, "send_alert", fake_send_alert)
    return sent


def _no_delivery(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The Slack transport, watched rather than stubbed: `send_alert` is left
    real here, so the only way this stays unawaited is that nobody asked for a
    delivery at all."""
    post = AsyncMock()
    monkeypatch.setattr(slack_client, "post_message", post)
    return post


async def _failing_scheduled_run(
    client: AsyncClient, sprite_exec, monkeypatch: pytest.MonkeyPatch, *, cause: str
) -> tuple[uuid.UUID, str, list[str]]:
    """Put one scheduled curator on the beat, let its turn die with `cause`, and
    hand back the dispatch so the caller can run the beat's own failure
    handler. Nothing about alerting is patched: the test decides the alert mode
    and reads whatever the handler does. The third element is the lane locks
    still held at the moment the run failed — the baseline that makes "the lock
    was released" a claim about a lock this run really took."""
    from backend.services import agent_auth, sprite_agent_service

    user_id = await _register(client)
    agent = await agent_service.get_or_create_curator(user_id)
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    held_at_failure: list[str] = []

    async def resolved_credential(user_id, prefer_provider=None, model_id=None, credential_id=None):
        return _runnable_cloud_auth()

    async def failed_turn(agent, stamp, read_position):
        held_at_failure.extend(
            key for key in sprite_exec.redis.data if key.startswith("agent-run:")
        )
        raise RuntimeError(cause)

    monkeypatch.setattr(agent_auth, "resolve", resolved_credential)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", failed_turn)
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        agent_schedules.run_scheduled_agent,
        "delay",
        lambda *args, **kwargs: dispatched.append(args),
    )
    assert await agent_schedules._run_due() == 1
    return uuid.UUID(dispatched[0][0]), dispatched[0][1], held_at_failure


@pytest.mark.asyncio
async def test_queued_curator_does_not_run_after_pause(client, pool, sprite_exec, monkeypatch):
    # `sprite_exec` is requested for its fake Redis, not for a harness turn: the
    # run fence this body takes is a Redis key, and this test never gets as far as
    # executing anything.
    from backend.services import sprite_agent_service

    user_id = await _register(client)
    agent = await agent_service.get_or_create_curator(user_id)
    await pool.execute("UPDATE agents SET run_mode='chat' WHERE id=$1", agent["id"])

    async def forbidden(*args):
        pytest.fail("A queued job must respect a subsequent pause")

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", forbidden)
    await agent_schedules._run_scheduled_agent(agent["id"], "test")


async def _make_curator(
    user_id: uuid.UUID, *, curated_hours_ago: int, last_run_error: str | None
) -> dict:
    agent = await agent_service.get_or_create_curator(user_id)
    last_run_outcome = "failed" if last_run_error is not None else None
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2, last_run_error = $3, "
        "last_run_outcome = $4 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(hours=curated_hours_ago),
        last_run_error,
        last_run_outcome,
    )
    return agent


@pytest.mark.asyncio
async def test_stale_failing_curator_alerts(client: AsyncClient, monkeypatch):
    user_id = await _register(client)
    await _make_curator(user_id, curated_hours_ago=72, last_run_error="opencode error")
    # A pending change — staleness only matters when there is work to curate.
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 1
    assert len(sent) == 1
    assert "stale" in sent[0] and "opencode error" in sent[0]


@pytest.mark.asyncio
async def test_skipped_by_design_curator_stays_quiet(client: AsyncClient, monkeypatch):
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = 'skipped_no_changes' WHERE id = $1", agent["id"]
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_stale_curator_with_unresolved_run_alerts(client: AsyncClient, monkeypatch):
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = 'started' WHERE id = $1", agent["id"]
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 1
    assert len(sent) == 1 and "never resolved" in sent[0]


@pytest.mark.asyncio
async def test_disabled_mode_stale_curator_is_recorded_not_delivered(
    client: AsyncClient, monkeypatch, caplog
):
    """The daily curator watchdog is the other alert path on an installation
    that sends no alerts: it must reach its verdict and record it without
    raising, or a worker with no Slack credentials logs a fresh traceback every
    day forever and never says which curator went stale."""
    user_id = await _register(client)
    await _make_curator(user_id, curated_hours_ago=72, last_run_error="opencode error")
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    _disable_alerts(monkeypatch)
    post = _no_delivery(monkeypatch)

    with caplog.at_level(logging.INFO):
        assert await agent_schedules._alert_stale_curators() == 1

    assert post.await_count == 0
    assert [r for r in caplog.records if r.name == "backend.services.alert_service"] == []
    # The watchdog's decision stays in the log naming the curator and its cause.
    recorded = [
        r.getMessage()
        for r in caplog.records
        if r.name == "backend.tasks.agent_schedules" and r.levelno == logging.ERROR
    ]
    assert any("Memory curator" in m and "opencode error" in m for m in recorded)


@pytest.mark.asyncio
async def test_stale_curator_without_pending_changes_stays_quiet(client: AsyncClient, monkeypatch):
    # An idle user's curator legitimately never advances — nothing to curate.
    user_id = await _register(client)
    await _make_curator(user_id, curated_hours_ago=72, last_run_error="opencode error")
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_fresh_curator_stays_quiet(client: AsyncClient, monkeypatch):
    # One bad night must not page — the watchdog only fires past the 48h bar.
    user_id = await _register(client)
    await _make_curator(user_id, curated_hours_ago=1, last_run_error="opencode error")
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_successful_but_backlogged_curator_still_alerts(client, pool, monkeypatch):
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await agent_service.mark_run_succeeded(agent["id"])
    await memory_service.push_event(
        user_id, "test", "user_message", "pending", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)
    assert await agent_schedules._alert_stale_curators() == 1
    assert "backlog remains after a successful batch" in sent[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["progress", "caught_up", "no_progress", "paused", "exhausted", "failed", "free"]
)
async def test_scoped_curator_catchup_is_bounded_and_requires_progress(
    client, pool, monkeypatch, mode
):
    from backend.services import billing_service, scoped_curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    agent = await agent_service.get_agent_by_id(agent["id"])
    started = datetime.now(UTC)
    before = agent["curated_through"]
    through = started if mode == "caught_up" else before + timedelta(hours=1)
    if mode == "no_progress":
        through = before
    await pool.execute(
        "UPDATE agents SET curated_through=$2,run_mode=$3,last_run_outcome=$4 WHERE id=$1",
        agent["id"],
        through,
        "chat" if mode == "paused" else "scheduled",
        "failed" if mode == "failed" else "ran",
    )

    async def workspace(_):
        return {"id": uuid.uuid4()}

    async def entitled(_):
        return mode != "free"

    monkeypatch.setattr(billing_service, "is_pro", entitled)
    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)

    async def pending(*args):
        return mode != "caught_up"

    monkeypatch.setattr(agent_schedules, "_pending_input", pending)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    await agent_schedules._queue_curator_catchup(agent, started, 0 if mode == "exhausted" else 2)
    if mode == "progress":
        assert queued == [
            {
                "args": [str(agent["id"])],
                "kwargs": {
                    "metered": False,
                    "platform_initiated": True,
                    "catchup_remaining": 1,
                    "catchup": True,
                },
                "countdown": 15,
            }
        ]
    else:
        assert queued == []


def _runnable_cloud_auth() -> agent_auth.RunAuth:
    """A resolved credential for a faked `agent_auth.resolve`.

    These fakes patch the resolver only to say "this lane is runnable", and used
    to return `None` because the call was awaited purely for its side effect.
    `require_run_auth` now answers the metering question off the auth it got back
    — no endpoint means a cloud key the platform funds, so metered — which makes
    the returned object load-bearing. A real `RunAuth` is the honest stub; the
    alternative would be a `None` guard that pretends to know the answer."""
    from backend.services import harness as harness_mod

    return agent_auth.RunAuth(harness=harness_mod.CLAUDE)


@pytest.mark.asyncio
async def test_run_due_failure_sends_alert(client: AsyncClient, sprite_exec, monkeypatch):
    from backend.services import agent_auth, sprite_agent_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    # Make the curator due on the next tick and eligible to run.
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )

    async def fake_resolve(user_id, prefer_provider=None, model_id=None, credential_id=None):
        return _runnable_cloud_auth()

    async def fake_run_scheduled(agent, stamp, read_position):
        raise RuntimeError("agent turn failed: opencode error")

    monkeypatch.setattr(agent_auth, "resolve", fake_resolve)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_run_scheduled)
    sent = _capture_alerts(monkeypatch)

    dispatched = []
    monkeypatch.setattr(
        agent_schedules.run_scheduled_agent,
        "delay",
        lambda *args, **kwargs: dispatched.append(args),
    )
    assert await agent_schedules._run_due() == 1
    await agent_schedules._run_scheduled_agent(uuid.UUID(dispatched[0][0]), dispatched[0][1])
    assert len(sent) == 1
    assert "Scheduled agent run failed" in sent[0] and "opencode error" in sent[0]
    # The failure is also stored on the agent, which is what the stale-curator
    # watchdog keys off — the two alert paths must stay connected.
    row = await get_pool().fetchrow(
        "SELECT last_run_error, last_run_outcome FROM agents WHERE id = $1", agent["id"]
    )
    assert row["last_run_error"] is not None and "opencode error" in row["last_run_error"]
    assert row["last_run_outcome"] == "failed"


@pytest.mark.asyncio
async def test_run_bookkeeping_failure_sends_alert(client: AsyncClient, sprite_exec, monkeypatch):
    """A run whose post-turn bookkeeping fails must record last_run_error and
    alert, exactly like a failed turn — otherwise the watermark silently stops
    advancing with no trace on the agent row."""
    # The run lock needs Redis; sprite_exec is what supplies the fake.
    from backend.services import curation_service, sprite_agent_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)

    async def fake_run_scheduled(agent, stamp, read_position):
        return ""

    async def boom(user_id, position, now, wiki, folder_id=None):
        raise RuntimeError("watermark write failed")

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_run_scheduled)
    monkeypatch.setattr(curation_service, "complete_through", boom)
    sent = _capture_alerts(monkeypatch)

    await agent_schedules._run_scheduled_agent(uuid.UUID(agent["id"]), "202608091200")

    assert len(sent) == 1 and "watermark write failed" in sent[0]
    row = await get_pool().fetchrow(
        "SELECT last_run_error, last_run_outcome FROM agents WHERE id = $1", agent["id"]
    )
    assert row["last_run_error"] is not None and "watermark write failed" in row["last_run_error"]
    assert row["last_run_outcome"] == "failed"


@pytest.mark.asyncio
async def test_disabled_mode_run_failure_records_without_alerting(
    client: AsyncClient, sprite_exec, monkeypatch, caplog
):
    """An installation with no alert destination is in a mode, not in trouble.

    The failure handler used to call `send_alert` unconditionally, so on such a
    stack the operator's log ended in `RuntimeError: ALERT_SLACK_TEAM_ID and
    ALERT_SLACK_CHANNEL_ID are required` — a traceback from the code that tried
    to report, naming a Slack problem nobody had, while the agent's own failure
    was the thing being swallowed. Everything durable still happens: the row
    carries the agent's cause and the lane lock is released.
    """
    agent_id, stamp, held_at_failure = await _failing_scheduled_run(
        client, sprite_exec, monkeypatch, cause="agent turn failed: opencode error"
    )
    _disable_alerts(monkeypatch)
    post = _no_delivery(monkeypatch)

    with caplog.at_level(logging.ERROR, logger="backend.services.alert_service"):
        await agent_schedules._run_scheduled_agent(agent_id, stamp)

    row = await get_pool().fetchrow(
        "SELECT last_run_error, last_run_outcome FROM agents WHERE id = $1", agent_id
    )
    assert row["last_run_outcome"] == "failed"
    assert "agent turn failed: opencode error" in row["last_run_error"]
    assert held_at_failure == [f"agent-run:{agent_id}"]
    assert f"agent-run:{agent_id}" not in sprite_exec.redis.data
    assert post.await_count == 0
    # `send_alert` logs `ALERT: …` before it delivers, so silence from that
    # logger is the proof that no delivery was even attempted.
    assert [r for r in caplog.records if r.name == "backend.services.alert_service"] == []


@pytest.mark.asyncio
async def test_configured_delivery_failure_still_raises_after_the_run_is_recorded(
    client: AsyncClient, sprite_exec, monkeypatch
):
    """The configured/disabled pair's other half: where alerts are configured,
    a delivery that genuinely fails stays loud. The gate decides whether to ask,
    never whether a broken delivery is swallowed — and it fires after the row is
    recorded, so the failure survives the page that failed to go out."""
    agent_id, stamp, held_at_failure = await _failing_scheduled_run(
        client, sprite_exec, monkeypatch, cause="agent turn failed: opencode error"
    )
    _configure_alerts(monkeypatch)

    async def undeliverable(text: str) -> None:
        raise ConnectionError("slack transport is down")

    monkeypatch.setattr(alert_service, "send_alert", undeliverable)

    with pytest.raises(ConnectionError, match="slack transport is down"):
        await agent_schedules._run_scheduled_agent(agent_id, stamp)

    row = await get_pool().fetchrow(
        "SELECT last_run_error, last_run_outcome FROM agents WHERE id = $1", agent_id
    )
    assert row["last_run_outcome"] == "failed"
    assert "agent turn failed: opencode error" in row["last_run_error"]
    assert held_at_failure == [f"agent-run:{agent_id}"]
    assert f"agent-run:{agent_id}" not in sprite_exec.redis.data


@pytest.mark.asyncio
async def test_configured_destination_without_a_bot_install_names_its_own_cause(
    client: AsyncClient, sprite_exec, monkeypatch, caplog
):
    """The state an operator lands in by following the alerting docs halfway.

    Measured on the founder stack 2026-11-03: `slack_bot_installs` held 0 rows,
    so setting the two `ALERT_SLACK_*` variables alone produces an alarm that
    cannot ring. This runs the real `send_alert` — the install lookup is what
    fails, not a stubbed transport — and pins what the operator's log then says:
    the run's own failure is already recorded (row, outcome, traceback) before the
    alert names its own cause, so the two failures sit side by side and neither
    stands in for the other.
    """
    agent_id, stamp, held_at_failure = await _failing_scheduled_run(
        client, sprite_exec, monkeypatch, cause="sprite exploded"
    )
    _configure_alerts(monkeypatch)
    assert await get_pool().fetchval("SELECT count(*) FROM slack_bot_installs") == 0

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="no Stash Slack bot installation"):
            await agent_schedules._run_scheduled_agent(agent_id, stamp)

    row = await get_pool().fetchrow(
        "SELECT last_run_error, last_run_outcome FROM agents WHERE id = $1", agent_id
    )
    assert row["last_run_outcome"] == "failed"
    assert "sprite exploded" in row["last_run_error"]
    assert held_at_failure == [f"agent-run:{agent_id}"]
    assert f"agent-run:{agent_id}" not in sprite_exec.redis.data

    # The run's own record: the handler's traceback, naming the run's cause.
    assert any(
        record.exc_info is not None and str(record.exc_info[1]) == "sprite exploded"
        for record in caplog.records
        if record.name == "backend.tasks.agent_schedules"
    )
    # The alert's own record, distinct from the run's record and from the error
    # the delivery raises: the alarm states what it meant to report even though it
    # could not deliver it.
    alert_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "backend.services.alert_service"
    ]
    assert len(alert_lines) == 1
    assert alert_lines[0].startswith("ALERT: Scheduled agent run failed:")
    assert "sprite exploded" in alert_lines[0]


@pytest.mark.asyncio
async def test_run_due_records_no_changes_skip(client: AsyncClient, sprite_exec, monkeypatch):
    from backend.services import agent_auth, curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=1, last_run_error=None)
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )

    async def fake_resolve(user_id, prefer_provider=None, model_id=None, credential_id=None):
        return _runnable_cloud_auth()

    gated_by: list[str] = []

    async def no_changes(owner_user_id, user_id, position, wiki, folder_id=None):
        gated_by.append(wiki)
        return False

    monkeypatch.setattr(agent_auth, "resolve", fake_resolve)
    monkeypatch.setattr(curation_service, "has_changes_since", no_changes)

    assert await agent_schedules._run_due() == 0
    # The beat judges this curator by ITS wiki, not a hardcoded one.
    assert gated_by == ["internal"]
    outcome = await get_pool().fetchval(
        "SELECT last_run_outcome FROM agents WHERE id = $1", agent["id"]
    )
    assert outcome == "skipped_no_changes"


@pytest.mark.asyncio
async def test_run_due_records_missing_credential_skip(
    client: AsyncClient, sprite_exec, monkeypatch
):
    from backend.services import agent_auth

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=1, last_run_error=None)
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )

    async def no_credential(user_id, prefer_provider=None, model_id=None, credential_id=None):
        raise agent_auth.NeedsAuth

    monkeypatch.setattr(agent_auth, "resolve", no_credential)

    assert await agent_schedules._run_due() == 0
    outcome = await get_pool().fetchval(
        "SELECT last_run_outcome FROM agents WHERE id = $1", agent["id"]
    )
    assert outcome == "skipped_no_credential"


@pytest.mark.asyncio
async def test_deleted_agent_run_is_a_quiet_no_op(client: AsyncClient, monkeypatch):
    """An agent deleted between the beat tick and its dispatched run has
    nothing to run and no row left to record a failure on — the task must
    return quietly instead of dying as a bare task error."""
    sent = _capture_alerts(monkeypatch)
    await agent_schedules._run_scheduled_agent(uuid.uuid4(), "202608091200")
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "scheduled"])
async def test_catchup_dispatch_failure_preserves_completed_batch_and_charge(
    client, pool, sprite_exec, monkeypatch, trigger
):
    from backend.services import sprite_agent_service

    uid = await _register(client)
    agent = await _make_curator(uid, curated_hours_ago=72, last_run_error=None)

    async def completed(*args):
        return "Completed."

    async def unavailable(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", completed)
    monkeypatch.setattr(agent_schedules, "_queue_curator_catchup", unavailable)
    alerts = _capture_alerts(monkeypatch)
    if trigger == "manual":
        await agent_schedules._run_curator_now(agent["id"])
    else:
        await agent_service.mark_run(agent["id"])
        await agent_schedules._run_scheduled_agent(agent["id"], "dispatch-failure")
    current = await agent_service.get_agent_by_id(agent["id"])
    assert current["last_run_outcome"] == "ran"
    assert current["last_run_error"] is None
    assert current["month_run_count"] == 1
    assert current["curated_through"] > datetime.now(UTC) - timedelta(minutes=1)
    assert len(alerts) == 1 and "catch-up dispatch failed" in alerts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "scheduled"])
async def test_busy_scoped_curator_refunds_only_skipped_run(
    client, pool, monkeypatch, trigger, sprite_exec
):
    from backend.services import scoped_curation_service, sprite_agent_service

    uid = await _register(client)
    agent = await _make_curator(uid, curated_hours_ago=72, last_run_error=None)
    before = (await agent_service.get_agent_by_id(agent["id"]))["curated_through"]
    await agent_service.mark_run(agent["id"])

    async def workspace(_):
        return {"id": uuid.uuid4()}

    async def busy(*args, **kwargs):
        raise sprite_agent_service.TurnInProgress

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", busy)
    alerts = _capture_alerts(monkeypatch)
    if trigger == "manual":
        await agent_schedules._run_curator_now(agent["id"])
    else:
        await agent_service.mark_run(agent["id"])
        await agent_schedules._run_scheduled_agent(agent["id"], "busy")
    current = await agent_service.get_agent_by_id(agent["id"])
    assert current["month_run_count"] == 1
    assert current["last_run_outcome"] == "started"
    assert current["last_run_error"] is None
    assert current["curated_through"] == before
    assert alerts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["entitlement", "workspace", "input", "auth"])
async def test_queued_catchup_rechecks_execution_gates(
    client, pool, monkeypatch, changed, sprite_exec
):
    from backend.services import (
        agent_auth,
        billing_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    uid = await _register(client)
    agent = await _make_curator(uid, curated_hours_ago=72, last_run_error=None)

    async def workspace(_):
        return None if changed == "workspace" else {"id": uuid.uuid4()}

    async def entitled(_):
        return changed != "entitlement"

    async def pending(*args):
        return changed != "input"

    async def auth(_, **kwargs):
        if changed == "auth":
            raise agent_auth.ProviderNotConfigured("revoked")

    async def forbidden(*args):
        pytest.fail("Catch-up must recheck its gates before starting a model run")

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)
    monkeypatch.setattr(scoped_curation_service, "require_run_auth", auth)
    monkeypatch.setattr(billing_service, "is_pro", entitled)
    monkeypatch.setattr(agent_schedules, "_pending_input", pending)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", forbidden)
    if changed == "auth":
        with pytest.raises(agent_auth.ProviderNotConfigured):
            await agent_schedules._run_curator_now(agent["id"], metered=False, catchup=True)
    else:
        await agent_schedules._run_curator_now(agent["id"], metered=False, catchup=True)
    current = await agent_service.get_agent_by_id(agent["id"])
    assert current["last_run_outcome"] is None
    assert current["month_run_count"] == 0


@pytest.mark.asyncio
async def test_weekly_curator_is_not_stale_before_its_next_scheduled_run(client, pool, monkeypatch):
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    previous = datetime.now(UTC) - timedelta(days=3)
    cron = f"0 0 * * {(previous.weekday() + 1) % 7}"
    await pool.execute(
        "UPDATE agents SET last_run_outcome='ran',schedule_cron=$2 WHERE id=$1", agent["id"], cron
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "pending", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)
    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_failed_without_message_is_not_reported_as_stuck(client, pool, monkeypatch):
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await pool.execute("UPDATE agents SET last_run_outcome='failed' WHERE id=$1", agent["id"])
    await memory_service.push_event(
        user_id, "test", "user_message", "pending", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)
    assert await agent_schedules._alert_stale_curators() == 1
    assert "last run failed without an error message" in sent[0]
    assert "never resolved" not in sent[0]


@pytest.mark.asyncio
async def test_catchup_budget_is_atomic_shared_and_expires_after_24_hours(client, pool):
    import asyncio

    user_id = await _register(client)
    agent = await agent_service.get_or_create_curator(user_id)
    claims = await asyncio.gather(*(agent_service.claim_catchup(agent["id"], 7) for _ in range(20)))
    assert sum(claims) == 7
    # Starting other manual/scheduled/first-day runs cannot reset this allowance.
    await agent_service.mark_run(agent["id"])
    await agent_service.mark_run(agent["id"], metered=False)
    assert not await agent_service.claim_catchup(agent["id"], 7)
    await pool.execute(
        "UPDATE agents SET catchup_window_started=now()-interval '25 hours' WHERE id=$1",
        agent["id"],
    )
    assert await agent_service.claim_catchup(agent["id"], 7)
    assert await pool.fetchval("SELECT catchup_runs FROM agents WHERE id=$1", agent["id"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "beat"])
async def test_busy_attempt_does_not_erase_previous_status_or_consume_tick(
    client, pool, monkeypatch, sprite_exec, trigger
):
    from backend.services import scoped_curation_service, sprite_agent_service

    user_id = await _register(client)
    agent = await _make_curator(
        user_id, curated_hours_ago=72, last_run_error="Previous worker error"
    )
    await pool.execute(
        "UPDATE agents SET schedule_cron='* * * * *',last_run_at=now()-interval '5 minutes' WHERE id=$1",
        agent["id"],
    )

    async def workspace(_):
        return {"id": uuid.uuid4()}

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)
    before = await agent_service.get_agent_by_id(agent["id"])
    async with sprite_agent_service._TurnLock(
        f"scoped-curator-{agent['id']}", settings.AGENT_TURN_TIMEOUT_SECONDS
    ):
        if trigger == "manual":
            await agent_schedules._run_curator_now(agent["id"])
        else:
            assert await agent_schedules._run_due() == 0
    after = await agent_service.get_agent_by_id(agent["id"])
    for key in (
        "last_run_at",
        "last_run_error",
        "last_run_outcome",
        "month_run_count",
        "month_run_anchor",
    ):
        assert before[key] == after[key]


@pytest.mark.asyncio
async def test_catchup_uses_pending_input_not_outer_task_start_time(client, pool, monkeypatch):
    from backend.services import billing_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    agent = await agent_service.get_agent_by_id(agent["id"])
    started = datetime.now(UTC)
    await pool.execute(
        "UPDATE agents SET curated_through=$2,last_run_outcome='ran' WHERE id=$1",
        agent["id"],
        started + timedelta(milliseconds=1),
    )

    async def yes(*args):
        return True

    monkeypatch.setattr(billing_service, "is_pro", yes)
    monkeypatch.setattr(agent_schedules, "_pending_input", yes)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    await agent_schedules._queue_curator_catchup(agent, started, 2, workspace={"id": uuid.uuid4()})
    assert len(queued) == 1


@pytest.mark.asyncio
async def test_exhausted_shared_catchup_allowance_never_starts_model(
    client, pool, monkeypatch, sprite_exec
):
    from backend.services import billing_service, scoped_curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error="Earlier error")
    await pool.execute(
        "UPDATE agents SET catchup_window_started=now(),catchup_runs=7 WHERE id=$1", agent["id"]
    )

    async def workspace(_):
        return {"id": uuid.uuid4()}

    async def yes(*args, **kwargs):
        return True

    async def forbidden(*args, **kwargs):
        pytest.fail("Exhausted catch-up budget must stop before execution")

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)
    monkeypatch.setattr(scoped_curation_service, "require_run_auth", yes)
    monkeypatch.setattr(billing_service, "is_pro", yes)
    monkeypatch.setattr(agent_schedules, "_pending_input", yes)
    monkeypatch.setattr(agent_schedules, "_run_turn", forbidden)
    before = await agent_service.get_agent_by_id(agent["id"])
    await agent_schedules._run_curator_now(agent["id"], metered=False, catchup=True)
    after = await agent_service.get_agent_by_id(agent["id"])
    assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["busy", "failure"])
async def test_refunds_do_not_change_an_old_month_counter(client, pool, reason):
    user_id = await _register(client)
    agent = await agent_service.get_or_create_curator(user_id)
    await pool.execute(
        "UPDATE agents SET month_run_anchor=(now()-interval '2 months')::date,month_run_count=3 WHERE id=$1",
        agent["id"],
    )
    if reason == "busy":
        await agent_schedules._skip_busy_curator(agent["id"], metered=True)
    else:
        await agent_service.mark_run_failed(agent["id"], "Failed")
    assert await pool.fetchval("SELECT month_run_count FROM agents WHERE id=$1", agent["id"]) == 3


# ---------------------------------------------------------------------------
# STAS-376: the catch-up cadence ceiling is derived from the curator allowance,
# and an allowance-blocked lane is reported instead of reading as healthy.
# ---------------------------------------------------------------------------


def test_the_catchup_ceiling_is_the_floor_at_default_settings():
    """The bug being closed lives only in deployments that raise their own
    allowance, so the regression that must be impossible is a default install
    drifting by even one run."""
    assert agent_schedules.curator_catchup_pass_ceiling() == agent_schedules.CURATOR_CATCHUP_PASSES


@pytest.mark.asyncio
async def test_catchup_bound_a_lane_claims_tracks_the_allowance_not_the_constant(
    client, pool, monkeypatch
):
    """Raising the month's curator allowance widens the catch-up bound, and the
    bound a pass actually *claims* is the widened one.

    The claim is the assertion that matters: a pass that planned 10 but claimed 7
    would still stop at 7, which is the bug. The constant would allow only 7
    attempts, so a limit of 9 is only reachable through the entitlement.
    """
    from backend.services import billing_service, scoped_curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await pool.execute("UPDATE agents SET run_mode='scheduled' WHERE id=$1", agent["id"])
    agent = await agent_service.get_agent_by_id(agent["id"])

    async def yes(*args, **kwargs):
        return True

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", lambda _a: {"id": None})
    monkeypatch.setattr(scoped_curation_service, "require_run_auth", yes)
    monkeypatch.setattr(billing_service, "is_pro", yes)
    monkeypatch.setattr(agent_schedules, "_pending_input", yes)
    # Stop the pass at the claim so nothing runs: returning False is the real
    # "allowance spent" outcome and takes the function straight to its return.
    claim = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_service, "claim_catchup", claim)

    await agent_schedules._run_curator_locked(
        agent, {"id": uuid.uuid4()}, False, False, None, True, True
    )
    assert claim.await_args.args[1] == agent_schedules.CURATOR_CATCHUP_PASSES - 1

    monkeypatch.setattr(settings, "FREE_CURATOR_RUNS_PER_MONTH", 310)
    assert agent_schedules.curator_catchup_pass_ceiling() == 10
    await agent_schedules._run_curator_locked(
        agent, {"id": uuid.uuid4()}, False, False, None, True, True
    )
    assert claim.await_args.args[1] == 10 - 1


@pytest.mark.asyncio
async def test_a_chain_started_under_a_raised_allowance_queues_the_wider_budget(
    client, pool, monkeypatch
):
    """The queued dispatch carries the entitlement's budget, so the chain is
    planned against the allowance the operator declared rather than the floor."""
    from backend.services import billing_service, scoped_curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    started = datetime.now(UTC)
    # A catch-up chain only continues from a successful, advancing batch, so the
    # lane is seeded that way before the dispatch is attempted. `agent` keeps its
    # pre-update watermark: the advance is what the chain reads as progress.
    await pool.execute(
        "UPDATE agents SET curated_through=$2, run_mode='scheduled', last_run_outcome='ran' "
        "WHERE id=$1",
        agent["id"],
        started,
    )

    async def yes(*args, **kwargs):
        return True

    async def workspace(_agent):
        return {"id": None}

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace)
    monkeypatch.setattr(billing_service, "is_pro", yes)
    monkeypatch.setattr(agent_schedules, "_pending_input", yes)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )

    monkeypatch.setattr(settings, "FREE_CURATOR_RUNS_PER_MONTH", 310)
    await agent_schedules._queue_curator_catchup(
        agent, started, agent_schedules.curator_catchup_pass_ceiling() - 1
    )
    # The chain start hands over ceiling - 1 and each dispatch reserves one for
    # itself, so a raised allowance plans 8 further passes where the constant
    # plans 6. The floor value is asserted in the same test so a drift in either
    # direction fails.
    assert queued == [
        {
            "args": [str(agent["id"])],
            "kwargs": {
                "metered": False,
                "platform_initiated": True,
                "catchup_remaining": 8,
                "catchup": True,
            },
            "countdown": 15,
        }
    ]

    queued.clear()
    monkeypatch.setattr(settings, "FREE_CURATOR_RUNS_PER_MONTH", 10)
    await agent_schedules._queue_curator_catchup(
        agent, started, agent_schedules.curator_catchup_pass_ceiling() - 1
    )
    assert queued == [
        {
            "args": [str(agent["id"])],
            "kwargs": {
                "metered": False,
                "platform_initiated": True,
                "catchup_remaining": 6,
                "catchup": True,
            },
            "countdown": 15,
        }
    ]


@pytest.mark.asyncio
async def test_an_allowance_blocked_curator_is_reported_with_its_backlog(client, pool, monkeypatch):
    """A lane frozen by the monthly allowance used to be indistinguishable from
    a healthy one, and the drain could not survive it.

    `last_run_error` is deliberately populated with an unrelated older failure:
    `mark_run_skipped` writes the outcome and nothing else, so a line built from
    the error column would print a cause that has nothing to do with the stop.
    The report must name the allowance and carry the backlog number, because the
    decision it triggers is about an account, not a lane.
    """
    from datetime import date

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error="opencode error")
    await pool.execute(
        "UPDATE agents SET last_run_outcome='skipped_credits', month_run_count=10, "
        "month_run_anchor=$2 WHERE id=$1",
        agent["id"],
        date.today(),
    )
    for _ in range(2):
        await memory_service.push_event(
            user_id, "test", "user_message", "curate me", user_id, f"sess-{uuid.uuid4()}"
        )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 1
    assert len(sent) == 1
    assert "credits" in sent[0] and "allowance" in sent[0]
    assert "10 of 10 runs used" in sent[0]
    assert "2 distinct events pending" in sent[0]
    assert "opencode error" not in sent[0]


@pytest.mark.asyncio
async def test_a_curator_that_found_nothing_to_do_still_stays_quiet(client, pool, monkeypatch):
    """The widened predicate must not turn every designed skip into a page. A
    lane that resolved as `skipped_no_changes` had a run and found nothing; it is
    not the bug and must stay silent."""
    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=72, last_run_error=None)
    await pool.execute(
        "UPDATE agents SET last_run_outcome='skipped_no_changes' WHERE id=$1", agent["id"]
    )
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []
