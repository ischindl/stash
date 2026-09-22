"""The watermark is one position — an instant plus the event id at it.

A `timestamptz` has microsecond resolution, but distinct events do not collide
only at microsecond granularity: they collide at *event* granularity, which is
what the feed caps on. When more distinct events share one instant than a run
holds, no offset inside the instant can name where the run stopped — the boundary
is a row, so the column has to name a row. These tests pin what that buys: a
watermark that can rest *inside* an instant, an advance that stays monotonic
across the halves of an instant, and a plateau that drains instead of latching.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.database import get_pool
from backend.services import agent_service, curation_service, memory_service
from backend.services.curation_service import Position

from .conftest import unique_name

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient) -> tuple[str, UUID]:
    r = await client.post(
        "/api/v1/users/register",
        json={"name": unique_name("wpos"), "password": "securepassword1"},
    )
    return r.json()["api_key"], UUID(r.json()["id"])


def _auth(k: str) -> dict:
    return {"Authorization": f"Bearer {k}"}


def _slot(n: int) -> UUID:
    """An event id that sorts n-th inside an instant, so which half of a tie a
    position sits in is readable in the assertion that fails."""
    return UUID(hex=f"{n:032x}")


async def _watermark(agent_id) -> Position:
    row = await get_pool().fetchrow(
        "SELECT curated_through, curated_through_event_id FROM agents WHERE id = $1",
        agent_id,
    )
    return curation_service.position_of(row)


async def _push(client: AsyncClient, key: str, events: list[dict]) -> None:
    r = await client.post(
        "/api/v1/me/sessions/events/batch", json={"events": events}, headers=_auth(key)
    )
    assert r.status_code == 201


# ── the writer: an advance is monotonic across an instant's halves ─────────────
@pytest.mark.asyncio
async def test_a_whole_instant_watermark_is_never_rewound_by_a_mid_tie_advance(
    client: AsyncClient, _db_pool
):
    """The case `greatest` could not express: the column consumed the whole of T,
    and a run that read before that advance proposes to stop *inside* T. The
    proposal is behind the stored position — so the write clamps. Compared on the
    instant alone the two look equal, and whichever way an equal comparison fell
    it was wrong half the time."""
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    at = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    await get_pool().execute(
        "UPDATE agents SET curated_through=$2, curated_through_event_id=NULL WHERE id=$1",
        curator["id"],
        at,
    )
    inside = _slot(7)
    stored = await agent_service.mark_curated(curator["id"], Position(at), Position(at, inside))
    assert stored == Position(at), "a mid-tie proposal must not move a whole instant"
    assert await _watermark(curator["id"]) == Position(at)


@pytest.mark.asyncio
async def test_a_mid_tie_watermark_advances_to_the_next_event_at_its_instant(
    client: AsyncClient, _db_pool
):
    """The other half of the same comparison: read mid-tie at T, propose a later
    event at T — that is ahead, so it lands, and both halves move together."""
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    at = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    read = Position(at, _slot(3))
    await get_pool().execute(
        "UPDATE agents SET curated_through=$2, curated_through_event_id=$3 WHERE id=$1",
        curator["id"],
        read.at,
        read.event_id,
    )
    through = Position(at, _slot(9))
    assert await agent_service.mark_curated(curator["id"], read, through) == through
    assert await _watermark(curator["id"]) == through


@pytest.mark.asyncio
async def test_an_advance_reads_the_position_it_started_from(client: AsyncClient, _db_pool):
    """The compare-and-set anchor is the whole position, not its instant: a rewind
    that left the instant alone but cleared the event half moved the position, and
    a run that reads the instant alone would not notice."""
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    at = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
    await get_pool().execute(
        "UPDATE agents SET curated_through=$2, curated_through_event_id=$3 WHERE id=$1",
        curator["id"],
        at,
        _slot(3),
    )
    with pytest.raises(agent_service.CuratorWatermarkConflict):
        await agent_service.mark_curated(curator["id"], Position(at), Position(at, _slot(9)))
    assert await _watermark(curator["id"]) == Position(at, _slot(3))


# ── the plateau: a tie bigger than the cap drains instead of latching ─────────
@pytest.mark.asyncio
async def test_a_tie_larger_than_the_cap_drains_to_zero(client: AsyncClient, _db_pool, monkeypatch):
    """Five distinct events at one instant, a run that holds three. The old writer
    subtracted a microsecond from the tied instant, which admitted all five again
    forever: the run completed, the backlog stayed at its full height, and the next
    run repeated it. Here the watermark stops *inside* the tie, so each run shows
    new events and the backlog reaches zero — and the gate agrees with the feed at
    every position, because both read one clause."""
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 3)
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    await get_pool().execute(
        "UPDATE agents SET curated_through=NULL, curated_through_event_id=NULL WHERE id=$1",
        curator["id"],
    )
    at = datetime.now(UTC) - timedelta(minutes=5)
    until = at + timedelta(hours=1)
    await _push(
        client,
        key,
        [
            {
                "agent_name": "plateau-chat",
                "event_type": "user_message",
                "content": f"turn {i}",
                "session_id": f"plateau-{i}",
                "created_at": at.isoformat(),
            }
            for i in range(5)
        ],
    )

    async def backlog() -> int:
        return (
            await curation_service.curator_event_backlog(
                uid, "internal", await _watermark(curator["id"])
            )
        )["distinct_events"]

    assert await backlog() == 5
    seen: list[str] = []
    runs = 0
    while await backlog() > 0:
        runs += 1
        assert runs <= 3, f"the plateau is not draining: {runs} runs, {seen}"
        # A run reads the position the lane actually holds, not one it remembers.
        position = await _watermark(curator["id"])
        assert await curation_service.has_changes_since(uid, uid, position, "internal") is True
        feed = await curation_service.changes_since(uid, uid, position, "internal")
        shown = [h["content"] for h in feed["history"]]
        assert shown, "a run with a non-empty backlog must never show nothing"
        assert not set(shown) & set(seen), f"the plateau re-served {shown}"
        seen.extend(shown)
        through = await curation_service.complete_through(uid, position, until, "internal")
        await agent_service.mark_curated(curator["id"], position, through)
        assert await backlog() == 5 - len(seen)

    assert len(seen) == 5, "every event of the plateau was served exactly once"
    assert runs == 2, "three events fit the first run, the other two the second"
    assert await _watermark(curator["id"]) == Position(until)
    assert (
        await curation_service.has_changes_since(
            uid, uid, await _watermark(curator["id"]), "internal"
        )
        is False
    )


@pytest.mark.asyncio
async def test_an_ingest_rewind_clears_the_event_half(client: AsyncClient, _db_pool):
    """A rewind re-opens history, so it lands on a whole instant: keeping the
    event half would leave the cursor inside an instant the rewind claims to have
    re-opened, and the rows the rewind exists to re-read would stay invisible."""
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    ahead = datetime.now(UTC) + timedelta(days=1)
    await get_pool().execute(
        "UPDATE agents SET curated_through=$2, curated_through_event_id=$3 WHERE id=$1",
        curator["id"],
        ahead,
        _slot(11),
    )
    await memory_service.push_event(
        uid,
        "rewind-chat",
        "user_message",
        "history imported from before",
        uid,
        session_id="rewind-import",
    )
    stored = await _watermark(curator["id"])
    assert stored.at < ahead, "an event older than the watermark re-opens the position"
    assert stored.event_id is None, "a rewound position is whole, never mid-tie"
    assert await curation_service.has_changes_since(uid, uid, stored, "internal") is True
