"""A self-hosted stack with no model must not let its curators idle in silence.

The beat already records why a tick stopped (`last_run_outcome =
'skipped_no_credential'` on the agent row), but a designed skip is a *resolved*
outcome, so the stale-curator watchdog deliberately stays quiet and the column
carried the whole story in a place nothing rendered. On a self-host box with no
`ANTHROPIC_API_KEY` and no connected model endpoint that meant the Memory
curator never ran, forever, with nothing in the UI, the log, or the pager to say
so.

These tests pin the loud half of that state on the backend: the beat says the
block out loud at WARNING with both remedies named, and the skip still never
pages — a self-hosting installation has no alert destination to page, which is
exactly why its signal is the log line plus the UI state.

The gate is the real one: nothing patches `agent_auth.resolve`. The scope simply
owns no credential row and the backend holds no key, which is precisely the
installed-and-unconfigured self-host profile this card is about. Patching the
resolver would only prove the branch exists, not that it fires.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.config import settings
from backend.database import get_pool
from backend.services import agent_service, memory_service, sprite_agent_service
from backend.tasks import agent_schedules

from .conftest import FakeRedis
from .test_agent_schedule_alerts import _capture_alerts
from .test_curator import _auth, _register


@pytest.fixture(autouse=True)
def _run_lock_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """An in-memory broker for the beat's per-agent run lock.

    The beat takes `sprite_agent_service.agent_run_lock` before it reaches the
    credential gate, so every test here needs *a* Redis; CI provisions none, and
    the suite's only fake is `FakeRedis`. `sprite_exec` is the fixture upstream
    asks for when it wants that fake, but it also hands the scope a Claude
    credential — the exact state this file is about not having — so the lock is
    faked on its own and the resolver stays untouched.
    """
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: FakeRedis())


def _self_host_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stack as installed by someone who changed nothing: no backend key, and
    a scope with zero `user_agent_credentials` rows (the caller registers a fresh
    user), so the real resolver has nothing to hand a run."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")


async def _scheduled_curator_with_work(user_id: uuid.UUID) -> dict:
    """A curator on the beat right now with something to read — the state a
    self-hoster has once they have used the product at all."""
    agent = await agent_service.get_or_create_curator(user_id)
    await _make_due(agent)
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{uuid.uuid4()}"
    )
    return agent


async def _make_due(agent: dict) -> None:
    """Put the agent one cron tick behind, as it is every day for a scheduled
    curator. The beat consumes the tick up front, so a repeat has to rewind it."""
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(minutes=5),
    )


async def _outcome(agent_id: uuid.UUID) -> tuple[str | None, str | None]:
    row = await get_pool().fetchrow(
        "SELECT last_run_outcome, last_run_error FROM agents WHERE id = $1", agent_id
    )
    return row["last_run_outcome"], row["last_run_error"]


@pytest.mark.asyncio
async def test_self_host_curator_is_skipped_on_every_tick(
    client: AsyncClient, pool, monkeypatch: pytest.MonkeyPatch
):
    """The permanent skip, measured through the real gate.

    The point is the repetition: the first tick records the block and the next
    day's tick records it again, because nothing about the row gets any better on
    its own. `last_run_error` stays NULL by design — a designed skip is not a
    failed run — which is why the UI has to read the outcome, not the error.
    """
    _self_host_profile(monkeypatch)
    _, user_id = await _register(client)
    agent = await _scheduled_curator_with_work(user_id)

    assert await agent_schedules._run_due() == 0
    assert await _outcome(agent["id"]) == ("skipped_no_credential", None)

    await _make_due(agent)
    assert await agent_schedules._run_due() == 0
    assert await _outcome(agent["id"]) == ("skipped_no_credential", None)


@pytest.mark.asyncio
async def test_no_credential_skip_logs_a_warning_naming_both_remedies(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """`docker compose logs` has to explain itself.

    A self-hoster's first stop is the container log, so the beat's own line is
    the one signal guaranteed to reach someone who never opens the Settings
    screen. INFO is buried in a beat that ticks every minute; the block has to
    be a WARNING and it has to name what to connect.
    """
    _self_host_profile(monkeypatch)
    _, user_id = await _register(client)
    await _scheduled_curator_with_work(user_id)

    with caplog.at_level(logging.WARNING, logger="backend.tasks.agent_schedules"):
        assert await agent_schedules._run_due() == 0

    said = [
        r.getMessage()
        for r in caplog.records
        if r.name == "backend.tasks.agent_schedules" and r.levelno >= logging.WARNING
    ]
    assert any("no runnable model" in m and "ANTHROPIC_API_KEY" in m for m in said), said


@pytest.mark.asyncio
async def test_no_credential_skip_still_never_pages(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The designed-skip doctrine stays intact — this card adds visibility, not
    a pager. Slack alerting cannot even fire on the installation this symptom is
    about, and paging an operator every day for a state the UI now states would
    train them to ignore the pager."""
    _self_host_profile(monkeypatch)
    _, user_id = await _register(client)
    agent = await _scheduled_curator_with_work(user_id)
    assert await agent_schedules._run_due() == 0

    # Stale watermark plus pending work: the exact shape that pages a `failed`
    # or unresolved `started` curator.
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(hours=72),
    )
    sent = _capture_alerts(monkeypatch)

    assert await agent_schedules._alert_stale_curators() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_the_curators_api_already_hands_the_block_to_the_ui(
    client, monkeypatch: pytest.MonkeyPatch
):
    """The Settings row needs no new endpoint and no router change: `GET
    /me/curators` returns the curator row whole, so the recorded block already
    reaches the client — nothing rendered it. This is the premise the frontend
    work stands on, pinned rather than assumed.
    """
    _self_host_profile(monkeypatch)
    key, user_id = await _register(client)
    agent = await _scheduled_curator_with_work(user_id)
    assert await agent_schedules._run_due() == 0

    rows = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()["curators"]
    row = next(r for r in rows if r["id"] == str(agent["id"]))
    assert row["last_run_outcome"] == "skipped_no_credential"
    assert row["last_run_error"] is None


@pytest.mark.asyncio
async def test_a_successful_run_clears_the_blocked_state(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """What the UI relies on to self-clear: the outcome is one column, and the
    next resolved run overwrites the block. A user who connects a model must
    never have to dismiss anything."""
    _self_host_profile(monkeypatch)
    _, user_id = await _register(client)
    agent = await _scheduled_curator_with_work(user_id)
    assert await agent_schedules._run_due() == 0
    assert await _outcome(agent["id"]) == ("skipped_no_credential", None)

    await agent_service.mark_run_succeeded(agent["id"])

    assert await _outcome(agent["id"]) == ("ran", None)
