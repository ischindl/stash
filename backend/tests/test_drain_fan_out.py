"""One drain, many positions: the per-part state inside the existing jsonb.

A history drain used to be one pair — `drain_through` beside `drain_cursor` — and one
run at a time could move it. Fan-out makes the pair plural: each part owns a slot inside
the SAME jsonb (no column, no migration), the scalar column becomes a DERIVED value —
the `from` of the oldest slot whose history is not yet folded into the wiki — and every
refusal a pass could hit gets named down to the slot that refused it.

These tests pin the state layer before any part runs: what a cursor may look like, what
`advance_drain` fences per part, what the column must always carry, how the parts tile a
timeline with no row read twice and none lost, and how the pair retires once the compose
pass has consumed every slot.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import agent_service, curation_service, sprite_agent_service
from backend.services.curation_service import (
    DRAIN_BEGINNING,
    Position,
    cut_drain_part,
    drain_derived_through,
    drain_frontier,
    drain_of,
    drain_parts_of,
    drain_seam_target,
    part_id_of,
)

from .test_curator import _push_events, _register

OLD = datetime(2020, 1, 1, tzinfo=UTC)
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
INTERNAL = "internal"


def _turn(session_id: str, content: str, at: datetime) -> dict:
    return {
        "agent_name": "heavi-chat",
        "event_type": "user_message",
        "content": content,
        "session_id": session_id,
        "created_at": at.isoformat(),
    }


def _cut_cursor(
    frontier: datetime, part_hours: int, parts_count: int, max_open: int = 4
) -> tuple[dict, list[str]]:
    """Cut `parts_count` windows forward from `frontier` with the pure cutter, and
    return the cursor beside the part ids in ascending order — the order itself is the
    subject: string order must BE instant order. `max_open` is only the constructor's
    own gate: a test that wants more slots than the default allowance passes a wider
    one, so the fixture and not the gate decides the shape."""
    target = frontier + timedelta(hours=part_hours * parts_count)
    cursor: dict = {}
    for _ in range(parts_count):
        cursor = cut_drain_part(
            cursor, frontier, part_hours=part_hours, target=target, max_open=max_open
        )
        assert cursor is not None, "the cutter refused a window the gate should admit"
    ids = sorted(cursor["parts"])
    assert ids == [
        part_id_of(frontier + timedelta(hours=part_hours * i)) for i in range(parts_count)
    ]
    return cursor, ids


async def _seed_drain(pool, agent_id: UUID, through: datetime | None, cursor: dict) -> None:
    await pool.execute(
        "UPDATE agents SET drain_through = $2::timestamptz, drain_cursor = $3::jsonb WHERE id = $1",
        agent_id,
        through,
        cursor,
    )


async def _stored(pool, agent_id: UUID) -> tuple:
    return await pool.fetchrow(
        "SELECT drain_through, drain_cursor, curated_through, curated_through_event_id,"
        " last_run_outcome FROM agents WHERE id = $1",
        agent_id,
    )


async def _advance(pool, agent_id, read_at, read_cursor, through, cursor, part_id=None) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await agent_service.advance_drain(
            conn, agent_id, read_at, read_cursor, through, cursor, part_id=part_id
        )


async def _curator(client, pool, uid) -> dict:
    curator = await agent_service.get_or_create_curator(uid)
    return await agent_service.get_curator_by_id(UUID(curator["id"]))


@pytest.mark.asyncio
async def test_two_slots_advance_independently_and_both_commits_land(client: AsyncClient, pool):
    """AC (a)'s state half: the fence is the slot's pair, not the row's column, so
    two parts committing seconds apart touch different slots of one jsonb and neither
    can refuse the other. The old CAS fenced the whole pair — the second part would
    have raised a conflict blaming the first."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    await _advance(
        pool,
        agent_id,
        OLD,
        {},
        OLD + timedelta(hours=1),
        {"partial": {"file:1": 3}},
        part_id=first,
    )
    # The second part read its own slot BEFORE the first committed and still lands:
    # its fence names its own pair, and neither commit moved that pair.
    await _advance(
        pool,
        agent_id,
        OLD + timedelta(hours=24),
        {},
        OLD + timedelta(hours=25),
        {},
        part_id=second,
    )

    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    assert datetime.fromisoformat(parts[first]["through"]) == OLD + timedelta(hours=1)
    assert datetime.fromisoformat(parts[second]["through"]) == OLD + timedelta(hours=25)
    # The column is not a first-write-wins fact: it is the oldest un-composed slot's
    # `from`, which neither commit moved.
    assert stored["drain_through"] == OLD


@pytest.mark.asyncio
async def test_a_write_on_a_stale_slot_is_refused_and_names_the_part(client: AsyncClient, pool):
    """The fence still fences: a pass holding the pair its slot stood on before its
    own commit (or before a compose pruned it) is refused, and the refusal names the
    slot — an operator reading it must not have to open the jsonb to learn which part
    lost the race. A slot the cursor does not carry (never cut, or already composed
    away) is the same refusal, never an implicit seed."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    await _advance(pool, agent_id, OLD, {}, OLD + timedelta(hours=1), {}, part_id=first)
    with pytest.raises(agent_service.CuratorDrainConflict) as moved:
        await _advance(pool, agent_id, OLD, {}, OLD + timedelta(hours=2), {}, part_id=first)
    assert first in str(moved.value)

    with pytest.raises(agent_service.CuratorDrainConflict) as ghost:
        await _advance(
            pool, agent_id, OLD, {}, OLD + timedelta(hours=3), {}, part_id="19700101T000000"
        )
    assert "19700101T000000" in str(ghost.value)
    # The untouched slot keeps its uncommitted shape: read it back, unchanged.
    stored = await _stored(pool, agent_id)
    assert stored["drain_cursor"]["parts"][second]["through"] is None


@pytest.mark.asyncio
async def test_the_derived_instant_is_the_oldest_uncomposed_slot_and_never_null(
    client: AsyncClient, pool
):
    """The column answers "how much history is not yet folded into the wiki", so it
    stands at the oldest slot's `from` — INCLUDING a `done` slot still holding its
    report — and only `finish_drain` may ever write NULL while a plan lives."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    assert (await _stored(pool, agent_id))["drain_through"] == OLD

    # Part one commits its whole window; a report is then written and the slot goes
    # `done` while it still stands un-composed.
    await _advance(pool, agent_id, OLD, {}, OLD + timedelta(hours=24), {}, part_id=first)
    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    marked = {
        **stored["drain_cursor"],
        "parts": {**parts, first: {**parts[first], "state": "done"}},
    }
    await _seed_drain(pool, agent_id, stored["drain_through"], marked)
    assert (await _stored(pool, agent_id))["drain_through"] == OLD, (
        "the column advanced past a done slot whose report is not yet composed"
    )

    # Composing slot one releases the seam: the column moves to slot two's `from`.
    stored = await _stored(pool, agent_id)
    async with pool.acquire() as conn, conn.transaction():
        edge_one, cursor_one = await agent_service.compose_drain(
            conn, agent_id, [first], stored["drain_through"], stored["drain_cursor"]
        )
    assert edge_one == OLD + timedelta(hours=24)
    assert (await _stored(pool, agent_id))["drain_through"] == OLD + timedelta(hours=24)

    # Part two commits and composes: with no slots left the column names the newest
    # composed edge — never NULL, because NULL beside a plan reads as "seeded at the
    # beginning" and that is a 140M-token re-read paid twice.
    await _advance(
        pool, agent_id, OLD + timedelta(hours=24), {}, OLD + timedelta(hours=48), {}, part_id=second
    )
    stored = await _stored(pool, agent_id)
    async with pool.acquire() as conn, conn.transaction():
        edge_all, cursor_all = await agent_service.compose_drain(
            conn, agent_id, [second], stored["drain_through"], stored["drain_cursor"]
        )
    assert edge_all == OLD + timedelta(hours=48)
    stored = await _stored(pool, agent_id)
    assert stored["drain_through"] == OLD + timedelta(hours=48)
    assert stored["drain_cursor"]["parts"] == {}

    # And the existing retirement hand-off clears both halves from that fully-composed
    # pair: NULL means no drain in flight, which only `finish_drain` writes. The
    # watermark hand-off goes through its own monotone clamp — this drain covered
    # history BEHIND the creation watermark, so the clamp keeps the later watermark
    # and the assertion is that the clear happened and nothing moved backwards.
    standing_watermark = curation_service.position_of(
        await agent_service.get_curator_by_id(agent_id)
    )
    async with pool.acquire() as conn, conn.transaction():
        await agent_service.finish_drain(
            conn,
            agent_id,
            edge_all,
            cursor_all,
            standing_watermark,
            Position(at=edge_all),
        )
    stored = await _stored(pool, agent_id)
    assert (stored["drain_through"], stored["drain_cursor"]) == (None, {})
    assert stored["curated_through"] == standing_watermark.at
    assert drain_of(await agent_service.get_curator_by_id(agent_id)) is None


@pytest.mark.asyncio
async def test_a_hand_edited_column_beside_parts_raises_rather_than_being_repaired(
    client: AsyncClient, pool
):
    """The column is a derivation written by the one UPDATE that moves a slot. A row
    whose column contradicts its parts was edited by a hand or a bug, and a reader
    that guessed which history to believe would invent one of its own."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, _ids = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    await pool.execute(
        "UPDATE agents SET drain_through = $2 WHERE id = $1", agent_id, OLD + timedelta(days=9)
    )
    with pytest.raises(ValueError, match="derive"):
        drain_of(await agent_service.get_curator_by_id(agent_id))
    # Hand-stamping NULL back while a plan lives is the same contradiction.
    await _seed_drain(pool, agent_id, None, cursor)
    with pytest.raises(ValueError):
        drain_of(await agent_service.get_curator_by_id(agent_id))


@pytest.mark.asyncio
async def test_a_seeded_drain_runs_as_exactly_one_part_with_its_instant_derived(
    client: AsyncClient, pool
):
    """The raw-SQL seed this suite itself uses — `drain_through` set, cursor empty —
    is the shape the cut must serve without a migration: one window from the standing
    instant, the standing instant becoming the first slot's `from`, and the column
    kept as the derivation of that slot rather than hand-stamped beside it."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    seeded_at = datetime(2026, 2, 2, tzinfo=UTC)
    await pool.execute(
        "UPDATE agents SET drain_through = $2, drain_cursor = '{}'::jsonb WHERE id = $1",
        agent_id,
        seeded_at,
    )
    standing, stored_cursor = drain_of(await agent_service.get_curator_by_id(agent_id))
    assert standing == seeded_at and stored_cursor == {}

    cursor = cut_drain_part(
        stored_cursor, standing, part_hours=6, target=seeded_at + timedelta(hours=6), max_open=6
    )
    assert cursor is not None
    (part_id,) = cursor["parts"]
    assert part_id == part_id_of(seeded_at)
    assert cursor["plan"] == {
        "part_hours": 6,
        "frontier": seeded_at.isoformat(),
        "target": (seeded_at + timedelta(hours=6)).isoformat(),
    }
    # Installing the plan moves the column as a derivation, not a hand stamp: the
    # value happens to equal the seed, and it is the OLDEST SLOT that says so.
    derived = drain_derived_through(cursor)
    assert derived == seeded_at
    await _seed_drain(pool, agent_id, derived, cursor)

    await _advance(
        pool, agent_id, seeded_at, {}, seeded_at + timedelta(hours=3), {}, part_id=part_id
    )
    stored = await _stored(pool, agent_id)
    assert datetime.fromisoformat(stored["drain_cursor"]["parts"][part_id]["through"]) == (
        seeded_at + timedelta(hours=3)
    )
    # Still derived from the only slot — the commit did not stamp the column at 3h.
    assert stored["drain_through"] == seeded_at


def test_the_first_slot_inherits_the_seeded_partial_offsets_and_they_move():
    """A drain stopped mid-document carries sparse `partial` offsets beside its
    instant. Cutting the first part MOVES those offsets into the slot's cursor: the
    parts map is authoritative once present, and a top-level copy left behind would be
    a second resumable position for two readers to disagree about."""
    cursor = cut_drain_part(
        {"partial": {"file:1": 10}},
        OLD,
        part_hours=24,
        target=OLD + timedelta(hours=24),
        max_open=4,
    )
    assert cursor is not None
    (part_id,) = cursor["parts"]
    assert cursor["parts"][part_id]["cursor"] == {"partial": {"file:1": 10}}
    assert "partial" not in cursor, "the offsets were copied, not moved"


@pytest.mark.asyncio
async def test_adjacent_parts_read_disjoint_rows_and_the_seam_belongs_to_the_earlier(
    client: AsyncClient, pool
):
    """AC (b) rests on this tiling: parts are `[from_i, to_i]` with `from_{i+1} = to_i`.
    The row sitting exactly on the shared instant is admitted by part one (`<= to`) and
    excluded by part two (whose lower bound reads strictly after its `from`), so no row
    is read twice and none is stranded. A half-open `[from, to)` would strand it: never
    shown, yet claimed by the position part one commits."""
    key, uid = await _register(client)
    seam = BASE + timedelta(minutes=20)
    await _push_events(
        client,
        key,
        [
            _turn("conv-tile", "before", BASE),
            _turn("conv-tile", "on-the-seam", seam),
            _turn("conv-tile", "after", seam + timedelta(minutes=10)),
        ],
    )

    earlier = await curation_service.changes_since(
        uid, uid, Position(at=BASE - timedelta(hours=1)), INTERNAL, None, seam
    )
    later = await curation_service.changes_since(
        uid, uid, Position(at=seam), INTERNAL, None, seam + timedelta(hours=1)
    )
    earlier_contents = [h["content"] for h in earlier["history"]]
    later_contents = [h["content"] for h in later["history"]]
    assert earlier_contents == ["before", "on-the-seam"]
    assert later_contents == ["after"]
    assert not set(earlier_contents) & set(later_contents)


@pytest.mark.asyncio
async def test_the_unparted_arm_refuses_a_planned_row_naming_the_agent(client: AsyncClient, pool):
    """The scoped lane calls `advance_drain` with six positionals and no part — and
    planning only ever happens on the personal lane. If its row ever carried a plan,
    the un-parted advance must refuse loudly rather than commit one slot's facts under
    an instant derived from another's."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, _ids = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    with pytest.raises(RuntimeError, match=str(agent_id)):
        async with pool.acquire() as conn, conn.transaction():
            # Six positionals, exactly the scoped lane's call shape.
            await agent_service.advance_drain(conn, agent_id, OLD, {}, OLD + timedelta(hours=1), {})


@pytest.mark.asyncio
async def test_two_slots_standing_at_one_seam_are_one_wall_not_two(client: AsyncClient, pool):
    """`drain_pass_budget` refuses a request two curators could answer, because one
    feed request cannot be bounded by two walls. Two SLOTS of one row standing at the
    seam — a part finished at its `to`, the next seeded at that same `to` — are one
    wall: the budget comes from the row, so the ambiguity test is per row, not per
    instant, and the seam is answered rather than refused."""
    from backend.services import scoped_curation_service

    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, _second) = _cut_cursor(OLD, 24, 2)
    parts = {
        k: {**slot, "through": (OLD + timedelta(hours=24)).isoformat() if k == first else None}
        for k, slot in cursor["parts"].items()
    }
    # Slot one finished at its `to`; slot two is seeded at that same instant and has
    # committed nothing — both stand at the seam.
    cursor = {**cursor, "parts": parts}
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    budget = await curation_service.drain_pass_budget(
        uid, Position(at=OLD + timedelta(hours=24)), INTERNAL
    )
    row = await agent_service.get_agent_by_id(agent_id)
    calibration = scoped_curation_service.curator_calibration
    rate, _source = await calibration.price(calibration.key_from_configuration(row))
    assert budget == scoped_curation_service.input_budget(
        agent_service.run_wall_seconds(row), rate
    ), "the seam was answered by something other than the row's own wall"
    # An instant between seams still answers nothing this drain is not.
    assert (
        await curation_service.drain_pass_budget(
            uid, Position(at=OLD + timedelta(hours=25, minutes=30)), INTERNAL
        )
        is None
    )


@pytest.mark.parametrize(
    "broken",
    [
        {"plan": {"part_hours": 24, "frontier": "x", "target": "y"}},  # plan, no parts key
        {"parts": "not-a-map"},  # parts that is not a map
        {
            "plan": {"part_hours": 24, "frontier": "x", "target": "y"},
            "parts": {"p": {"from": "2026-01-01T00:00:00+00:00", "state": "open"}},
        },  # slot with no `to`
        {
            "plan": {"part_hours": 24, "frontier": "x", "target": "y"},
            "parts": {
                "p": {
                    "from": "2026-01-01T00:00:00+00:00",
                    "to": "2026-01-02T00:00:00+00:00",
                    "state": "half-open",
                }
            },
        },  # a state word no reader knows
    ],
)
def test_a_cursor_no_reader_can_interpret_raises(broken: dict):
    """`plan` with no `parts`, a `parts` that is not a map, a slot missing its window,
    and a state word outside the named two are the shapes Design refuses; none is
    repaired in place, and the agent is named because the row is where the operator
    will look."""
    with pytest.raises(ValueError):
        drain_parts_of({"id": "agent-x", "drain_through": None, "drain_cursor": broken})


@pytest.mark.asyncio
async def test_the_cutter_refuses_at_the_open_gate_and_at_the_target(client: AsyncClient, pool):
    """The plan is generated forward under two brakes: open parts never number at or
    over the gate (the coordinator passes 2 x the endpoint bound), and no window is cut
    past the target. Refusing is a None from a pure function — the cursor the row
    carries cannot move while a window is merely imagined."""
    cursor, ids = _cut_cursor(OLD, 24, 2)  # the helper's own gate was 4
    target = OLD + timedelta(hours=72)
    third = cut_drain_part(cursor, OLD, part_hours=24, target=target, max_open=2)
    assert third is None, "a third open part slipped past a gate of two"

    cursor["parts"][ids[0]]["state"] = "done"
    grown = cut_drain_part(cursor, OLD, part_hours=24, target=target, max_open=2)
    assert grown is not None and len(grown["parts"]) == 3, (
        "closing a slot did not free the gate for the next window"
    )

    # Past the target there is no window to cut, however free the gate is.
    assert cut_drain_part(grown, OLD, part_hours=24, target=target, max_open=9) is None, (
        "a window was cut at or past the target"
    )

    # A plan's granularity is frozen: a caller wanting different hours needs a new plan.
    with pytest.raises(ValueError, match="part_hours"):
        cut_drain_part(grown, OLD, part_hours=12, target=target + timedelta(days=9), max_open=9)


def test_a_target_that_is_not_a_seam_cuts_no_window_at_all():
    """A window is cut at a declared granularity, so a window ending mid-second is not
    a window the plan ever promised.

    Cutting one anyway is the defect the live proof caught and no constructed cursor
    reached: the newest slot's `to` clamps to a live `now`, the next coordinator pass
    opens FROM that mid-second instant, and a part id names SECONDS — so that pass
    reaches for an id its neighbour already spent and dies naming two plans over one
    seam. A target short of the next seam is therefore no target at all.
    """
    cursor, _ = _cut_cursor(OLD, 1, 1)
    mid = OLD + timedelta(hours=1, seconds=45, microseconds=170_009)
    assert cut_drain_part(cursor, OLD, part_hours=1, target=mid, max_open=9) is None, (
        "a 45-second window was cut over a target that names no seam"
    )


def test_a_plan_chasing_now_keeps_every_seam_on_the_grid_it_promised():
    """Forty coordinator passes twenty seconds apart must cut ONE window, not forty.

    Each pass chases its own `now`, and a plan cut hour by hour has one seam per hour:
    the seam a pass may plan up to is the last one at or before its `now`, so the head
    of the plan waits for its hour instead of opening a window of microseconds that
    claims a dispatch, spends the planning gate's room, and repeats a spent part id.
    """
    cursor: dict = {}
    anchor = OLD
    for tick in range(40):
        now = OLD + timedelta(hours=1, minutes=13, seconds=20 * tick)
        nxt = cut_drain_part(
            cursor,
            anchor,
            part_hours=1,
            target=drain_seam_target(anchor, 1, now),
            max_open=6,
        )
        if nxt is not None:
            cursor = nxt
            anchor = drain_frontier(cursor, OLD)
    assert len(cursor["parts"]) == 1, "a plan chased its target into sliver windows"

    # The hours that do arrive earn exactly one on-grid window each.
    for _ in range(4):
        cursor = cut_drain_part(
            cursor,
            drain_frontier(cursor, OLD),
            part_hours=1,
            target=OLD + timedelta(hours=5),
            max_open=9,
        )
    assert len(cursor["parts"]) == 5
    for part_id, slot in cursor["parts"].items():
        opened = datetime.fromisoformat(slot["from"])
        assert part_id == part_id_of(opened)
        assert (datetime.fromisoformat(slot["to"]) - OLD) % timedelta(hours=1) == timedelta(0), (
            f"window {part_id} closes off the grid its plan promised"
        )


def test_a_covering_the_whole_corpus_slot_names_the_beginning_not_a_null():
    """A drain seeded with NO instant covers everything ever written; its first slot
    says so by naming `DRAIN_BEGINNING`, so the derivation still lands on a concrete
    instant and a NULL column keeps meaning exactly one thing: no drain in flight."""
    cursor = cut_drain_part(
        {}, None, part_hours=24, target=DRAIN_BEGINNING + timedelta(days=36_500), max_open=4
    )
    (part_id,) = cursor["parts"]
    assert part_id == part_id_of(DRAIN_BEGINNING)
    assert drain_derived_through(cursor) == DRAIN_BEGINNING


# ---------------------------------------------------------------------------
# The part as a lock subject: AC (a)'s running half
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_part_locks_of_one_curator_are_held_together_and_named_exactly(
    client: AsyncClient, pool, monkeypatch
):
    """AC (a): the second shard of one curator used to die on `is busy; skipping`
    because the lane key was the agent id. The key gains `part:<part_id>` ONLY when
    a part is named — so the pins elsewhere in this suite that assert
    `held_at_failure == [f"agent-run:{agent_id}"]` stay true untouched, and this
    test names the full keys rather than the prefix because the prefix filter in
    `test_agent_run_resolution.py` also SEES part keys and must not be the only
    thing proving their shape.

    The un-parted key stays a distinct subject a compose pass can take while parts
    run: that separation is what makes the compose pass the only writer of shared
    wiki pages at any instant."""
    from backend.services import sprite_agent_service
    from backend.tasks import agent_schedules

    from .conftest import FakeRedis

    redis = FakeRedis()
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: redis)
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent = await agent_service.get_agent_by_id(UUID(curator["id"]))
    agent_id = str(agent["id"])

    first_part = agent_schedules._run_lock(agent, None, part_id="20200101T000000")
    second_part = agent_schedules._run_lock(agent, None, part_id="20200102T000000")
    await first_part.acquire()
    await second_part.acquire()  # AC (a): no TurnInProgress for a part dispatch

    held = sorted(key for key in redis.data if key.startswith("agent-run:"))
    assert held == [
        f"agent-run:{agent_id}:part:20200101T000000",
        f"agent-run:{agent_id}:part:20200102T000000",
    ]

    # One part cannot double-run onto itself...
    with pytest.raises(sprite_agent_service.TurnInProgress):
        await agent_schedules._run_lock(agent, None, part_id="20200101T000000").acquire()

    # ...while the un-parted subject is free to take its own lane beside them, and
    # refuses only a second un-parted run.
    compose_lane = agent_schedules._run_lock(agent, None)
    await compose_lane.acquire()
    assert f"agent-run:{agent_id}" in redis.data
    with pytest.raises(sprite_agent_service.TurnInProgress):
        await agent_schedules._run_lock(agent, None).acquire()

    # The scoped fence never gains a part: its key is upstream's.
    with pytest.raises(RuntimeError, match="scoped lane has no parts"):
        agent_schedules._run_lock(agent, "some-workspace", part_id="20200101T000000")

    await compose_lane.release()
    await second_part.release()
    await first_part.release()
    assert [key for key in redis.data if key.startswith("agent-run:")] == []


@pytest.mark.asyncio
async def test_a_dead_parts_slot_is_reclaimable_after_its_deadline_and_not_before(
    client: AsyncClient, pool
):
    """The sweep cannot help a part (it deletes the agent-wide key only, and on a
    copy that must not run beat there is no sweep at all), so a wedged part frees
    by two clocks: the Redis TTL and the slot's `claim_expires_at`. This is the
    second clock: a live claim refuses, an aged-out claim is taken, and taking it
    does not disturb the pair the resumed pass will fence on."""
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    t0 = OLD + timedelta(hours=5)
    async with pool.acquire() as conn, conn.transaction():
        assert await agent_service.claim_drain_part(
            conn, agent_id, first, now=t0, claim_seconds=600
        )
    # Before the deadline: the holder may still be running; the slot refuses.
    async with pool.acquire() as conn, conn.transaction():
        assert not await agent_service.claim_drain_part(
            conn, agent_id, first, now=t0 + timedelta(seconds=300), claim_seconds=600
        )
    # After it: the worker is evidence-free, and the slot is resumable.
    async with pool.acquire() as conn, conn.transaction():
        assert await agent_service.claim_drain_part(
            conn, agent_id, first, now=t0 + timedelta(seconds=601), claim_seconds=600
        )
    # The pair under the claim is untouched: the resumed pass fences on the same one.
    await _advance(pool, agent_id, OLD, {}, OLD + timedelta(hours=2), {}, part_id=first)

    # A slot that is `done` or absent claims nothing: the report is written, or the
    # window has been composed, and a turn spent on either is a turn wasted.
    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    marked = {
        **stored["drain_cursor"],
        "parts": {**parts, second: {**parts[second], "state": "done"}},
    }
    await _seed_drain(pool, agent_id, stored["drain_through"], marked)
    async with pool.acquire() as conn, conn.transaction():
        assert not await agent_service.claim_drain_part(
            conn, agent_id, second, now=t0, claim_seconds=600
        )
        assert not await agent_service.claim_drain_part(
            conn, agent_id, "19700101T000000", now=t0, claim_seconds=600
        )


@pytest.mark.asyncio
async def test_dropping_a_part_lock_reaches_only_the_part_key(
    client: AsyncClient, pool, monkeypatch
):
    """The escape guard drops the key its own body held. Reaching past a dead part
    to the agent-wide key would unlock a compose pass or an incremental run that is
    still executing — the agent-wide drop stays the sweep's and the non-part
    guard's, exactly as it was before parts."""
    from backend.services import sprite_agent_service

    from .conftest import FakeRedis

    redis = FakeRedis()
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: redis)
    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    part_key = f"agent-run:{agent_id}:part:20200101T000000"
    agent_key = f"agent-run:{agent_id}"
    redis.data[part_key] = b"dead-worker"
    redis.data[agent_key] = b"compose-still-running"

    await sprite_agent_service.drop_agent_run_lock(agent_id, "20200101T000000")
    assert part_key not in redis.data
    assert agent_key in redis.data, "dropping a part unlocked the agent-wide lane"

    await sprite_agent_service.drop_agent_run_lock(agent_id)
    assert agent_key not in redis.data


# ---------------------------------------------------------------------------
# The part as a run: coordinator, gauge, empty windows, report page
# ---------------------------------------------------------------------------


class _GaugeRedis:
    """FakeRedis plus the sorted-set verbs the endpoint gauge uses.

    Kept in this file rather than `conftest.FakeRedis` because the gauge is the
    only consumer of zsets in the suite, and `conftest.py` is outside this card's
    file scope."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value.encode() if isinstance(value, str) else value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def zrem(self, key, member):
        return int(self.zsets.get(key, {}).pop(member, None) is not None)

    async def zremrangebyscore(self, key, low, high):
        live = self.zsets.get(key, {})
        expired = [member for member, score in live.items() if low <= score <= high]
        for member in expired:
            live.pop(member)
        return len(expired)


@pytest.fixture
def part_env(monkeypatch):
    """The outside world one part run touches, as stubs: the gauge Redis, the
    endpoint resolver (a platform-funded harness, nothing pinned), and the model
    turn itself — captured so prompt text and call counts are assertable, canned
    so no HTTP leaves the test."""
    from types import SimpleNamespace

    from backend.services import agent_auth, sprite_agent_service

    redis = _GaugeRedis()
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: redis)

    async def fake_resolve(user_id, prefer_provider=None, credential_id=None, model_id=None):
        return SimpleNamespace(endpoint=None, harness="claude")

    monkeypatch.setattr(agent_auth, "resolve", fake_resolve)

    digest_calls: list[tuple[str, str]] = []

    async def fake_run_chat(
        owner_user_id,
        owner_name,
        user_id,
        session_id,
        message,
        channel=None,
        model_provider=None,
        model_id=None,
        credential_id=None,
        persona=None,
        agent_name=None,
        agent_id=None,
    ):
        digest_calls.append((session_id, message))
        return "extracts: the window's distilled report"

    monkeypatch.setattr(sprite_agent_service, "run_chat", fake_run_chat)

    dispatched: list[tuple] = []
    from backend.tasks import agent_schedules

    monkeypatch.setattr(
        agent_schedules.run_curator_part,
        "apply_async",
        lambda args=None, kwargs=None, **extra: dispatched.append((args, kwargs)),
    )
    # The compose pass is dispatched as an ordinary curator run, so the queue that
    # carries it is part of the outside world a part run touches too. It records into the
    # same list: a `run_curator_part` dispatch carries two args (agent, part) and an
    # ordinary run carries one, which is how the assertions below tell them apart.
    monkeypatch.setattr(
        agent_schedules.run_curator_now,
        "apply_async",
        lambda args=None, kwargs=None, **extra: dispatched.append((args, kwargs)),
    )
    return SimpleNamespace(redis=redis, digest_calls=digest_calls, dispatched=dispatched)


async def _digest_curator(client: AsyncClient, pool, uid) -> dict:
    curator = await _curator(client, pool, uid)
    await pool.execute(
        "UPDATE agents SET digest_provider = 'anthropic' WHERE id = $1", UUID(curator["id"])
    )
    return await agent_service.get_agent_by_id(UUID(curator["id"]))


@pytest.mark.asyncio
async def test_a_part_run_commits_only_its_slot_writes_only_its_report_and_chains(
    client: AsyncClient, pool, monkeypatch, part_env, caplog
):
    """The part body's whole contract in one run: the digest turn reads the closed
    window (its prompt carries `--until`), the commit lands on the part's own slot,
    the report reaches the server-written page named from the part id, the next part
    is handed off, and the row resolves `ran` — while the two pinned log shapes
    survive: exactly one `drain part=` line, and zero `turn started` records, which
    is what keeps `_turn_bracket`'s counts for the un-parted run untouched."""
    import logging

    from backend.tasks import agent_schedules

    caplog.set_level(logging.INFO, logger="backend.tasks.agent_schedules")
    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(
        client,
        key,
        [_turn(f"w1-{i}", f"work {i}", OLD + timedelta(hours=1 + i)) for i in range(2)],
    )

    await agent_schedules._run_curator_part(agent_id, first, metered=False)

    stored = await _stored(pool, agent_id)
    slot1 = stored["drain_cursor"]["parts"][first]
    assert slot1["state"] == "done" and slot1["through"] == (OLD + timedelta(hours=24)).isoformat()
    assert stored["drain_cursor"]["parts"][second]["through"] is None, (
        "the run reached past its window into the neighbouring slot"
    )
    assert stored["last_run_outcome"] == "ran"

    assert len(part_env.digest_calls) == 1
    assert f"--until {(OLD + timedelta(hours=24)).isoformat()}" in part_env.digest_calls[0][1], (
        "the digest prompt read open-ended instead of to the part's closed edge"
    )

    owner = UUID(str(curator["user_id"]))
    pages = await pool.fetch(
        "SELECT name, content_markdown FROM pages WHERE owner_user_id = $1 AND name = $2",
        owner,
        f"drain-report-{first}",
    )
    assert len(pages) == 1, "the report page must be the one deterministic page for the part"
    assert "the window's distilled report" in pages[0]["content_markdown"]

    part_lines = [r.getMessage() for r in caplog.records if "drain part=" in r.getMessage()]
    assert sum(1 for line in part_lines if "committed_through=" in line) == 1, part_lines
    assert not [line for line in part_lines if "turn started" in line or "turn finished" in line]
    assert not [r.getMessage() for r in caplog.records if "turn started" in r.getMessage()], (
        "a part run printed the un-parted run's turn bracket"
    )

    assert part_env.dispatched, "the part completed without handing the pipeline on"


@pytest.mark.asyncio
async def test_a_second_part_runs_while_the_first_part_lane_is_held(
    client: AsyncClient, pool, monkeypatch, part_env
):
    """AC (a)'s running half, at body level: with part one's lane held by a dispatch
    that will not return, a second dispatch of THAT part declines with its named
    guard and spends nothing, while a dispatch of the other part runs to its own
    commit. Before this card both second dispatches logged `is busy; skipping`."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])

    held = agent_schedules._run_lock(
        await agent_service.get_agent_by_id(agent_id), None, part_id=first
    )
    await held.acquire()
    try:
        await agent_schedules._run_curator_part(agent_id, first, metered=False)
        row = await _stored(pool, agent_id)
        assert row["last_run_outcome"] is None, "the refused second dispatch spent a charge"
        assert row["drain_cursor"]["parts"][first]["through"] is None

        await agent_schedules._run_curator_part(agent_id, second, metered=False)
    finally:
        await held.release()

    row = await _stored(pool, agent_id)
    assert (
        row["drain_cursor"]["parts"][second]["through"] == (OLD + timedelta(hours=48)).isoformat()
    )
    assert row["last_run_outcome"] == "ran"
    # The held part is untouched by the second run's success: its own row-stamp is
    # still unspent, proving the declined dispatch resolved nothing.
    row_first = row["drain_cursor"]["parts"][first]
    assert row_first["through"] is None and row_first["state"] == "open"


@pytest.mark.asyncio
async def test_a_full_gauge_declines_the_part_naming_guard_size_and_limit(
    client: AsyncClient, pool, monkeypatch, part_env, caplog
):
    """The declined part prints `guard=endpoint_pressure` carrying gauge size and
    limit, and spends no turn and no credit: `mark_run` never ran (the month count
    and the outcome trio are untouched) and the digest call count stays zero."""
    import logging

    from backend.config import settings
    from backend.tasks import agent_schedules

    caplog.set_level(logging.INFO, logger="backend.tasks.agent_schedules")
    monkeypatch.setattr(settings, "CURATOR_DRAIN_MAX_CONCURRENT_TURNS", 1)
    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, _second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work", OLD + timedelta(hours=1))])

    part_env.redis.zsets["drain-turns:harness:claude"] = {"other-agent:other-part": 1e12}
    before = await pool.fetchval("SELECT month_run_count FROM agents WHERE id = $1", agent_id)

    await agent_schedules._run_curator_part(agent_id, first, metered=False)

    lines = [r.getMessage() for r in caplog.records if "declined" in r.getMessage()]
    assert any("guard=endpoint_pressure" in line and "gauge=1 limit=1" in line for line in lines), (
        lines
    )
    assert part_env.digest_calls == []
    after = await pool.fetchrow(
        "SELECT month_run_count, last_run_outcome FROM agents WHERE id = $1", agent_id
    )
    assert after["month_run_count"] == before
    assert after["last_run_outcome"] is None
    row = await _stored(pool, agent_id)
    assert row["drain_cursor"]["parts"][first]["claim_expires_at"] is None, (
        "a part that spent nothing left a claim standing, which would hold its slot out "
        "of the next dispatch for the whole claim horizon"
    )


@pytest.mark.asyncio
async def test_an_empty_window_earns_its_edge_without_a_model_turn(
    client: AsyncClient, pool, monkeypatch, part_env
):
    """A planned drain's early windows can sit behind the first row. The empty
    check runs BEFORE the gauge and before `mark_run`: the window commits its own
    edge, the slot finishes with the server's one-line report, and not a turn, a
    credit, or a gauge member was spent."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, _second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("late", "much later", OLD + timedelta(hours=100))])

    await agent_schedules._run_curator_part(agent_id, first, metered=False)

    assert part_env.digest_calls == []
    assert part_env.redis.zsets == {}, "the empty path must never touch the endpoint gauge"
    stored = await _stored(pool, agent_id)
    slot = stored["drain_cursor"]["parts"][first]
    assert slot["state"] == "done" and slot["through"] == (OLD + timedelta(hours=24)).isoformat()
    assert stored["last_run_outcome"] is None, "an empty window spent a charge"


@pytest.mark.asyncio
async def test_the_planning_gate_refuses_to_cut_past_two_times_the_endpoint_bound(
    client: AsyncClient, pool, monkeypatch, part_env
):
    """The coordinator cuts only while open parts number under 2 x the endpoint
    bound — claims exist only where a worker could soon run them — and a slot whose
    claim is alive is not dispatched, while its free neighbour is."""
    from backend.config import settings
    from backend.tasks import agent_schedules

    monkeypatch.setattr(settings, "CURATOR_DRAIN_MAX_CONCURRENT_TURNS", 3)
    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, ids = _cut_cursor(OLD, 24, 6, max_open=9)
    claimed_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    cursor["parts"][ids[0]]["claim_expires_at"] = claimed_at
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)

    await agent_schedules._coordinate_drain_parts(await agent_service.get_agent_by_id(agent_id))

    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    assert len(parts) == 6, "the gate let a seventh window stand open"
    dispatched_ids = [args[1] for args, _kwargs in part_env.dispatched]
    assert ids[0] not in dispatched_ids, (
        "a part with a live claim was dispatched onto a second turn"
    )
    assert ids[1] in dispatched_ids
    assert dispatched_ids == sorted(dispatched_ids), "the hand-off lost the ascending order"


@pytest.mark.asyncio
async def test_the_first_window_of_a_fresh_drain_spans_the_empty_head(
    client: AsyncClient, pool, monkeypatch
):
    """`DRAIN_BEGINNING` is a sentinel, not a date with history under it: the first
    window of a fresh full-history drain reaches from it to the corpus's first
    event, instead of opening the gate's whole allowance of empty year-1900s
    windows. The row standing ON the first event belongs to that first window
    (closed upper edge), never to the one whose `from` it is."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    await _push_events(client, key, [_turn("early", "the first row", BASE)])

    await agent_schedules._plan_new_drain(await agent_service.get_agent_by_id(agent_id))

    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    assert len(parts) == 1
    only = next(iter(parts.values()))
    assert only["from"] == DRAIN_BEGINNING.isoformat()
    assert only["to"] == BASE.isoformat(), (
        "the first window stopped one part-width from the sentinel, leaving the corpus's "
        "earliest rows behind the plan's first edge"
    )
    assert stored["drain_through"] == DRAIN_BEGINNING, (
        "the derived column must carry the sentinel while the first slot is the oldest"
    )


# ---------------------------------------------------------------------------
# The compose pass: the drain's only shared-wiki writer
# ---------------------------------------------------------------------------


async def _report_page_provenance(pool, owner: UUID, part_id: str) -> dict:
    """The `page_edits` row a part's report page carries, which is where a reviewer looks
    to see who wrote a page and whether a session claims it."""
    return await pool.fetchrow(
        "SELECT pe.agent_name, pe.session_id FROM page_edits pe JOIN pages p ON p.id = pe.page_id"
        " WHERE p.owner_user_id = $1 AND p.name = $2",
        owner,
        f"drain-report-{part_id}",
    )


async def _mark_done(pool, agent_id: UUID, part_id: str) -> None:
    """Put one slot in the state a finished part leaves behind — `done`, its claim
    released — without running a part. The compose tests below ask about the cursor a fold
    receives; a test that reached it only through real part runs would also inherit the
    coordinator's forward cut, which is Step 4's subject and another test's."""
    async with pool.acquire() as conn, conn.transaction():
        await agent_service.mark_drain_part_done(conn, agent_id, part_id)


@pytest.mark.asyncio
async def test_a_part_report_page_is_attributed_to_the_drain_part_and_to_no_session(
    client: AsyncClient, pool, part_env
):
    """AC (d)'s provenance half: the report is a SERVER write, so its `page_edits` row
    names the standing actor `curator-drain-part` and carries no session — the write is
    nobody's chat turn. A page name chosen by a model would have let two parts of one
    curator land on one page; this row is what a reviewer checks."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, _second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])

    await agent_schedules._run_curator_part(agent_id, first, metered=False)

    edit = await _report_page_provenance(pool, UUID(str(curator["user_id"])), first)
    assert edit is not None, "the report page carries no page_edits row at all"
    assert edit["agent_name"] == "curator-drain-part"
    assert edit["session_id"] is None, "a server write claimed a chat session as its author"


@pytest.mark.asyncio
async def test_reports_fold_in_part_order_whatever_order_the_parts_finished_in(
    client: AsyncClient, pool, monkeypatch, part_env
):
    """AC (b): what a drain composes is a function of the report set and the part order,
    never of which worker finished first. Three windows are read and committed out of
    order — newest first — and the one writer turn that folds them sees them oldest-first,
    read back from their page names.

    The assertion is on the ORDER the reports arrive, not on the wiki's finished content:
    a model's synthesis is not reproducible here, and the property this card exists to hold
    is the order its inputs reach the writer in."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    owner = UUID(str(curator["user_id"]))
    cursor, (first, second, third) = _cut_cursor(OLD, 24, 3)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])
    await _push_events(client, key, [_turn("w3", "work three", OLD + timedelta(hours=49))])

    # Each part's report says which window it read, so the fold's input order is visible in
    # the writer's prompt instead of inferred from a canned reply three parts share.
    async def _report_the_window(
        owner_user_id,
        owner_name,
        user_id,
        session_id,
        message,
        channel=None,
        model_provider=None,
        model_id=None,
        credential_id=None,
        persona=None,
        agent_name=None,
        agent_id=None,
    ):
        part_env.digest_calls.append((session_id, message))
        closing = re.search(r"--until (\S+)", message)
        return (
            f"the window ending {closing.group(1)} spoke here"
            if closing
            else "the writer turn folded the reports"
        )

    monkeypatch.setattr(sprite_agent_service, "run_chat", _report_the_window)

    before_fold = await _stored(pool, agent_id)
    for part_id in (third, first, second):
        await agent_schedules._run_curator_part(agent_id, part_id, metered=False)
    assert len(part_env.digest_calls) == 3, "the three parts did not each spend one digest turn"

    part_env.digest_calls.clear()
    assert await agent_schedules._compose_drain_parts(await agent_service.get_agent_by_id(agent_id))

    assert len(part_env.digest_calls) == 1, (
        "folding three reports is ONE writer turn — a second means the pass read the feed "
        "again instead of working from the reports"
    )
    fold_session, fold_message = part_env.digest_calls[0]
    assert fold_session.startswith(f"agent-curate-{agent_id}-") and not fold_session.endswith(
        sprite_agent_service.DIGEST_SESSION_SUFFIX
    ), f"the fold was not the wiki writer's own turn: session {fold_session}"
    # Each part's report names the window it read — the canned digest reply says so — so
    # the order the BODIES arrive in is assertable, not just the order of the page headings
    # the server wrote around them.
    arrival = [
        fold_message.index(f"the window ending {(OLD + timedelta(hours=hours)).isoformat()}")
        for hours in (24, 48, 72)
    ]
    assert arrival == sorted(arrival), (
        "the reports reached the writer in the order the parts finished, not in part order"
    )
    # The span the writer is told to read is the composed span: the oldest part's opening
    # and the newest composed edge, in words, so the fold is about a bounded history.
    edge = OLD + timedelta(hours=72)
    assert f"the changes between {OLD.isoformat()} and {edge.isoformat()}" in fold_message, (
        "the fold's prompt did not name the composed span it was writing from"
    )

    stored = await _stored(pool, agent_id)
    for part_id in (first, second, third):
        assert part_id not in stored["drain_cursor"]["parts"], f"{part_id} was not pruned"
    assert stored["curated_through"] == before_fold["curated_through"], (
        "the watermark took over a drain that still has windows standing behind the folded prefix"
    )
    assert stored["drain_through"] is not None and stored["drain_cursor"]["plan"], (
        "the fold cleared a pair its drain had not finished"
    )
    # The reports stay where they were written: folding a report is not deleting it.
    for part_id in (first, second, third):
        assert await _report_page_provenance(pool, owner, part_id) is not None


@pytest.mark.asyncio
async def test_a_fully_composed_drain_hands_its_covered_past_to_the_watermark(
    client: AsyncClient, pool, part_env
):
    """Retirement is the compose pass's own transaction: the last slot prunes, the plan's
    target is behind the newest composed edge, and the pending-input gate asked AT that edge
    answers nothing — so the pair clears and the watermark takes the covered past.

    The gate is asked of the edge rather than of the column on purpose: the column of a row
    with reports still waiting stands at the OLDEST un-composed slot, and asking it would
    answer "pending" about history this very pass already folded."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])
    for part_id in (first, second):
        slot = cursor["parts"][part_id]
        await _advance(
            pool,
            agent_id,
            datetime.fromisoformat(slot["from"]),
            {},
            datetime.fromisoformat(slot["to"]),
            {},
            part_id=part_id,
        )
        await agent_schedules._write_part_report_page(
            UUID(str(curator["user_id"])), part_id, "the window's distilled report"
        )
        await _mark_done(pool, agent_id, part_id)

    assert await agent_schedules._compose_drain_parts(await agent_service.get_agent_by_id(agent_id))

    stored = await _stored(pool, agent_id)
    assert (stored["drain_through"], stored["drain_cursor"]) == (None, {}), (
        "a fully-composed drain left its pair behind: the next dispatch would re-read the "
        "history the watermark now covers"
    )
    assert stored["curated_through"] == OLD + timedelta(hours=48), (
        "the hand-off did not carry the watermark to the newest composed edge"
    )
    assert drain_of(await agent_service.get_curator_by_id(agent_id)) is None


@pytest.mark.asyncio
async def test_rows_arriving_past_the_target_fold_without_retiring_the_drain(
    client: AsyncClient, pool, part_env
):
    """A row that arrives past the plan's target answers the pending gate "still pending",
    so the reports fold and their slots prune but the pair stays — naming the newest
    composed edge, never NULL beside a plan — and the coordinator then cuts one more window
    FROM that edge instead of re-cutting history or retiring a drain with rows ahead of it."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])
    # Rows arriving while the drain works: past the plan's target, ahead of the edge.
    await _push_events(client, key, [_turn("w3", "arrived late", OLD + timedelta(hours=60))])
    for part_id in (first, second):
        slot = cursor["parts"][part_id]
        await _advance(
            pool,
            agent_id,
            datetime.fromisoformat(slot["from"]),
            {},
            datetime.fromisoformat(slot["to"]),
            {},
            part_id=part_id,
        )
        await agent_schedules._write_part_report_page(
            UUID(str(curator["user_id"])), part_id, "the window's distilled report"
        )
        await _mark_done(pool, agent_id, part_id)

    before = await _stored(pool, agent_id)
    assert await agent_schedules._compose_drain_parts(await agent_service.get_agent_by_id(agent_id))

    stored = await _stored(pool, agent_id)
    assert stored["curated_through"] == before["curated_through"], (
        "the watermark took over history the drain has not finished: rows sit ahead of the "
        "edge it was handed"
    )
    assert stored["drain_cursor"]["parts"] == {}, "the composed slots were not pruned"
    assert stored["drain_through"] == OLD + timedelta(hours=48), (
        "the fully-composed pair must name the newest composed edge, never NULL beside a plan"
    )

    await agent_schedules._coordinate_drain_parts(
        await agent_service.get_agent_by_id(agent_id), compose_here=True
    )
    stored = await _stored(pool, agent_id)
    parts = stored["drain_cursor"]["parts"]
    assert parts, "the coordinator did not cut the window the late rows earned"
    oldest = parts[min(parts)]
    assert oldest["from"] == (OLD + timedelta(hours=48)).isoformat(), (
        "the new window re-cut history instead of continuing from the composed edge"
    )
    assert stored["drain_through"] == OLD + timedelta(hours=48), (
        "the derived instant moved for a window that has been read by nobody"
    )


@pytest.mark.asyncio
async def test_a_done_slot_behind_an_open_one_waits_for_its_neighbour(
    client: AsyncClient, pool, part_env
):
    """A part that died before committing leaves its slot's position untouched, and the
    reports written after it wait: folding the newer windows would put newer knowledge on
    the wiki before older, which is the arrival-order accident AC (b) forbids. The column
    stays at the dead slot's `from` — that history is still unfolded — and nothing prunes,
    so a pass that folds nothing spends nothing.

    This is AC (a)'s other half at the compose boundary: an open slot is never pruned, so
    its position cannot be moved by a pass that did not read it."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second, third) = _cut_cursor(OLD, 24, 3)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])
    await _push_events(client, key, [_turn("w3", "work three", OLD + timedelta(hours=49))])

    for part_id in (second, third):
        await agent_schedules._run_curator_part(agent_id, part_id, metered=False)

    part_env.dispatched.clear()
    part_env.digest_calls.clear()
    folded = await agent_schedules._compose_drain_parts(
        await agent_service.get_agent_by_id(agent_id)
    )
    assert folded is False, "the pass folded past an open slot"
    assert part_env.digest_calls == [], "a pass that folds nothing spent a model turn"
    stored = await _stored(pool, agent_id)
    assert stored["drain_through"] == OLD, (
        "the derived instant moved while the oldest slot's history is still unfolded"
    )
    dead = stored["drain_cursor"]["parts"][first]
    assert (dead["state"], dead["through"], dead["cursor"]) == ("open", None, {}), (
        f"the dead slot was not left exactly as it stood: {dead}"
    )

    # And the wait is not a silent park: every coordinator pass keeps handing the owed slot
    # to a worker, and dispatches nobody to fold what cannot be folded.
    await agent_schedules._coordinate_drain_parts(await agent_service.get_agent_by_id(agent_id))
    assert [str(agent_id), first] in [
        args for args, _kwargs in part_env.dispatched if len(args) == 2
    ], "the coordinator stopped dispatching the slot that is still owed"
    assert not [args for args, _kwargs in part_env.dispatched if len(args) == 1], (
        "a fold was dispatched while the oldest slot still stands open"
    )


@pytest.mark.asyncio
async def test_reports_waiting_earn_the_ordinary_run_while_other_parts_still_run(
    client: AsyncClient, pool, part_env
):
    """A compose pass is an ordinary curator run with no part id, and that dispatch is what
    a coordinator makes the moment a prefix of reports waits — while other parts are still
    open, because the fold takes the un-partitioned lock and a part takes only its own.
    Making the fold wait for the last part would strand every composed window un-folded
    across the whole drain; sending it through `run_curator_part` would put a shared-wiki
    writer behind a part key, where a second part could run beside it."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, _second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])

    await agent_schedules._run_curator_part(agent_id, first, metered=False)

    ordinary = [
        (args, kwargs)
        for args, kwargs in part_env.dispatched
        if len(args) == 1 and args == [str(agent_id)]
    ]
    assert ordinary, "the pipeline waited on a fold that nobody was dispatched to perform"
    assert ordinary[0][1] == {"metered": False}, "the fold charged the customer for its own work"
    assert [str(agent_id), _second] in [
        args for args, _kwargs in part_env.dispatched if len(args) == 2
    ], "waiting for a fold stopped the parts that were still owed their turn"


@pytest.mark.asyncio
async def test_two_reports_about_one_page_reach_one_writer_carrying_both_claims(
    client: AsyncClient, pool, monkeypatch, part_env
):
    """AC (b)'s substance: two windows that both speak about the same page must not
    arrive as two writers last-write-winning over each other. The fold hands both reports
    to ONE writer turn, so whatever that turn writes, it writes knowing both claims.

    The page this double writes stands in for the wiki write the real writer turn makes
    through the page routes: the claims it lands are read out of the prompt it was given,
    so a fold that handed over only one report would produce a page with one claim."""
    from backend.tasks import agent_schedules

    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    owner = UUID(str(curator["user_id"]))
    cursor, (first, second) = _cut_cursor(OLD, 24, 2)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])

    wiki_writes: list[str] = []

    async def _both_claims_reach_the_page(
        owner_user_id,
        owner_name,
        user_id,
        session_id,
        message,
        channel=None,
        model_provider=None,
        model_id=None,
        credential_id=None,
        persona=None,
        agent_name=None,
        agent_id=None,
    ):
        part_env.digest_calls.append((session_id, message))
        claims = re.findall(r"the window ending (\S+) spoke here", message)
        if not claims:
            # A digest turn reads one window and reports on it; the wiki turn below is the
            # only caller that ever sees more than one window at once.
            closing = re.search(r"--until (\S+)", message)
            return f"the window ending {closing.group(1)} spoke here"
        wiki_writes.append(message)
        r = await client.post(
            "/api/v1/me/pages/new",
            json={"name": "locks", "content": "\n".join(claims)},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 201, r.text
        return "folded"

    monkeypatch.setattr(sprite_agent_service, "run_chat", _both_claims_reach_the_page)

    # Finished in the wrong order, so a fold that read them as they came would be caught.
    for part_id in (second, first):
        await agent_schedules._run_curator_part(agent_id, part_id, metered=False)
    assert await agent_schedules._compose_drain_parts(await agent_service.get_agent_by_id(agent_id))

    assert len(wiki_writes) == 1, (
        f"the fold spent {len(wiki_writes)} writer turns for one prefix of reports: the "
        "collision between two reports about one page is exactly what one writer prevents"
    )
    pages = await pool.fetch(
        "SELECT id, content_markdown FROM pages WHERE owner_user_id = $1 AND name = 'locks' "
        "AND deleted_at IS NULL",
        owner,
    )
    assert len(pages) == 1, "two pages named `locks` — a page per report, not one page per topic"
    body = pages[0]["content_markdown"]
    assert body.split() == [
        (OLD + timedelta(hours=24)).isoformat(),
        (OLD + timedelta(hours=48)).isoformat(),
    ], f"the one page carries the claims out of part order: {body!r}"


@pytest.mark.asyncio
async def test_the_endpoint_gauge_admits_every_room_it_has_before_it_refuses(
    client: AsyncClient, pool, monkeypatch, part_env, caplog
):
    """The bound counts live turns at one endpoint, so it admits every part it has room
    for and refuses only the turn that would cross it. At a limit of two with one foreign
    turn already live, exactly ONE further part is admitted; the next spends nothing and
    names the guard, and the same part runs the moment a member releases."""
    import logging

    from backend.config import settings
    from backend.tasks import agent_schedules

    caplog.set_level(logging.INFO, logger="backend.tasks.agent_schedules")
    monkeypatch.setattr(settings, "CURATOR_DRAIN_MAX_CONCURRENT_TURNS", 2)
    key, uid = await _register(client)
    curator = await _digest_curator(client, pool, uid)
    agent_id = UUID(curator["id"])
    cursor, (first, second) = _cut_cursor(OLD, 24, 2, max_open=4)
    await _seed_drain(pool, agent_id, drain_derived_through(cursor), cursor)
    # Both windows hold a row: an empty window earns its edge without a turn, which would
    # read as a refusal when it is only a window with nothing in it.
    await _push_events(client, key, [_turn("w1", "work one", OLD + timedelta(hours=1))])
    await _push_events(client, key, [_turn("w2", "work two", OLD + timedelta(hours=25))])

    identity = "harness:claude"
    key_gauge = f"drain-turns:{identity}"
    part_env.redis.zsets[key_gauge] = {"someone:else": 1e12}

    admitted_first, size_first = await agent_schedules._part_gauge_admit(identity, agent_id, first)
    admitted_second, size_second = await agent_schedules._part_gauge_admit(
        identity, agent_id, second
    )
    assert (admitted_first, size_first) == (True, 2), (
        "a gauge with one seat free refused a part it was there to admit"
    )
    assert (admitted_second, size_second) == (False, 2), (
        "the gauge admitted a second turn beside the one already live, crossing its limit"
    )
    assert set(part_env.redis.zsets[key_gauge]) == {"someone:else", f"{agent_id}:{first}"}

    # The part the gauge refused spends no turn, and says which guard refused it.
    part_env.digest_calls.clear()
    await agent_schedules._run_curator_part(agent_id, second, metered=False)
    assert part_env.digest_calls == [], "a declined part spent a model turn anyway"
    declines = [r.getMessage() for r in caplog.records if "declined" in r.getMessage()]
    assert any(
        "guard=endpoint_pressure" in line and "gauge=2 limit=2" in line for line in declines
    ), declines
    refused = await _stored(pool, agent_id)
    assert refused["drain_cursor"]["parts"][second]["state"] == "open", (
        "the refused part was left in a state its next dispatch cannot resume"
    )

    # Release the live member and the room is real: the same part now runs.
    await agent_schedules._part_gauge_release(identity, agent_id, first)
    await agent_schedules._run_curator_part(agent_id, second, metered=False)
    assert len(part_env.digest_calls) == 1, "the room freed by a release was never granted"
    ran = await _stored(pool, agent_id)
    assert ran["drain_cursor"]["parts"][second]["state"] == "done"
