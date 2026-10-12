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

from backend.database import get_pool
from backend.integrations.slack import client as slack_client
from backend.services import agent_service, alert_service, memory_service
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
        return None

    async def failed_turn(agent, stamp, read_position):
        held_at_failure.extend(
            key for key in sprite_exec.redis.data if key.startswith("agent-run:")
        )
        raise RuntimeError(cause)

    monkeypatch.setattr(agent_auth, "resolve", resolved_credential)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", failed_turn)
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        agent_schedules.run_scheduled_agent, "delay", lambda *args: dispatched.append(args)
    )
    assert await agent_schedules._run_due() == 1
    return uuid.UUID(dispatched[0][0]), dispatched[0][1], held_at_failure


@pytest.mark.asyncio
async def test_queued_curator_does_not_run_after_pause(client, pool, monkeypatch):
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
async def test_run_due_failure_sends_alert(client: AsyncClient, sprite_exec, monkeypatch):
    # The run lock needs Redis; sprite_exec is what supplies the fake.
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
        return None

    async def fake_run_scheduled(agent, stamp, read_position):
        raise RuntimeError("agent turn failed: opencode error")

    monkeypatch.setattr(agent_auth, "resolve", fake_resolve)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_run_scheduled)
    sent = _capture_alerts(monkeypatch)

    dispatched = []
    monkeypatch.setattr(
        agent_schedules.run_scheduled_agent, "delay", lambda *args: dispatched.append(args)
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
async def test_run_due_records_no_changes_skip(client: AsyncClient, monkeypatch):
    from backend.services import agent_auth, curation_service

    user_id = await _register(client)
    agent = await _make_curator(user_id, curated_hours_ago=1, last_run_error=None)
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )

    async def fake_resolve(user_id, prefer_provider=None, model_id=None, credential_id=None):
        return None

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
async def test_run_due_records_missing_credential_skip(client: AsyncClient, monkeypatch):
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
