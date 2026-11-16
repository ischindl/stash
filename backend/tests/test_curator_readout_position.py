"""What a curator reads is one position, and a readout must name THAT one.

Two positions live on an `agents` row and exactly one of them is live at a time:
`curated_through` (+ its event half) is the monotone nightly watermark, while
`drain_through` + `drain_cursor` is where a history drain resumes, moved at every
committed pass. The run path already asks `read_position_of` before it reads
anything. The human-facing readouts did not: for the whole month a drain is in
flight the console stated the watermark's frozen figure while the pair that was
genuinely moving was never consulted, and the operator watching a backfill could
not tell "progressing" from "wedged".

Every seed here is deliberately discriminating: the watermark sits PAST the
events and the drain sits BEHIND them, so the watermark-relative count is zero
and the drain-relative count is not. Events seeded after both positions would
make the two counters agree, and such a test would be green before the fix and
measure nothing.

`test_curator_watermark_position.py` pins the pair itself; this file pins which
of the two a readout is allowed to answer with, and that only one place in the
codebase gets to decide.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.services import agent_service, curation_service, memory_service
from backend.services.curation_service import NEVER, Position
from backend.tasks import agent_schedules

from .test_curator import _register

INTERNAL = "internal"

# The discriminating window: three events inside it, the watermark ahead of it,
# the drain behind it. Fixed instants, so nothing here can flip on wall-clock skew.
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
DRAIN = BASE - timedelta(days=30)
WATERMARK = BASE + timedelta(days=1)


async def _lane(pool, uid: UUID, *, events: int, drain_at: datetime | None, cursor: dict):
    """The account, its curator row, and the two positions the test is about.

    Events land between `DRAIN` and `WATERMARK`, so the watermark has already
    counted them and the drain has not reached any of them. Returns the row as
    the routes and helpers receive it, with the pair freshly read back.
    """
    curator = await agent_service.get_or_create_curator(uid)
    for i in range(events):
        await memory_service.push_event(
            uid,
            "heavi-chat",
            "user_message",
            f"unread turn {i}",
            uid,
            f"conv-readout-{i}",
            created_at=BASE + timedelta(minutes=i),
        )
    await pool.execute(
        "UPDATE agents SET curated_through = $2, curated_through_event_id = NULL,"
        " drain_through = $3, drain_cursor = $4::jsonb WHERE id = $1",
        UUID(curator["id"]),
        WATERMARK,
        drain_at,
        cursor,
    )
    return await agent_service.get_agent(uid, UUID(curator["id"]))


@pytest.mark.asyncio
async def test_an_open_drain_is_the_position_the_readout_answers_with(client, pool):
    """A month-long drain must be reportable, or it is indistinguishable from a hang.

    The readout answers with the drain's own stored instant and counts from it, so
    the figure it carries is the work that drain has left — while the same helper
    asked at the watermark reports zero, which is the frozen number this card
    exists to remove.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=3, drain_at=DRAIN, cursor={})

    assert curation_service.readout_position_of(row) == Position(row["drain_through"])

    readout = await curation_service.curator_readout(uid, row)
    assert readout["draining"] is True
    assert readout["read_from"] == row["drain_through"]
    assert readout["distinct_events"] == 3
    # The old answer, on the same rows: a lane that has three events to read says zero.
    assert await curation_service.curator_event_backlog(
        uid, INTERNAL, curation_service.position_of(row)
    ) == {"distinct_events": 0, "raw_rows": 0, "distinct_sessions": 0}


@pytest.mark.asyncio
async def test_a_lane_with_no_drain_pair_keeps_counting_from_its_watermark(client, pool):
    """AC (b): with no pair stored, nothing about the readout's figures changes.

    A drain is a special case of one question, not a second counter: the closed-drain
    answer must be the figures `curator_event_backlog` itself produces at the
    watermark, key for key, with the watermark named as where they came from.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=3, drain_at=None, cursor={})

    readout = await curation_service.curator_readout(uid, row)

    assert readout["draining"] is False
    assert readout["read_from"] == row["curated_through"]
    assert readout == {
        **await curation_service.curator_event_backlog(
            uid, INTERNAL, curation_service.position_of(row), row["curator_folder_id"]
        ),
        "read_from": row["curated_through"],
        "draining": False,
    }


@pytest.mark.asyncio
async def test_a_drain_holding_only_its_cursor_still_owns_the_answer(client, pool):
    """A pair is a pair when either half is there, and the readout honours that.

    `drain_of` treats a non-empty cursor as the pair even with no instant, and
    `agent_service.py`'s own position rule words the no-drain case by
    `drain_through IS NULL` alone. A run resuming from that half has read nothing
    yet, so the readout names the same beginning-of-corpus position it does —
    never the watermark the lane has not actually consumed.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=3, drain_at=None, cursor={"partial": {"file:1": 10}})

    assert curation_service.readout_position_of(row) == NEVER
    readout = await curation_service.curator_readout(uid, row)
    assert readout["draining"] is True
    assert readout["read_from"] is None
    assert readout["distinct_events"] == 3


@pytest.mark.asyncio
async def test_a_committed_pass_makes_the_reported_number_fall(client, pool):
    """The operator's instrument: one committed pass, one smaller number.

    Only the pair moves here. The watermark is read back byte-for-byte identical
    before and after the pass, because `finish_drain` adopts a drain's ground only
    when the drain retires — which is exactly why a readout built on the watermark
    cannot report progress at all.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=3, drain_at=DRAIN, cursor={})

    async def stored_watermark():
        return await pool.fetchrow(
            "SELECT curated_through, curated_through_event_id FROM agents WHERE id = $1",
            UUID(row["id"]),
        )

    async def stored_drain():
        return await pool.fetchrow(
            "SELECT drain_through, drain_cursor FROM agents WHERE id = $1", UUID(row["id"])
        )

    async def committed_count():
        current = await agent_service.get_agent(uid, UUID(row["id"]))
        return (await curation_service.curator_readout(uid, current))["distinct_events"]

    before = await stored_watermark()
    assert await committed_count() == 3

    through = BASE + timedelta(minutes=1)
    async with pool.acquire() as conn, conn.transaction():
        await agent_service.advance_drain(conn, UUID(row["id"]), DRAIN, {}, through, {})

    assert (await stored_drain())["drain_through"] == through, "the pass did not commit"
    assert await stored_watermark() == before, "a draining pass moved the incremental watermark"
    assert await committed_count() == 1, "the reported backlog did not fall with the pass"


@pytest.mark.asyncio
async def test_the_pending_input_gate_asks_the_one_predicate(client, pool, monkeypatch):
    """One answer to "which position is live", or a fifth reader appears silently.

    `_pending_input` spelled the drain-or-watermark choice out inline; extracting it
    into `readout_position_of` is only durable if re-inlining it is a red test. The
    gate and the readout must be unable to disagree about what a lane has left.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=3, drain_at=DRAIN, cursor={})

    asked: list[dict] = []
    real = curation_service.readout_position_of

    def recording(agent: dict) -> Position:
        asked.append(agent)
        return real(agent)

    monkeypatch.setattr(curation_service, "readout_position_of", recording)

    assert await agent_schedules._pending_input(row, None) is True

    assert [a["id"] for a in asked] == [row["id"]]


@pytest.mark.asyncio
async def test_the_backlog_helper_gains_no_keys_and_the_readout_carries_two(client, pool):
    """The two new keys live on the readout, never on the counter it shares.

    `curator_event_backlog` is also the feed route's backlog, and a caller there
    names its own position — a `draining` flag computed from a different row would
    be a lie about a lane the request was never about. Whole-dict assertions across
    the identity and folder suites hold this shape from the other side.
    """
    key, uid = await _register(client)
    row = await _lane(pool, uid, events=2, drain_at=DRAIN, cursor={})

    assert set(
        await curation_service.curator_event_backlog(
            uid, INTERNAL, curation_service.position_of(row)
        )
    ) == {"distinct_events", "raw_rows", "distinct_sessions"}
    assert set(await curation_service.curator_readout(uid, row)) == {
        "distinct_events",
        "raw_rows",
        "distinct_sessions",
        "read_from",
        "draining",
    }
