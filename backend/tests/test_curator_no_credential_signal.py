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
from uuid import UUID

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


# --- The allowance, stated by the API rather than by each surface (STAS-412) ---


@pytest.mark.asyncio
async def test_the_curators_endpoint_states_the_cap_and_the_rows_carry_their_own(
    client: AsyncClient, _db_pool
):
    """A surface can only state a limit it was given, and a per-row number can
    only come from a row.

    `free_runs_per_month` is account-level entitlement and so travels on the
    envelope, never invented from a numeral in the client; `month_run_count`
    travels on each curator row and counts that row's worked runs. The two
    coexist without one being averaged into the other: the account's allowance is
    what one curator consumes at a time, so the page has to be able to show a
    different numerator per row against one denominator.
    """
    key, uid = await _register(client)
    internal = await agent_service.get_or_create_curator(uid)
    await agent_service.get_or_create_curator(uid, wiki="team")
    await get_pool().execute(
        "UPDATE agents SET month_run_count = 4, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        internal["id"],
    )

    body = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()
    assert body["free_runs_per_month"] == settings.FREE_CURATOR_RUNS_PER_MONTH

    rows = {row["curator_wiki"]: row for row in body["curators"]}
    assert rows["internal"]["month_run_count"] == 4
    assert rows["team"]["month_run_count"] == 0, "a row's count is its own, not the account's total"


@pytest.mark.asyncio
async def test_a_count_left_from_a_past_month_is_not_reported_as_spent_now(
    client: AsyncClient, _db_pool
):
    """The surface prints the row's number as runs already spent this month, so the
    number it is handed has to be this month's.

    `month_run_count` belongs to the month its own anchor names, and a lane that
    stopped being charged keeps whatever it last recorded forever — paused by its
    owner, or moved onto the customer's own endpoint, which this card takes out of
    metering for good. Echoing that stale number would print last month's work as
    this month's consumption, the same misreading the card exists to remove, so the
    payload carries `month_runs_used`: the reading the allowance gate itself
    consults. The row is not rewritten to make the readout true — the count it
    recorded stays where it was recorded.
    """
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    await get_pool().execute(
        "UPDATE agents SET month_run_count = 7, month_run_anchor = "
        "(date_trunc('month', now()) - interval '1 month')::date WHERE id = $1",
        curator["id"],
    )

    body = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()
    row = next(r for r in body["curators"] if r["id"] == curator["id"])
    assert row["month_run_count"] == 0, "a period already spent is not this month's consumption"
    stored = await get_pool().fetchval(
        "SELECT month_run_count FROM agents WHERE id = $1", curator["id"]
    )
    assert stored == 7, "the honest readout reads the period, it does not rewrite history"


@pytest.mark.asyncio
async def test_an_account_with_no_cap_gets_a_null_and_not_a_numeral(client: AsyncClient, _db_pool):
    """The null rule, pinned: absent means no cap, and a client must never render
    a denominator it invented for an unlimited account.

    Enterprise is an admin-granted entitlement and `is_pro` covers it along with
    paid Pro and internal emails; the response carries the difference as absence
    so that no account is shown a ceiling it does not have.
    """
    key, uid = await _register(client)
    await agent_service.get_or_create_curator(uid)
    await get_pool().execute("UPDATE users SET plan = 'enterprise' WHERE id = $1", uid)

    body = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()
    assert body["free_runs_per_month"] is None
    # The per-row counter still travels: an uncapped account still has a number,
    # it just has no ceiling to compare it against.
    assert all("month_run_count" in row for row in body["curators"])


@pytest.mark.asyncio
async def test_the_developer_curator_route_states_the_same_cap(client: AsyncClient, _db_pool):
    """The second surface reads the same key off its own envelope.

    The developer page's curator is a workspace-scope row, so the entitlement is
    read for the scope's owner — the same account the beat gates on — and it
    arrives beside the curator row rather than inside it, because it describes the
    account and not the agent.
    """
    from .test_developer_platform import _developer

    api_key, _, workspace = await _developer(client)
    scope = {**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]}

    # A workspace's scope user carries the enterprise entitlement, so this surface
    # reads as uncapped — and it reads that way for the same reason the beat gates
    # it that way: the allowance is looked up for the account that owns the curator
    # row, which here is the scope user, not the developer who signed in.
    scope_uid = UUID(workspace["scope_user_id"])
    body = (await client.get("/api/v1/me/developer/curator", headers=scope)).json()
    assert body["free_runs_per_month"] is None
    assert body["curator"]["month_run_count"] == 0

    # The same route on an account the allowance does constrain states the numeral.
    await get_pool().execute("UPDATE users SET plan = 'free' WHERE id = $1", scope_uid)
    capped = (await client.get("/api/v1/me/developer/curator", headers=scope)).json()
    assert capped["free_runs_per_month"] == settings.FREE_CURATOR_RUNS_PER_MONTH
