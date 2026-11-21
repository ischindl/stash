"""A personal curator pass is handed one priced part, and commits exactly what it read.

The failure these tests hold shut was measured on the founder stack on 2026-11-21: the
nightly personal tick was handed its ENTIRE unread delta (4,396,654 events for one lane),
spent its 570s wall reading, raised `RunCeilingExceeded` and committed nothing — while its
own wiki writer had already edited pages from that partial read, so the wiki moved and the
watermark did not, and the next night read the same characters again.

Every test here is about the agreement between the read and the commit. A change that only
shortens the feed, or only moves the commit, fails one of them.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import agent_service, curation_service
from backend.services.curation_service import Position

from .conftest import unique_name

pytestmark = pytest.mark.asyncio


async def _owner(client: AsyncClient) -> UUID:
    r = await client.post(
        "/api/v1/users/register",
        json={"name": unique_name("slice"), "password": "securepassword1"},
    )
    return UUID(r.json()["id"])


async def _feed(pool, owner: UUID, sizes: list[int]) -> list[datetime]:
    """One event per size, a minute apart, all ahead of any watermark a test holds."""
    start = datetime.now(UTC) - timedelta(days=2)
    stamps = []
    for index, size in enumerate(sizes):
        at = start + timedelta(minutes=index)
        stamps.append(at)
        await pool.execute(
            "INSERT INTO history_events (owner_user_id, created_by, agent_name, event_type,"
            " content, session_id, created_at) VALUES ($1, $1, 'cli', 'user_message', $2, $3, $4)",
            owner,
            "x" * size,
            f"sess-slice-{index}",
            at,
        )
    return stamps


@pytest.mark.asyncio
async def test_the_window_ends_at_the_last_instant_the_wall_buys(client, _db_pool):
    """The edge is an event's own instant, and the characters are that prefix's own size.

    The budget buys two of the three events, so the window closes on the second one's
    instant: `until_bound` reads `<= until`, which is what lets the whole timestamp group
    at the seam belong to this pass instead of being shown and not committed.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    window = await curation_service.read_window(owner, Position(before), "internal", None, 250, now)

    assert window.until == stamps[1]
    assert window.characters == 200


@pytest.mark.asyncio
async def test_the_commit_lands_on_the_edge_the_turn_was_shown(client, _db_pool):
    """The boundary a run earns IS the edge it was shown — never one event further.

    This is the whole defect in one assertion: the feed and the commit used to be cut
    apart, and a commit ahead of the read claims characters no model ever saw.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)
    window = await curation_service.read_window(owner, Position(before), "internal", None, 250, now)

    through = await curation_service.complete_through(
        owner, Position(before), window.until, "internal", None, max_event_chars=250
    )

    assert through == Position(at=stamps[1])


@pytest.mark.asyncio
async def test_a_feed_the_budget_covers_is_still_read_whole(client, _db_pool):
    """Nothing was here before a backlog outgrew the wall, and that shape survives.

    A lane whose unread delta fits closes at the tick's own `now` — the unchanged run that
    reads everything and commits through the tick. Without this the fix could quietly
    window every run, including the ones that were never broken.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    window = await curation_service.read_window(
        owner, Position(before), "internal", None, 10_000, now
    )

    assert window.until == now
    assert window.characters == 200


@pytest.mark.asyncio
async def test_the_write_phase_is_paid_before_the_read_is_priced(client, _db_pool, monkeypatch):
    """One wall, two model turns: the digest is priced on what the writer leaves behind.

    570s is the founder lane's measured wall, 0.10s a character a priced route, 270s the
    write phase's measured share (the worst of four `saneca` runs) and the commit's own
    reserve comes off too. The drain keeps the whole wall because its parts checkpoint one
    by one — a personal run gets no such chance.
    """
    from backend.services import curator_calibration

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    row = await agent_service.get_agent_by_id(UUID(curator["id"]))

    async def priced(_key):
        return 0.10, "test"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    reserve = agent_service.RUN_COMMIT_RESERVE_SECONDS

    grant = await curation_service.personal_budget_of(row)
    drain_grant = await curation_service.drain_budget_of(row)

    assert grant == min(max(int((570 - 270 - reserve) / 0.10), 384), 8192)
    assert drain_grant == min(max(int((570 - reserve) / 0.10), 1024), 8192)


@pytest.mark.asyncio
async def test_the_nightly_tick_hands_the_turn_its_window_and_books_it(
    client, _db_pool, sprite_exec, monkeypatch, caplog
):
    """The beat's own tick is the run that cures a founder lane, so it is the one pinned.

    Four events of 1000 characters, a wall that pays for two of them: the turn must be
    told where to stop, the watermark must stop exactly there, the lane must report `ran`
    rather than the nightly `failed` it has reported since 2026-09-01, and the meter must
    say once how much was read.
    """
    import logging

    from backend.services import curator_calibration, sprite_agent_service
    from backend.tasks.agent_schedules import _run_due, _run_scheduled_agent, run_scheduled_agent

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    curator_id = UUID(curator["id"])
    stamps = await _feed(_db_pool, owner, [1000] * 4)
    await _db_pool.execute(
        "UPDATE agents SET schedule_cron = '* * * * *',"
        " last_run_at = now() - interval '5 minutes', curated_through = $2 WHERE id = $1",
        curator_id,
        stamps[0] - timedelta(minutes=1),
    )

    async def priced(_key):
        return 0.10, "test"

    seen: list[dict] = []

    async def fake_turn(*_args, **kwargs):
        seen.append(kwargs)
        return "curated"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_turn)
    monkeypatch.setattr(run_scheduled_agent, "delay", lambda *_a, **_k: dispatched.append(_a))
    dispatched: list[tuple] = []

    with caplog.at_level(logging.INFO, logger="backend.tasks.agent_schedules"):
        assert await _run_due() == 1
        await _run_scheduled_agent(dispatched[0][0], dispatched[0][1])

    row = await _db_pool.fetchrow(
        "SELECT curated_through, last_run_outcome FROM agents WHERE id = $1", curator_id
    )
    assert seen[0]["until"] == stamps[1].isoformat()
    assert row["curated_through"] == stamps[1]
    assert row["last_run_outcome"] == "ran"
    meters = [r.message for r in caplog.records if "characters=" in r.message]
    assert len(meters) == 1
    assert "characters=2000" in meters[0] and "budget=2700" in meters[0]


@pytest.mark.asyncio
async def test_a_wall_that_cannot_buy_the_smallest_part_runs_as_it_always_did(
    client, _db_pool, monkeypatch, caplog
):
    """Pricing does not invent a part a wall cannot pay, and does not crash on one either.

    The tiny-wall lane's problem is its ceiling, and its honest report is the cut the wall
    already raises — not a `ValueError` from the pricing helper, and not a slice the run
    cannot finish. The refusal is said out loud, because a lane that can never catch up is
    the thing an operator has to be able to find.
    """
    import logging

    from backend.services import curator_calibration

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    row = await agent_service.get_agent_by_id(UUID(curator["id"]))

    async def priced(_key):
        return 0.32646, "shipped-constant"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 20)

    with caplog.at_level(logging.INFO, logger="backend.services.curation_service"):
        assert await curation_service.personal_budget_of(row) is None
        assert await curation_service.personal_window(row, datetime.now(UTC)) is None

    refusals = [r.message for r in caplog.records if "no priced part" in r.message]
    assert len(refusals) == 2
    assert "smallest pass needs" in refusals[0]
