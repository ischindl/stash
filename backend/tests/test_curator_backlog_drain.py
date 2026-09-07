"""The curator backlog drain: continuous automated dispatch, bounded and braked.

A curator's nightly cron gives a lane one run per day. That is right for a lane
which fits its material in one run and hopeless for one which cannot: the
founder's lanes held ~140k distinct events behind a watermark, so one run per lane
per day is weeks of calendar (STAS-199). `drain_curator_backlog` re-dispatches the
most-behind lanes on the platform's own clock instead.

Continuous dispatch is only safe if it is bounded, so the tests here pin the two
brakes (a lane already running is stepped past, never re-dispatched; a lane whose
last run failed belongs to the nightly tick), the gates it mirrors from the nightly
tick (credential, monthly allowance, pending material in the lane's own scope),
that it wakes curators and nothing else, the most-behind-first ordering, and the
per-tick cap.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.config import settings
from backend.database import get_pool
from backend.services import (
    agent_auth,
    agent_service,
    billing_service,
    curation_service,
    memory_service,
)
from backend.tasks import agent_schedules
from backend.tasks.agent_schedules import AGENT_RUN_LOCK_TTL, CURATOR_DRAIN_LANES_PER_TICK

from .conftest import unique_name
from .test_curator import _auth, _register


async def _healthy_probe(base_url: str, api_key: str | None) -> dict:
    """A local box that answers, for tests whose subject is not the dial."""
    return {"ok": True, "http_status": 200, "models": ["stub-model"]}


async def _lane(
    client: AsyncClient,
    *,
    watermark_hours_ago: float,
    outcome: str | None = None,
    last_run_minutes_ago: float = 24 * 60,
    metered_runs: int = 0,
) -> dict:
    """A curator lane with one event waiting behind its watermark."""
    name = unique_name("drain")
    response = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@example.com"},
    )
    assert response.status_code == 201
    user_id = UUID(response.json()["id"])
    agent = await agent_service.get_or_create_curator(user_id)
    # The event lands first on purpose: a genuinely-new event older than the
    # watermark legitimately rewinds it (that is the ingest invariant), so writing
    # the watermark last is the only way this fixture means what it says in both
    # directions — a watermark ahead of now really is a caught-up lane.
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{agent['id']}"
    )
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2, last_run_outcome = $3, last_run_at = $4, "
        "month_run_count = $5, month_run_anchor = $6 WHERE id = $1",
        agent["id"],
        datetime.now(UTC) - timedelta(hours=watermark_hours_ago),
        outcome,
        datetime.now(UTC) - timedelta(minutes=last_run_minutes_ago),
        metered_runs,
        date.today(),
    )
    return agent


async def _metering(agent_id) -> tuple:
    """What a drain tick would have to have written to have metered this lane."""
    row = await get_pool().fetchrow(
        "SELECT month_run_count, last_run_outcome, curated_through FROM agents WHERE id = $1",
        agent_id,
    )
    return (row["month_run_count"], row["last_run_outcome"], row["curated_through"])


async def _make_due(agent_id) -> None:
    """Put a lane's own cron tick in the past, which is what `_run_due` checks."""
    await get_pool().execute(
        "UPDATE agents SET schedule_cron = '* * * * *', last_run_at = now() - interval '5 minutes' "
        "WHERE id = $1",
        agent_id,
    )


def _capture_dispatches(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    """Capture what the two beat dispatchers hand to Celery (tests have no broker)."""
    captured: dict[str, list[dict]] = {"curator": [], "scheduled": []}

    def _fake_task(bucket: str):
        class _FakeTask:
            def delay(self, agent_id, *positional, **kwargs):
                captured[bucket].append({"agent_id": agent_id, **kwargs})

        return _FakeTask()

    monkeypatch.setattr(agent_schedules, "run_curator_now", _fake_task("curator"))
    monkeypatch.setattr(agent_schedules, "run_scheduled_agent", _fake_task("scheduled"))
    return captured


def _dispatched(captured: dict) -> list[str]:
    return [entry["agent_id"] for entry in captured["curator"]]


@pytest.mark.asyncio
async def test_drain_dispatches_the_most_behind_lanes_first_up_to_the_cap(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # Four lanes are waiting and only two heavy slots exist, so the tick must
    # spend them on the furthest-behind work, not on whoever was created first.
    lanes = [await _lane(client, watermark_hours_ago=hours) for hours in (100, 50, 10, 1)]
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == CURATOR_DRAIN_LANES_PER_TICK
    assert _dispatched(captured) == [str(lanes[0]["id"]), str(lanes[1]["id"])]
    # The run a drain starts spends the monthly allowance like any other run. A
    # `metered=False` dispatch here would hand every free scope unlimited curation.
    assert all("metered" not in entry for entry in captured["curator"])


@pytest.mark.asyncio
async def test_drain_steps_past_a_busy_lane_instead_of_spending_the_cap(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # The busy lane is the most-behind one, so a cap consumed by skips would
    # leave this tick with no dispatch at all and the waiting lane un-served.
    busy = await _lane(client, watermark_hours_ago=100, outcome="started", last_run_minutes_ago=1)
    waiting = await _lane(client, watermark_hours_ago=50)
    busy_before = await _metering(busy["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 1
    assert _dispatched(captured) == [str(waiting["id"])]
    # The run in flight keeps its own row: the drain consumed no tick, so it
    # writes nothing — not a metered count, and not an outcome that would
    # overwrite the `started` the stale-run alert pages on.
    assert await _metering(busy["id"]) == busy_before


@pytest.mark.asyncio
async def test_drain_unsticks_a_lane_whose_run_died_without_releasing_its_lock(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # An unresolved 'started' older than the single-flight lock's TTL cannot be a
    # run still going: its worker died mid-turn. Treating that as busy would keep
    # the drain stepping past a lane that has been dead for hours, forever.
    stale = await _lane(
        client,
        watermark_hours_ago=100,
        outcome="started",
        last_run_minutes_ago=AGENT_RUN_LOCK_TTL / 60 + 10,
    )
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 1
    assert _dispatched(captured) == [str(stale["id"])]


@pytest.mark.asyncio
async def test_drain_leaves_a_failing_lane_to_the_nightly_tick(
    client: AsyncClient, sprite_exec, monkeypatch
):
    failing = await _lane(client, watermark_hours_ago=100, outcome="failed")
    before = await _metering(failing["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 0
    assert captured["curator"] == []
    # Handed over, not overwritten: erasing the failed outcome would also silence
    # the alert that pages when a lane keeps failing.
    assert await _metering(failing["id"]) == before
    # The lane is not stranded. A failed run is refunded its allowance credit, so
    # a drain that re-dispatched it would retry an always-failing lane for free
    # every tick forever; the nightly tick still retries it daily, and it ignores
    # the outcome — which is exactly the retry this assertion covers.
    await _make_due(failing["id"])
    assert await agent_schedules._run_due() == 1
    assert [entry["agent_id"] for entry in captured["scheduled"]] == [str(failing["id"])]


@pytest.mark.asyncio
async def test_drain_stops_at_the_monthly_allowance_for_free_scopes_only(
    client: AsyncClient, sprite_exec, monkeypatch
):
    monkeypatch.setattr(settings, "FREE_CURATOR_RUNS_PER_MONTH", 1)
    spent = await _lane(client, watermark_hours_ago=100, metered_runs=1)
    before = await _metering(spent["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 0
    assert captured["curator"] == []
    assert await _metering(spent["id"]) == before, "a skip must not spend the allowance it lacks"

    # Pro is unlimited, which is why the founder's backlog drains continuously
    # while a free scope stops at the same counter.
    async def fake_is_pro(user_id: UUID) -> bool:
        return True

    monkeypatch.setattr(billing_service, "is_pro", fake_is_pro)
    assert await agent_schedules._drain_curator_backlog() == 1
    assert _dispatched(captured) == [str(spent["id"])]


@pytest.mark.asyncio
async def test_drain_never_wakes_a_lane_with_nothing_pending(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # A watermark an hour ahead of now: nothing has arrived since it, so the lane
    # is caught up and the whole tick must cost one EXISTS and no run.
    caught_up = await _lane(client, watermark_hours_ago=-1)
    before = await _metering(caught_up["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 0
    assert captured["curator"] == []
    # The idle-day cost the nightly path promises (one EXISTS, no metering) holds
    # for the drain too — it fires 288 times more often.
    assert await _metering(caught_up["id"]) == before


@pytest.mark.asyncio
async def test_nightly_tick_and_drain_together_meter_one_run_per_lane(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # The two dispatchers coexist: the nightly cron keeps firing, the drain keeps
    # the lane busy between ticks. One lane must still cost one metered run.
    lane = await _lane(client, watermark_hours_ago=100)
    await _make_due(lane["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._run_due() == 1
    assert await agent_schedules._drain_curator_backlog() == 0

    row = await get_pool().fetchrow(
        "SELECT month_run_count, last_run_outcome FROM agents WHERE id = $1", lane["id"]
    )
    assert row["month_run_count"] == 1, "the nightly tick and the drain must not both meter"
    assert [entry["agent_id"] for entry in captured["scheduled"]] == [str(lane["id"])]
    assert captured["curator"] == []


@pytest.mark.asyncio
async def test_drain_never_wakes_a_lane_whose_owner_has_no_credential(
    client: AsyncClient, monkeypatch
):
    # Deliberately no `sprite_exec`: this user connected no key, which is the
    # state most of the fleet is in. A drain that dispatched anyway would spend
    # allowance on runs that can only fail and wake a sprite for nobody.
    _key, user_id = await _register(client)
    lane = await agent_service.get_or_create_curator(user_id)
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{lane['id']}"
    )
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1",
        lane["id"],
        datetime.now(UTC) - timedelta(hours=100),
    )
    before = await _metering(lane["id"])
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 0
    assert captured["curator"] == []
    assert await _metering(lane["id"]) == before


@pytest.mark.asyncio
async def test_drain_never_wakes_a_scheduled_agent_that_is_not_a_curator(
    client: AsyncClient, sprite_exec, monkeypatch
):
    # The drain is a curator mechanism: it reads a curator's watermark and spends
    # a curator's allowance. A user's ordinary scheduled agent belongs to its own
    # cron alone, however due that cron looks and however eligible its owner is.
    key, user_id = await _register(client)
    lane = await agent_service.get_or_create_curator(user_id)
    await memory_service.push_event(
        user_id, "test", "user_message", "hello", user_id, f"sess-{lane['id']}"
    )
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1",
        lane["id"],
        datetime.now(UTC) - timedelta(hours=100),
    )
    worker = (
        await client.post(
            "/api/v1/me/agents",
            json={
                "name": "Nightly report",
                "run_mode": "scheduled",
                "schedule_cron": "* * * * *",
                "schedule_prompt": "Summarize the day.",
            },
            headers=_auth(key),
        )
    ).json()
    assert worker["run_mode"] == "scheduled"
    captured = _capture_dispatches(monkeypatch)

    assert await agent_schedules._drain_curator_backlog() == 1
    assert _dispatched(captured) == [str(lane["id"])]
    assert captured["scheduled"] == [], "the drain dispatches curators and nothing else"


@pytest.mark.asyncio
async def test_drain_gate_for_a_folder_lane_is_that_folder_and_nothing_else(
    client: AsyncClient, pool, monkeypatch
):
    """The scoping trap this dispatcher could have reinstated.

    A folder curator's promise is that it reads one project. `has_changes_since`
    takes an optional folder, so a drain that asked only whether the *user* has
    new material would wake a project's curator for events filed in no project at
    all — a metered run that reads nothing, writes nothing, and advances nothing,
    on a lane whose watermark only its own project's filing can move. The folder
    curator's credential resolves here, so the folder id is the only thing
    standing between this dispatch and that no-op."""
    from cryptography.fernet import Fernet

    from .test_folder_curators import BASE, _file_session, _folder

    # Credential storage is Fernet-encrypted and CI sets no key for it.
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Connecting probes the box before storing it, and this test names a host that
    # does not exist: the test's subject is the drain gate, not the dial.
    monkeypatch.setattr(agent_auth, "probe_local_endpoint", _healthy_probe)
    key, user_id = await _register(client)
    workspace = await agent_service.get_or_create_curator(user_id)
    connected = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": "http://my-host:11434/v1", "model": "qwen"},
        headers=_auth(key),
    )
    assert connected.status_code == 200, connected.text
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, user_id, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    folder_curator = await agent_service.create_folder_curator(
        user_id, UUID(rozvrh), "local", "qwen"
    )

    # Material that will never belong to this project, dated after the folder's
    # own first event so an unscoped reading of the same watermark sees it.
    await memory_service.push_event(
        user_id,
        "test",
        "user_message",
        "an unfiled transcript in some other session",
        user_id,
        "conv-unfiled",
        created_at=BASE + timedelta(hours=1),
    )
    watermark = await get_pool().fetchval(
        "SELECT curated_through FROM agents WHERE id = $1", folder_curator["id"]
    )
    assert watermark == BASE  # seeding read the folder's own first event
    # The trap, stated as data: the same lane's gate answers differently by scope.
    assert await curation_service.has_changes_since(user_id, user_id, watermark, "internal")
    assert not await curation_service.has_changes_since(
        user_id, user_id, watermark, "internal", UUID(rozvrh)
    )
    before = await _metering(folder_curator["id"])
    captured = _capture_dispatches(monkeypatch)

    # One event, two lanes: it belongs to the workspace lane's scope and to no
    # project, so it wakes exactly one of them.
    assert await agent_schedules._drain_curator_backlog() == 1, (
        f"workspace lane {workspace['id']}, folder lane {folder_curator['id']}"
    )
    assert _dispatched(captured) == [str(workspace["id"])]
    assert await _metering(folder_curator["id"]) == before
