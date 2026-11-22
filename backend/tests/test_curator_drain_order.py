"""A drain that works its feed newest-first, and the one column that proves it did.

The founder lane's defect was never the order its slots ran in. It was that one monotone
cursor read the whole feed with `ORDER BY stamp,kind,id LIMIT n`, so a document's turn sat
behind every document that predates it, and on that lane the queue measured 4,813,383 rows
(re-measured on the founder stack at planning time). Recency and backlog were fighting over
one cursor and the backlog was winning: material uploaded today was not eligible for
curation at all.

The shape here is a DESCENDING plan: the lane cuts the feed into windows downward from the
live seam and works the newest first, so a row that becomes eligible input falls inside the
head cell the next run cuts and is worked FIRST. Two equalities hold the whole design:

* the descent reads exactly the set of rows the incremental read would eventually read,
  partitioned into windows and visited in the opposite order. Nothing added, nothing
  removed. Every no-double-read and no-gap assertion below is one instance of that.
* `drain_through` never names ground that no committed pass read. That is why the descent
  carries its own `published` edge, why it is seeded at the watermark the descent starts
  BEHIND rather than at the seam, and why the column is fenced on itself at the write and
  not only on the slot it was derived from.

The pure tests here need no database. The dispatch tests at the bottom drive the real
scoped entry point, and they are the acceptance half of the bound: a fresh document read by
the FIRST committed pass with thousands of older ones present.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.config import settings
from backend.database import get_pool
from backend.services import agent_service, curation_service, scoped_curation_inputs
from backend.services import scoped_curation_service as curation
from backend.services.curation_service import (
    Position,
    cut_descent_cell,
    cut_head_cell,
    descending_plan,
    drain_derived_through,
    drain_descends,
    drain_of,
    drain_parts_of,
    drain_plan_top,
    drain_published,
    drain_seam_target,
    drain_slot_position,
    extend_descent_floor,
    newest_open_part,
    part_id_of,
    slot_instant,
)

FLOOR = datetime(2026, 8, 10, 13, 21, 54, 812999, tzinfo=UTC)
SEAM = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
PART_HOURS = 24
MAX_OPEN = 6


def _cursor(
    *,
    floor: datetime = FLOOR,
    seam: datetime = SEAM,
    published: datetime | None = None,
    part_hours: int = PART_HOURS,
    **plan_overrides,
) -> dict:
    """A descending plan cursor, built the way a seeder builds one.

    `floor`, `seam`, `published` and `part_hours` go THROUGH `descending_plan` rather than
    being pasted over its answer, because `descent_to` starts at the plan's own seam: a
    fixture that overrode the seam afterwards would leave the frontier on the seam of a
    different grid, and the descent it described could not be cut by any cutter.
    `plan_overrides` is for the deliberate surgeries that test a refusal."""
    plan = (
        descending_plan(
            part_hours=part_hours,
            floor=floor,
            seam=seam,
            published=FLOOR if published is None else published,
        )
        | plan_overrides
    )
    return {"plan": plan, "parts": {}}


def _patched(cursor: dict, **plan_overrides) -> dict:
    """A cursor whose plan was edited AFTER it was built, which is the shape of every
    refusal test here: a valid descent with one edge removed, or its kind word replaced.
    """
    plan = dict(cursor["plan"])
    for key, value in plan_overrides.items():
        plan.pop(key) if value is _ABSENT else plan.__setitem__(key, value)
    return {"plan": plan, "parts": dict(cursor["parts"])}


_ABSENT = object()


def _commit(cursor: dict, part_id: str, through: datetime) -> dict:
    """The pure shape of `advance_drain`'s planned arm: move the slot's edge and re-derive."""
    parts = cursor["parts"]
    cursor["parts"] = {
        **parts,
        part_id: {**parts[part_id], "through": through.isoformat(), "cursor": {}},
    }
    return cursor


def _prune(cursor: dict, part_id: str, published: datetime) -> dict:
    """The pure shape of `prune_published_part`: delete the slot and raise the scalar.

    The deleted slot is deliberately the one that may hold the maximum: that is the whole
    reason the plan carries `published` at all, and the reason the column is fenced on
    itself and not merely on the slot a pass read.
    """
    parts = dict(cursor["parts"])
    del parts[part_id]
    plan = {**cursor["plan"], "published": max(published, drain_published(cursor)).isoformat()}
    return {"plan": plan, "parts": parts}


# ---------------------------------------------------------------- the plan's own kind


def test_a_descending_plan_names_itself_and_the_forward_plan_stays_kindless():
    """The kind word is the only thing telling the two walks apart, so it is the only
    thing `drain_descends` reads — and the forward plan must answer False without having
    been edited at all, which is how a lane already draining stays on its own path."""
    forward = {
        "plan": {"part_hours": 24, "frontier": SEAM.isoformat(), "target": SEAM.isoformat()},
        "parts": {},
    }
    assert drain_descends(forward) is False
    assert drain_descends(_cursor()) is True


def test_a_mixed_cursor_raises_naming_the_agent():
    """One cursor cannot walk two ways: a plan carrying a forward target beside the kind
    word has no answer to 'which end is next', so it is refused rather than averaged."""
    mixed = _patched(_cursor(), target=(SEAM + timedelta(hours=24)).isoformat())
    with pytest.raises(ValueError, match="agent-under-test") as raised:
        drain_parts_of({"id": "agent-under-test", "drain_cursor": mixed})
    assert "two walking directions" in str(raised.value)


def test_a_descending_cursor_with_its_kind_word_stripped_raises():
    """The four edges left behind would be read as noise by the forward arm, and the
    published edge — the one number the alert and the hand-off consult — would silently
    stop being a fact anybody asked. Refuse the cursor instead."""
    kindless = _patched(_cursor(), order=_ABSENT)
    with pytest.raises(ValueError, match="agent-under-test") as raised:
        drain_parts_of({"id": "agent-under-test", "drain_cursor": kindless})
    assert "with no `order`" in str(raised.value)


@pytest.mark.parametrize("order", ["oldest_first", "newest", "random"])
def test_a_kind_word_that_is_not_the_one_legal_word_raises(order: str):
    order = _patched(_cursor(), order=order)
    with pytest.raises(ValueError, match="agent-under-test"):
        drain_parts_of({"id": "agent-under-test", "drain_cursor": order})


@pytest.mark.parametrize("edge", ["floor", "seam", "descent_to", "published"])
def test_a_descending_plan_missing_any_of_its_four_edges_raises(edge: str):
    """A descent that does not say how far down it goes cannot stop, and one that does not
    say what it published leaves its column to be guessed at by its readers."""
    broken = _patched(_cursor(), **{edge: _ABSENT})
    with pytest.raises(ValueError, match="agent-under-test") as raised:
        drain_parts_of({"id": "agent-under-test", "drain_cursor": broken})
    assert edge in str(raised.value)


def test_a_plan_with_no_kind_and_no_descending_edges_is_still_read_as_forward():
    """The forward plan is the older shape and carries neither a kind word nor a descent
    edge; adding the descent's refusals must not touch a cursor that never mentions them.
    """
    forward = {
        "plan": {
            "part_hours": 24,
            "frontier": SEAM.isoformat(),
            "target": (SEAM + timedelta(hours=24)).isoformat(),
        },
        "parts": {},
    }
    assert drain_parts_of({"id": "agent-under-test", "drain_cursor": forward}) == {}


# ------------------------------------------------------------- the tiling of a descent


def _descend(cursor: dict, cells: int, *, part_hours=PART_HOURS, max_open=99) -> tuple:
    """Cut `cells` descent windows and return (cursor, [(part_id, from, to)] oldest-first)."""
    for _ in range(cells):
        nxt = cut_descent_cell(cursor, part_hours=part_hours, max_open=max_open)
        assert nxt is not None, "the cutter stopped above its floor"
        cursor = nxt
    return cursor, sorted(cursor["parts"].items())


def drain_open_descent_count(cursor: dict) -> int:
    """The gate's own count, read the way `cut_descent_cell` reads it: the head cell is
    not one of the descent cells, because it closes above the seam."""
    seam = slot_instant(cursor["plan"], "seam")
    return sum(
        1
        for slot in cursor["parts"].values()
        if slot["state"] == "open" and slot_instant(slot, "to") <= seam
    )


def test_ten_descent_cells_tile_the_span_with_no_gap_and_no_overlap():
    """Adjacent cells share exactly one instant and the loader hands that instant to the
    older `to`, so ten cells whose edges touch cover `(floor, seam]` once each. A gap is a
    window nobody ever cut; an overlap is the same characters paid for twice."""
    _, cells = _descend(_cursor(), 10)
    assert len(cells) == 10
    assert slot_instant(cells[-1][1], "to") == SEAM, "the newest cell closes at the seam"
    for (older_id, older), (newer_id, newer) in zip(cells, cells[1:]):
        assert slot_instant(newer, "from") == slot_instant(older, "to"), "no gap"
        assert slot_instant(older, "to") - slot_instant(older, "from") == timedelta(
            hours=PART_HOURS
        )
        assert newer_id > older_id


def test_the_descent_lands_on_the_floor_by_arithmetic_and_not_by_a_clamp():
    """`seam` is `floor + k * H` because the grid is anchored on the floor, so walking
    down by H reaches the floor exactly. The last cell is `(floor, floor + H]` and never
    `[floor, ...)`: the loader reads strictly after a window's `from`, so a floor naming a
    real row would strand it, and a cell reading the floor's own instant would re-read
    ground the watermark already consumed."""
    seam = FLOOR + timedelta(hours=PART_HOURS * 10)
    cursor, cells = _descend(_cursor(seam=seam), 10)
    assert slot_instant(cells[0][1], "from") == FLOOR
    assert drain_published(cursor) is not None
    assert slot_instant(cursor["plan"], "descent_to") == FLOOR
    assert cut_descent_cell(cursor, part_hours=PART_HOURS, max_open=MAX_OPEN) is None, (
        "the descent must stop"
    )


def test_part_ids_sort_as_instants_across_a_dst_boundary_and_a_year_boundary():
    """A part id IS its `from` instant in `DRAIN_PART_ID_FORMAT`, so a string sort is an
    instant sort — the claim that makes the working order, the derivation order and what
    an operator greps in the logs one order and not three. Two dates that break naive
    arithmetic (a DST change and a year rollover) must not break it."""
    for anchor in (
        datetime(2026, 3, 6, 12, tzinfo=UTC),  # across the US spring-forward
        datetime(2026, 12, 31, 18, tzinfo=UTC),  # across the year boundary
    ):
        cursor, cells = _descend(_cursor(floor=anchor - timedelta(days=40), seam=anchor), 4)
        ids = [part_id for part_id, _ in cells]
        instants = [slot_instant(slot, "from") for _, slot in cells]
        assert ids == sorted(ids) and instants == sorted(instants)
        assert [part_id_of(i) for i in instants] == ids
        assert newest_open_part(cursor) == ids[-1]


def test_a_head_cell_is_cut_once_per_seam_and_its_edge_is_captured_once():
    """`drain_seam_target`'s own docstring records two coordinator passes 56 microseconds
    apart producing two plans over one seam. The head cell answers it by storing the `now`
    it opened with: the next call reads that stored top, finds a head cell standing, and
    opens nothing, so no run re-reads the clock for head geometry."""
    now = SEAM + timedelta(hours=90)
    first = cut_head_cell(_cursor(), part_hours=PART_HOURS, now=now)
    assert first is not None and newest_open_part(first) == part_id_of(SEAM)
    head = first["parts"][part_id_of(SEAM)]
    assert slot_instant(head, "from") == SEAM and slot_instant(head, "to") == now
    assert slot_instant(first["plan"], "head_top") == now
    for later in (now + timedelta(microseconds=56), now + timedelta(days=3)):
        assert cut_head_cell(first, part_hours=PART_HOURS, now=later) is None
    # The stored top survives the head cell's publication, so the NEXT head cell resumes
    # from it rather than from a clock reading a day later.
    published = _prune(first, part_id_of(SEAM), now)
    assert slot_instant(published["plan"], "head_top") == now


def test_a_head_cell_is_refused_below_the_descent_frontier_and_at_its_edge_is_legal():
    """The seed case: the first head cell's `from` is the seam while `descent_to` is still
    the seam too. Adjacency shares one instant and the loader gives that instant to the
    older `to`, so equality is the tiling law and refusing it would refuse the bound.
    BELOW the frontier would overlap ground the descent already promised, and a `now` at
    or under the stored top is a cell with no ground in it; both are refused."""
    seeded = _cursor()
    assert cut_head_cell(seeded, part_hours=PART_HOURS, now=SEAM) is None
    assert cut_head_cell(seeded, part_hours=PART_HOURS, now=SEAM - timedelta(hours=1)) is None
    opened = cut_head_cell(seeded, part_hours=PART_HOURS, now=SEAM + timedelta(hours=1))
    assert slot_instant(opened["parts"][part_id_of(SEAM)], "from") == SEAM, "adjacent, legal"
    descended = cut_descent_cell(opened, part_hours=PART_HOURS, max_open=MAX_OPEN)
    assert newest_open_part(descended) == part_id_of(SEAM), "the head cell stays the newest"
    surgery = _patched(_cursor(), descent_to=(SEAM + timedelta(hours=PART_HOURS)).isoformat())
    assert cut_head_cell(surgery, part_hours=PART_HOURS, now=SEAM + timedelta(hours=6)) is None, (
        "a head cell below the descent frontier would overlap cut ground"
    )


def test_max_open_bounds_the_descent_cells_and_never_the_head_cell():
    """The gate bounds the backlog a lane promises ahead of the work it can do. The head
    cell is exempt by construction: exactly one can stand, so there is no queue of them to
    bound, and refusing it while the gate is full would be refusing the bound itself."""
    full = _cursor()
    for _ in range(MAX_OPEN):
        full = cut_descent_cell(full, part_hours=PART_HOURS, max_open=MAX_OPEN)
        assert full is not None
    assert cut_descent_cell(full, part_hours=PART_HOURS, max_open=MAX_OPEN) is None
    head = cut_head_cell(full, part_hours=PART_HOURS, now=SEAM + timedelta(hours=3))
    assert head is not None, "a full descent gate must not stop the newest window opening"
    assert drain_open_descent_count(head) == MAX_OPEN


# ------------------------------------------- the column, which may only ever move up


def test_the_descending_column_is_never_null_while_the_plan_stands():
    """A NULL beside a live plan reads to `drain_of` as 'seeded at the beginning', and
    that reading is the corpus paid for twice. The forward plan tolerates a None (its
    empty map means 'everything composed, hand-off owed'); a descent may not answer it at
    all, because its own `published` scalar is the answer."""
    cursor = _cursor()
    assert drain_derived_through(cursor) == FLOOR
    assert drain_derived_through({"plan": cursor["plan"], "parts": {}}) == FLOOR
    cut = cut_head_cell(cursor, part_hours=PART_HOURS, now=SEAM + timedelta(hours=4))
    assert drain_derived_through(cut) == FLOOR, "a cut that published nothing moves nothing"


def test_the_seeded_published_edge_is_the_watermark_and_never_the_seam():
    """AC (j)'s seed case, stated as the invariant it is: the column never names ground
    that no committed pass read. `seam` stands at the TOP of a cell nobody has read, and
    the stale-curator alert reads this column, so a seam-shaped seed silences the alert
    with a number that describes nothing."""
    cursor = _cursor()
    assert drain_published(cursor) == FLOOR
    assert drain_published(cursor) < slot_instant(cursor["plan"], "seam")
    assert drain_derived_through(cursor) == FLOOR


def test_publishing_and_pruning_the_head_cell_leaves_the_column_where_it_stood():
    """The hole the operator's derivation found: the forward lane derives the `from` of the
    OLDEST slot, so pruning its own maximum moves the aggregate forward; a descent deletes
    the very slot holding the maximum, so an aggregate over slots alone would fall back to
    a value the lane had already left behind. The scalar is what makes the prune a no-op
    on the column, and `>=` its prior value is the assertion a per-slot fence cannot make.
    """
    cursor = _cursor()
    cursor = cut_head_cell(cursor, part_hours=PART_HOURS, now=SEAM + timedelta(hours=4))
    head = part_id_of(SEAM)
    edge = SEAM + timedelta(hours=4)
    cursor = _commit(cursor, head, edge)
    before = drain_derived_through(cursor)
    assert before == edge, "a pass that read to the cell's edge raises the column to it"
    after = _prune(cursor, head, edge)
    assert drain_derived_through(after) == before
    assert drain_derived_through(after) >= before
    assert after["parts"] == {} and drain_derived_through(after) is not None


def test_an_n_cell_descent_leaves_the_column_non_decreasing_across_every_commit():
    """Ten commits, nine prunes, one direction. The per-slot fence ("a drain commits where
    its pass stopped reading, never behind it") cannot see this class at all: a pass can
    satisfy it exactly and the column it writes can still sit lower, because the winner
    left the set."""
    cursor, cells = _descend(_cursor(), 10)
    seen = [drain_derived_through(cursor)]
    for part_id, slot in cells:  # oldest first: the descent's own reverse order
        cursor = _commit(cursor, part_id, slot_instant(slot, "to"))
        seen.append(drain_derived_through(cursor))
        cursor = _prune(cursor, part_id, slot_instant(slot, "to"))
        seen.append(drain_derived_through(cursor))
    assert seen == sorted(seen), f"the column moved backwards across a descent: {seen}"
    assert None not in seen
    assert seen[-1] == SEAM, "the last published edge is the top of the newest cell"


def test_working_order_is_newest_first_and_the_head_cell_goes_before_the_descent():
    """The fairness rule in both directions: a fresh row cannot starve behind the backlog
    because the head cell is the newest id and is worked first, and the backlog cannot
    starve behind fresh input because once the head cell is published the rest of the run
    descends. `at_or_below` is the half that keeps a second head cell from a run."""
    cursor = _cursor()
    cursor = cut_head_cell(cursor, part_hours=PART_HOURS, now=SEAM + timedelta(hours=4))
    for _ in range(3):
        cursor = cut_descent_cell(cursor, part_hours=PART_HOURS, max_open=MAX_OPEN)
    head = part_id_of(SEAM)
    assert newest_open_part(cursor) == head
    published = _prune(cursor, head, SEAM + timedelta(hours=4))
    assert newest_open_part(published) == part_id_of(SEAM - timedelta(hours=PART_HOURS))
    # A run that already worked its one head cell asks for the newest cell at or below the
    # plan top, so a head cell cut mid-run by another writer cannot take the next pass.
    assert newest_open_part(cursor, at_or_below=SEAM) == part_id_of(
        SEAM - timedelta(hours=PART_HOURS)
    )


def test_a_slot_position_names_the_pair_a_pass_fences_on_and_its_closed_edge():
    """The run threads a slot through the loader and the commit, so the pair it fences on
    and the window it must not read past come from ONE reader. A `done` slot has an author
    and owes no turn, so it answers None and no pass re-runs a published window."""
    cursor = cut_head_cell(_cursor(), part_hours=PART_HOURS, now=SEAM + timedelta(hours=2))
    head = part_id_of(SEAM)
    agent = {
        "id": "agent-under-test",
        "drain_cursor": cursor,
        "drain_through": drain_derived_through(cursor),
    }
    position, to_at = drain_slot_position(agent, head)
    assert position == Position(SEAM) and to_at == SEAM + timedelta(hours=2)
    assert drain_slot_position(agent, part_id_of(SEAM - timedelta(hours=1))) is None


def test_the_plan_top_is_the_seam_until_a_head_cell_captures_one():
    """The coverage probe and the pending gate end at the newest edge the standing plan
    owns, which is the head cell's captured top once one exists. Reading it off the clock
    instead would ask the probe about ground no window covers."""
    cursor = _cursor()
    assert drain_plan_top(cursor) == SEAM
    opened = cut_head_cell(cursor, part_hours=PART_HOURS, now=SEAM + timedelta(hours=8))
    assert drain_plan_top(opened) == SEAM + timedelta(hours=8)
    assert drain_plan_top({"plan": {"part_hours": 24}, "parts": {}}) is None


def test_a_descending_row_is_a_drain_to_every_reader_of_the_pair():
    """`drain_of` must name a descending plan as a live position, or the run would read
    the incremental watermark and a lane mid-descent would be called caught up."""
    cursor = cut_head_cell(_cursor(), part_hours=PART_HOURS, now=SEAM + timedelta(hours=1))
    row = {"drain_through": drain_derived_through(cursor), "drain_cursor": cursor}
    assert drain_of(row) == (FLOOR, cursor)
    assert curation_service.read_position_of(row) == (FLOOR, cursor)
    assert curation_service.readout_position_of(row) == Position(FLOOR)


def test_the_two_cutters_refuse_a_forward_plan_by_name_rather_than_by_silence():
    """A cutter for one direction quietly returning None on the other would read as 'no
    window may be cut' and park a forward drain forever. Both say which cutter was asked."""
    forward = {
        "plan": {
            "part_hours": 24,
            "frontier": SEAM.isoformat(),
            "target": (SEAM + timedelta(hours=24)).isoformat(),
        },
        "parts": {},
    }
    for cut in (
        lambda: cut_head_cell(forward, part_hours=PART_HOURS, now=SEAM),
        lambda: cut_descent_cell(forward, part_hours=PART_HOURS, max_open=MAX_OPEN),
    ):
        with pytest.raises(ValueError, match="does not descend"):
            cut()


def test_a_granularity_change_refuses_the_cut_rather_than_shifting_the_seams():
    """A plan's seams are set by its first cut, and `CURATOR_DRAIN_PART_HOURS` moving
    mid-drain must widen the NEXT plan, never re-seam this one — which is the refusal the
    forward cutter already makes and both descent cutters keep."""
    cursor = _cursor()
    for cut in (
        lambda: cut_head_cell(cursor, part_hours=48, now=SEAM + timedelta(hours=1)),
        lambda: cut_descent_cell(cursor, part_hours=48, max_open=MAX_OPEN),
    ):
        with pytest.raises(ValueError, match="must await a new plan"):
            cut()


def test_drain_seam_target_anchored_on_the_floor_lands_on_the_grid_it_promised():
    """The descent's stopping proof is arithmetic, and the arithmetic only closes if the
    grid is anchored on the floor: seam = floor + k*H, so descending by H reaches floor."""
    now = FLOOR + timedelta(hours=PART_HOURS * 7 + 137)
    seam = drain_seam_target(FLOOR, PART_HOURS, now)
    assert (seam - FLOOR) % timedelta(hours=PART_HOURS) == timedelta(0)
    assert seam <= now < seam + timedelta(hours=PART_HOURS)


# --- what a rewind does to a floor that is already frozen --------------------


def test_a_watermark_that_fell_under_the_floor_moves_the_floor_and_keeps_the_seams():
    """A descent's floor is a promise, and a rewind lengthens it rather than breaking it.

    The grid is anchored on the seam and not on the floor's current value, so lowering the
    floor must not move one already-promised seam: the cells above stay where they were cut and
    the walk simply continues past the old edge, its final cell running short to the new floor
    exactly as a seeded descent's final cell runs short to the seeded floor. That is what lets a
    plan survive a rewind without re-cutting ground it already promised.
    """
    cursor, cells = _descend(_cursor(seam=FLOOR + timedelta(days=4)), 3)
    frontier = slot_instant(cursor["plan"], "descent_to")
    promised = sorted(cursor["parts"])
    assert promised == sorted(part_id for part_id, _slot in cells)

    rewound = FLOOR - timedelta(hours=12)
    moved = extend_descent_floor(cursor, floor=rewound)

    assert slot_instant(moved["plan"], "floor") == rewound
    assert slot_instant(moved["plan"], "seam") == FLOOR + timedelta(days=4), (
        "a rewind moved the grid's frontier"
    )
    assert slot_instant(moved["plan"], "descent_to") == frontier, (
        "extending the floor re-cut the ground the descent had already promised"
    )
    assert sorted(moved["parts"]) == promised, "the cells above a moved floor changed identity"
    # The cutter's last cell runs short to the floor, so the walk reaches `rewound` on the nose
    # and stops there: one cell per 24 hours, rounded up for the seam that straddles the edge.
    steps = -(-(frontier - rewound) // timedelta(hours=PART_HOURS))
    walked, walked_cells = _descend(moved, steps)
    assert slot_instant(walked["plan"], "descent_to") == rewound
    assert min(slot_instant(slot, "from") for _pid, slot in walked_cells) == rewound, (
        "the walk past an extended floor never landed on it, which is a gap at the bottom"
    )


def test_a_floor_that_has_risen_is_not_an_extension_and_says_so_by_answering_none():
    """Pulling a descent's floor upward deletes a promise, so the cutter refuses it.

    A watermark that moved forward is the ordinary direction and needs nothing. A plan allowed to
    raise its own floor would stop short of ground it may already have committed to read, and the
    retirement gate would then call that shortfall finished.
    """
    cursor = _cursor()
    assert extend_descent_floor(cursor, floor=FLOOR) is None
    assert extend_descent_floor(cursor, floor=FLOOR + timedelta(days=1)) is None
    moved = extend_descent_floor(cursor, floor=FLOOR - timedelta(microseconds=1))
    assert slot_instant(moved["plan"], "floor") == FLOOR - timedelta(microseconds=1)
    assert cursor == _cursor(), "the refusal still touched the cursor"


def test_extending_a_forward_plan_refuses_by_naming_what_it_was_asked():
    """A forward plan has no floor to move, and silence here would be a no-op that lies.

    The forward arm's frontier is its `target` and it never freezes a lower edge, so this is not
    a question a forward plan can be asked. A caller handed `None` would read it as "already
    extended" and carry on descending a plan that cannot descend.
    """
    forward = {"plan": {"target": SEAM.isoformat(), "part_hours": PART_HOURS}, "parts": {}}
    with pytest.raises(ValueError, match="does not descend"):
        extend_descent_floor(forward, floor=FLOOR - timedelta(days=1))


# --- The door and the seed: `scoped_curation_service.plan_descent`, against the database.
#
# `plan_descent` is the one place that decides whether a lane reads downward, and every one
# of these tests reads the ROW back afterwards. The decision is worth nothing as a return
# value: what a lane does next is decided by the cursor its row carries, and a plan that
# lived only in a caller's memory is a plan no later dispatch honours.

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


async def _lane(pool, *, watermark: datetime | None) -> dict:
    """A curator row of its own, at a chosen incremental position and with no input yet."""
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id,name,display_name) VALUES ($1,$2,$2)",
        user_id,
        f"lane_{user_id.hex[:8]}@example.com",
    )
    agent = await agent_service.get_or_create_curator(user_id, wiki="internal")
    await pool.execute(
        "UPDATE agents SET curated_through=$2 WHERE id=$1", UUID(str(agent["id"])), watermark
    )
    return await agent_service.get_agent_by_id(UUID(str(agent["id"])))


async def _doc(pool, owner: UUID, *, at: datetime, name: str, body: str = "BODY BODY BODY") -> None:
    """One eligible input row at a chosen instant.

    A `files` row is the cheapest eligible input there is — one table, no session, no
    source — and the loader's UNION treats it identically to the others, so a door that
    answers correctly here answers correctly for history events and drive documents.
    """
    await pool.execute(
        "INSERT INTO files (owner_user_id,name,content_type,size_bytes,storage_key,"
        "uploaded_by,extracted_text,extraction_status,created_at) "
        "VALUES ($1,$2,'text/plain',3,$2,$1,$4,'done',$3)",
        owner,
        name,
        at,
        body,
    )


async def _stored(pool, agent: dict) -> dict:
    return await agent_service.get_agent_by_id(UUID(str(agent["id"])))


@pytest.mark.asyncio
async def test_a_lane_whose_pending_input_sits_inside_the_newest_window_plans_nothing(pool):
    """AC (g)'s first proof: a healthy lane is untouched by this card.

    Two hours of un-curated material on a lane whose watermark is two hours old is exactly
    the material today's incremental read already reaches, so no descent may be planned and
    no column may be written — a lane that got a plan here would be a lane whose read shape
    changed for no reason at all."""
    agent = await _lane(pool, watermark=NOW - timedelta(hours=2))
    await _doc(pool, UUID(str(agent["user_id"])), at=NOW - timedelta(hours=1), name="fresh")

    assert await curation.plan_descent(agent, now=NOW) is None, "a healthy lane was re-planned"
    row = await _stored(pool, agent)
    assert (row["drain_through"], row["drain_cursor"]) == (None, {}), (
        "a lane with no backlog was given a drain position"
    )


@pytest.mark.asyncio
async def test_a_stale_watermark_alone_is_not_a_backlog(pool):
    """The door asks for UN-CURATED old input, not merely old input.

    A lane that retired a month ago and has been idle since has every curated row on its
    corpus older than one window, so the question "is anything older than one window" is
    true forever on it and the door would re-arm monthly over ground the watermark already
    consumed. The lane below has a month-old watermark and one hour of genuinely unread
    material: it stays on the incremental read, byte for byte."""
    watermark = NOW - timedelta(days=30, hours=5)
    agent = await _lane(pool, watermark=watermark)
    await _doc(pool, UUID(str(agent["user_id"])), at=NOW - timedelta(hours=1), name="fresh")

    seam = curation_service.drain_seam_target(watermark, PART_HOURS, NOW)
    assert seam == NOW - timedelta(hours=5), (
        f"the grid this door asks about is not the one the test names: {seam}"
    )
    assert await curation.plan_descent(agent, now=NOW) is None, (
        "a retired lane was re-seeded over its own curated past"
    )
    assert (await _stored(pool, agent))["drain_cursor"] == {}


@pytest.mark.asyncio
async def test_a_backlog_seeds_a_plan_whose_published_edge_is_the_watermark_it_behind(
    pool,
):
    """AC (j)'s seed case, and the hole the operator derived.

    Seeding `published` at the seam was the first draft's bug: the seam is the top of a cell
    no pass has ever read, and this instant is what the drain column derives, what the
    stale-curator alert compares, and what retirement hands the watermark. A seam-shaped seed
    therefore silences `_alert_stale_curators` with a number describing nothing and would let
    retirement hand off un-read ground.

    So the assertion is on the stored row and it is a comparison: the column equals the
    watermark the descent starts behind, and the seam is strictly above it. The gap between
    the two IS the descent's promise, and a seed that filled it in advance would be a claim
    of work already done."""
    watermark = NOW - timedelta(days=40, hours=5)
    agent = await _lane(pool, watermark=watermark)
    owner = UUID(str(agent["user_id"]))
    await _doc(pool, owner, at=NOW - timedelta(days=20), name="old")
    await _doc(pool, owner, at=NOW - timedelta(hours=1), name="fresh")

    planned = await curation.plan_descent(agent, now=NOW)
    assert planned is not None, "a 40-day backlog was left on the incremental read"
    row = await _stored(pool, agent)
    cursor = row["drain_cursor"]
    assert curation_service.drain_descends(cursor), f"the seed is not a descent: {cursor}"
    plan = cursor["plan"]
    stored_published = slot_instant(plan, "published")
    assert stored_published == watermark, (
        f"plan.published is {stored_published} but the seed read curated_through "
        f"{watermark}: the published edge must name the position the descent starts behind"
    )
    assert row["drain_through"] == watermark, (
        f"drain_through stores {row['drain_through']}, not the {watermark} the lane really "
        "holds: the column would be reporting a publication that never happened"
    )
    assert slot_instant(plan, "seam") > stored_published, (
        "a seed whose seam equals its published edge describes a descent with nothing to do"
    )
    assert slot_instant(plan, "floor") == watermark
    assert slot_instant(plan, "descent_to") < slot_instant(plan, "seam"), (
        "a seed that cut no descent cell leaves the backlog un-promised"
    )


@pytest.mark.asyncio
async def test_a_seed_cuts_one_head_cell_above_the_seam_and_a_bounded_row_below_it(pool):
    """The seed's own geometry: the cell that carries the bound, plus a gate on the backlog.

    The head cell `(seam, now]` is what a document uploaded today falls into, so it must
    exist at the seed and must be the newest thing on the map — `newest_open_part` answering
    anything else would put the fresh row's turn behind a window from 19 days ago. The
    descent cells below it are bounded by `2 x CURATOR_DRAIN_MAX_CONCURRENT_TURNS`, and the
    head cell is exempt from that count by construction, so the open total is one MORE than
    the gate rather than one over it."""

    watermark = NOW - timedelta(days=40, hours=5)
    agent = await _lane(pool, watermark=watermark)
    await _doc(pool, UUID(str(agent["user_id"])), at=NOW - timedelta(days=20), name="old")

    planned = await curation.plan_descent(agent, now=NOW)
    assert planned is not None
    cursor = planned["drain_cursor"]
    seam = slot_instant(cursor["plan"], "seam")
    parts = cursor["parts"]
    head = [pid for pid, slot in parts.items() if slot_instant(slot, "from") >= seam]
    assert len(head) == 1, f"expected exactly one head cell above {seam}, got {head}"
    assert slot_instant(parts[head[0]], "to") == NOW, (
        "the head cell's top is not the run's read boundary, so rows arriving before the "
        "next run belong to no window"
    )
    assert slot_instant(parts[head[0]], "from") == seam, (
        "the head cell does not start at the seam, leaving a gap no cell covers"
    )
    assert newest_open_part(cursor) == head[0], (
        "the freshest window on the map is not the one a pass is handed next"
    )
    max_open = 2 * settings.CURATOR_DRAIN_MAX_CONCURRENT_TURNS
    assert len(parts) == max_open + 1, (
        f"{len(parts)} slots stand where the gate allows {max_open} descent cells plus one "
        "head cell"
    )
    assert slot_instant(cursor["plan"], "descent_to") == seam - timedelta(
        hours=PART_HOURS * max_open
    )


@pytest.mark.asyncio
async def test_a_lane_that_never_ran_descends_to_the_corpus_minimum_and_claims_nothing(pool):
    """A never-run lane has no watermark, so its floor is the corpus's oldest instant minus
    one microsecond — and its published edge is that SAME instant, not the seam.

    The minus-one-microsecond is not rounding: the loader reads strictly after a window's
    `from`, so a floor naming a real row leaves that row in no window at all. And the
    published edge taking the floor value claims nothing, because every eligible row sits
    above it. A never-run lane whose whole corpus is one hour old still plans nothing, since
    there is no ground older than one window to descend through.
    """
    agent = await _lane(pool, watermark=None)
    await _doc(pool, UUID(str(agent["user_id"])), at=NOW - timedelta(hours=2), name="fresh")

    assert await curation.plan_descent(agent, now=NOW) is None, (
        "a two-hour-old corpus on a never-run lane is not a backlog"
    )

    old_at = NOW - timedelta(days=10)
    await _doc(pool, UUID(str(agent["user_id"])), at=old_at, name="old")
    planned = await curation.plan_descent(agent, now=NOW)
    assert planned is not None, "a ten-day-old corpus on a never-run lane is not a backlog"
    plan = planned["drain_cursor"]["plan"]
    assert slot_instant(plan, "floor") == old_at - timedelta(microseconds=1), (
        "the floor of a lane with no watermark must sit one microsecond BELOW the oldest "
        "eligible row, which the loader reads strictly after its window's `from`"
    )
    assert slot_instant(plan, "published") == old_at - timedelta(microseconds=1)
    assert planned["drain_through"] == old_at - timedelta(microseconds=1)


# --- the run loop honours the plan it was seeded with -------------------------
#
# Everything above is law stated in the abstract. These four drive the real `run()` and are
# where the design is actually tested, because the descending lane's whole difficulty is
# ORDERED STATE ACROSS PASSES: which cell pass two reads depends on what pass one committed,
# and no pure function can show that a run re-reads its own row instead of trusting the
# cursor it loaded at dispatch.


async def _live_lane(pool, *, back: timedelta, offset: timedelta) -> tuple[dict, dict]:
    """A scoped lane with a workspace, its watermark `back` ago, on the internal wiki.

    The two offsets are what make the geometry nameable against a clock this function does
    not control. `run()` captures its own read boundary from the real clock, and the seam is
    the first grid point above `now - part_hours`, so a watermark congruent to `now - offset`
    puts the seam exactly `offset` below the run's boundary. A watermark of 40 days plus 12
    hours therefore opens a head cell a full 12 hours wide, and a document stamped an hour
    ago falls inside it rather than into a window from last month.

    The internal wiki is chosen so each pass fans out to exactly one scope: these tests count
    model turns, and a lane that fanned out to five scopes per pass would make that count
    about something other than the windows.
    """
    live = datetime.now(UTC)
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id,name,display_name) VALUES ($1,$2,$2)",
        user_id,
        f"live_{user_id.hex[:8]}@example.com",
    )
    wiki_id = await pool.fetchval(
        "INSERT INTO folders (owner_user_id,name,created_by,is_protected) "
        "VALUES ($1,'External Wiki',$1,true) RETURNING id",
        user_id,
    )
    workspace_id = await pool.fetchval(
        "INSERT INTO workspaces (name,domain,scope_user_id,created_by,external_wiki_folder_id) "
        "VALUES ('Descending',$2,$1,$1,$3) RETURNING id",
        user_id,
        f"desc-{user_id.hex[:8]}.example.com",
        wiki_id,
    )
    agent = await agent_service.get_or_create_curator(user_id, wiki="internal")
    await pool.execute(
        "UPDATE agents SET curated_through=$2 WHERE id=$1",
        UUID(str(agent["id"])),
        live - back - offset,
    )
    workspace = dict(await pool.fetchrow("SELECT * FROM workspaces WHERE id=$1", workspace_id))
    row = await agent_service.get_agent_by_id(UUID(str(agent["id"])))
    return row, workspace


# The loader as the module ships it, read once at import. A second `install` inside one test --
# the second dispatch of one descent -- has to delegate to THIS and never to the first dispatch's
# stub: a stub that chained to its predecessor filed the second run's read in both ledgers, and the
# test that compares two dispatches' read positions then convicted the descent of a duplicate read
# that only the harness had written.
_PRISTINE_LOAD_BATCH = scoped_curation_inputs.load_batch


class _RunStub:
    """A scoped run with its provider, its model turn, and its budget removed.

    The three seams are the ones these tests are not about — which endpoint answers, what one
    scope's turn writes, how many characters a pass is granted. What is kept under
    observation is the loader call: the pair each pass started from and the edge it was
    forbidden to read past is the entire read shape of the descent, and `turns` counts the
    model calls so a window with nothing in it that still spent a paid turn shows up as a
    number rather than as a comment.
    """

    def __init__(self) -> None:
        self.turns = 0
        self.reads: list[tuple[datetime, datetime, dict]] = []
        # The batch's own answer, kept beside the request: `through` is the boundary the pass
        # would commit and `keys` is the read set itself, which is what a partition is compared
        # against. A test that asserted only on requests could not tell a window that read one
        # row from a window that read none of them.
        self.throughs: list[datetime] = []
        self.keys: list[list[str]] = []
        # Which rows each batch actually carried, as their stamps: a boundary alone cannot tell
        # a batch that read the fresh row from one that stopped a window short of it.
        self.stamps: list[list[datetime]] = []

    def install(self, monkeypatch, *, budget: int = 200_000) -> "_RunStub":
        async def route(_owner, _agent=None):
            return curation.llm.anthropic_route()

        async def turn(_scope, _instructions, _route):
            self.turns += 1
            return "stubbed"

        real_load = _PRISTINE_LOAD_BATCH

        async def load(owner, since, until, **kwargs):
            batch = await real_load(owner, since, until, **kwargs)
            self.reads.append((since, until, dict(kwargs["cursor"] or {})))
            self.throughs.append(batch.through)
            self.keys.append([str(d["key"]) for d in batch.documents])
            self.stamps.append([d["stamp"] for d in batch.documents])
            return batch

        monkeypatch.setattr(curation, "require_route", route)
        monkeypatch.setattr(curation, "run_scope", turn)
        monkeypatch.setattr(curation, "input_budget", lambda wall, rate: budget)
        monkeypatch.setattr(scoped_curation_inputs, "load_batch", load)
        return self


def _position(since: datetime, cursor: dict) -> tuple[datetime, tuple]:
    """One read's position, as something a set can hold.

    The cursor is the half of the position that a document's middle carries, so leaving it out
    would call two passes over one document a duplicate read. Its values are flattened to text
    only to be hashable and comparable; nothing here interprets them.
    """
    return since, tuple(sorted((k, str(v)) for k, v in cursor.items()))


def _worked_windows(caplog) -> list[tuple[datetime, datetime]]:
    """Every window one run worked, newest first, read out of the descent log it wrote.

    Returned as `(from, to]` pairs, because that is the shape whose contiguity is the
    invariant: adjacent cells share one instant, and the shared instant is read by exactly
    one of them. Read back out of the log rather than out of a loader spy because an empty
    cell never asks the loader anything — it is published on the strength of the existence
    check alone — and a coverage proof has to count the cells nobody read too.
    """
    return [
        tuple(
            datetime.fromisoformat(part)
            for part in record.getMessage().split("window=")[1].split()[0].split("..")
        )
        for record in caplog.records
        if record.getMessage().startswith("curator descent ")
    ]


def _promised_cells(plan: dict) -> list[tuple[datetime, datetime]]:
    """Every cell the plan has CUT, newest first, as the `(from, to]` pairs a read sees.

    Derived from the plan's own stored edges rather than from the test's arithmetic, so the
    comparison below is between two readings of one promise and not between the code and a
    second copy of its formula. The walk stops at `descent_to` — the frontier of CUT ground —
    and not at the floor, because the gate keeps a descent from promising its whole backlog at
    once and cells that were never cut are not cells this run owed.
    """
    hours = plan["part_hours"]
    head_top, seam, frontier = (slot_instant(plan, k) for k in ("head_top", "seam", "descent_to"))
    edges = [head_top, seam]
    while edges[-1] > frontier:
        edges.append(max(edges[-1] - timedelta(hours=hours), frontier))
    return [(lower, upper) for upper, lower in zip(edges, edges[1:])]


@pytest.mark.asyncio
async def test_one_run_reads_the_head_cell_first_and_then_tiles_down_to_its_floor(
    pool, monkeypatch, caplog
):
    """AC (a) and (b) together, and the bound that makes them worth shipping.

    The equality is the acceptance proof: every window one run worked is EXACTLY a cell the
    plan cut, in the plan's own order, each one starting where the newer one ended. Four
    separate claims collapse into that one line. Head cell first is the fairness bound — the
    document uploaded today is in the first window the run works, not behind 4.7 million older
    ones. Contiguity is the no-gap, no-double-read invariant: one cell's `from` is the newer
    cell's `to`, so the shared instant belongs to exactly one read. Covering the whole cut row
    is the partition claim: the descent reads the rows the incremental read would eventually
    have read, no more and no fewer. And a run that never re-read its own row would work the
    head cell twice and skip the cell below it, which this equality refuses.

    Two documents exist and exactly two model turns were spent, which is the same run's cost
    claim: the windows that held nothing bought no turn, and the five-day-old row's window is
    one of the two that did.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    await _doc(pool, owner, at=datetime.now(UTC) - timedelta(days=5), name="old")
    await _doc(pool, owner, at=datetime.now(UTC) - timedelta(hours=1), name="fresh")
    stub = _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    plan = (await _stored(pool, agent))["drain_cursor"]["plan"]
    worked = _worked_windows(caplog)
    assert worked == _promised_cells(plan), (
        "the run's windows are not the plan's cells, in order:\n"
        + "\n".join(f"worked   {a} .. {b}" for a, b in worked)
        + "\n"
        + "\n".join(f"promised {a} .. {b}" for a, b in _promised_cells(plan))
    )
    seam, head_top = slot_instant(plan, "seam"), slot_instant(plan, "head_top")
    assert worked[0] == (seam, head_top), (
        "the first window the run worked was not the head cell, so fresh input waited behind "
        "the backlog exactly as it did before this card"
    )
    assert stub.turns == 2, (
        f"a run over two non-empty windows and {len(worked) - 2} empty ones spent "
        f"{stub.turns} model turn(s); an empty window costs nothing"
    )
    assert slot_instant(plan, "published") == head_top, (
        "the published edge is not the newest window the run actually finished"
    )
    assert (await _stored(pool, agent))["drain_cursor"]["parts"] == {}, (
        "a window this run published is still standing on the row"
    )
    # The last prune deleted the slot that held the maximum, and the column stood on the plan's
    # published edge alone. A NULL here is the corpus paid for twice: `drain_of` reads a NULL
    # beside a live cursor as "seeded at the beginning".
    assert (await _stored(pool, agent))["drain_through"] is not None, (
        "pruning the last open window left the drain column NULL over a standing plan"
    )


@pytest.mark.asyncio
async def test_a_pass_that_stops_mid_window_keeps_its_slot_and_the_next_pass_resumes_it(
    pool, monkeypatch
):
    """A window is a read boundary, not a pass budget.

    The wall cuts a run mid-window constantly — that is what a 24-hour cell of a 4.8-million
    row corpus means — and the pass that gets cut must be able to come back. So the pair the
    next pass reads from is the slot's COMMITTED pair and never the slot's `from`: a run that
    resumed at `from` would re-read, and re-pay for, every character in the window. Strict
    increase of the `since` values inside one window is that claim, and a slot that survives
    being read six times is the slot surviving its own partial passes.

    The proof sits on a backlog window rather than the head cell because of the fairness bound
    itself: a run works one head cell, and once it has worked it the run descends, so a head
    cell the wall cuts is resumed by the NEXT run — first again, because it is still the newest
    thing on the map. A descent cell is the window a single run is expected to finish.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    # Three fat documents inside one backlog window, so that window needs several passes at the
    # smallest slice this loader writes, and one fresh row above the seam, because the door
    # asks for un-curated input under the first uncovered cell and a lane whose only material
    # is fresh stays on the incremental read, byte for byte.
    for hours in (6, 5, 4):
        await _doc(
            pool,
            owner,
            at=datetime.now(UTC) - timedelta(days=5, hours=hours),
            name=f"old-{hours}",
            body="lesson " * 150,
        )
    await _doc(pool, owner, at=datetime.now(UTC) - timedelta(hours=1), name="fresh")
    stub = _RunStub().install(monkeypatch, budget=512)

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    plan = (await _stored(pool, agent))["drain_cursor"]["plan"]
    head_top, seam = slot_instant(plan, "head_top"), slot_instant(plan, "seam")
    windows = [(since, until) for since, until, _ in stub.reads]
    positions = [_position(since, cursor) for since, _until, cursor in stub.reads]
    assert windows[0] == (seam, head_top), f"the run's first read was not the head cell: {windows}"
    backlog_windows = [window for window in windows[1:] if window[0] < seam]
    assert len(backlog_windows) > 1, (
        "no backlog window was read twice, so this run never exercised the resume path it "
        "exists for: fatten the documents or shrink the budget"
    )
    assert len({until for _since, until in backlog_windows}) == 1, (
        f"the passes after the head cell did not all belong to one window: {backlog_windows}"
    )
    # The PAIR is the position, never the instant alone: a pass that stops inside a document
    # commits that document's stamp as its boundary and an offset inside it, so two passes over
    # one document share their `since` and differ only in their cursor. No window and position
    # asked twice in the whole run is the claim that nothing was handed to the model twice, and
    # a repeat is exactly what resuming at a slot's `from` instead of its committed pair causes.
    assert len({(w, p) for w, p in zip(windows, positions)}) == len(positions), (
        f"one position of one window was read twice: {list(zip(windows, positions))}"
    )
    # And inside a window the boundary never sits backwards, which is the half the global claim
    # cannot see: across windows a descent moves DOWNWARD by design, so a regression inside one
    # window would hide in that overall decrease.
    by_window: dict[datetime, list[tuple[datetime, tuple]]] = {}
    for (since, until), position in zip(windows, positions):
        by_window.setdefault(until, []).append(position)
    for until, group in by_window.items():
        assert [since for since, _cursor in group] == sorted(since for since, _cursor in group), (
            f"a pass read from behind where an earlier pass of {until} had already read: {group}"
        )
    assert (await _stored(pool, agent))["drain_cursor"]["parts"] == {}, (
        "the window this run resumed never finished, so its slot is still standing"
    )


@pytest.mark.asyncio
async def test_a_descending_run_finishes_nothing_and_moves_no_watermark(pool, monkeypatch):
    """The one bug this card must not ship, held shut by a spy on the hand-off.

    The loop's old exit read `the loader handed me no more input` as `this drain is over`, and
    for a descending lane those two sentences are 4.7 million rows apart: an exhausted WINDOW
    is one cell of many, and finishing the drain on it hands the incremental watermark the
    position of a descent that has read its newest window and nothing else. So the run that
    publishes its head cell and every empty window below it must still be descending when it
    leaves, and the watermark it never read past must be the one it started behind.

    The spy is on `_finish_drain` rather than on the columns because the hand-off is the act
    being forbidden; the untouched `curated_through` is its consequence."""
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    await _doc(pool, owner, at=datetime.now(UTC) - timedelta(days=5), name="old")
    await _doc(
        pool,
        owner,
        at=datetime.now(UTC) - timedelta(hours=1),
        name="fresh",
        body="lesson " * 150,
    )
    stub = _RunStub().install(monkeypatch)
    handed_off: list[str] = []

    async def spy(*args, **kwargs):
        handed_off.append("finish_drain")

    monkeypatch.setattr(curation, "_finish_drain", spy)
    before = (await _stored(pool, agent))["curated_through"]

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    row = await _stored(pool, agent)
    assert handed_off == [], "a descent whose windows are merely exhausted retired its drain"
    assert row["curated_through"] == before, (
        "the incremental watermark moved to a position no committed pass read upward through it"
    )
    assert drain_of(row) is not None, (
        "the descent's plan left the row while ground below its frontier is still un-read"
    )
    assert stub.reads[0][1] == slot_instant(row["drain_cursor"]["plan"], "head_top")


@pytest.mark.asyncio
async def test_a_run_never_works_two_head_cells(pool, monkeypatch):
    """One head cell per run, and the run cannot be argued into a second one.

    The bound is `at_or_below=seam` once the head cell has been worked: the head cell's `to`
    is a captured instant ABOVE the seam while every descent cell closes at or below it. Read
    the same row twice with the fairness bound un-applied and the head cell is the newest
    thing on the map again — which is precisely how a lane with busy users would spend every
    run above the seam and never once touch its backlog. So the proof is the absence: no two
    reads of this run close above the seam.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    await _doc(pool, owner, at=datetime.now(UTC) - timedelta(days=5), name="old")
    for minutes in (90, 60, 30):
        await _doc(
            pool,
            owner,
            at=datetime.now(UTC) - timedelta(minutes=minutes),
            name=f"fresh-{minutes}",
            body="lesson " * 150,
        )
    stub = _RunStub().install(monkeypatch, budget=512)

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    seam = slot_instant((await _stored(pool, agent))["drain_cursor"]["plan"], "seam")
    above = [read for read in stub.reads if read[1] > seam]
    assert len(above) >= 1 and len({read[1] for read in above}) == 1, (
        f"a run may work one head cell, whose closed edge is its captured top; it read "
        f"{sorted({read[1] for read in above})}"
    )


# ------------------------------------------ the descent's own end: cut, extend, or retire
#
# Step 4's law is about a lane caught in one of three states, and a state is a position in a
# run, not a function argument. `_standing_descent` therefore puts a lane INTO a state by
# cutting it there with the same cutters the seed uses, and every assertion below is made
# against the stored row, because `advance_watermark` CLAMPS and logs a behind proposal
# instead of raising: a hand-off that silently did nothing would look like a green test, which
# is exactly how this card's one forbidden bug ships.


async def _standing_descent(
    pool,
    agent: dict,
    *,
    live: datetime,
    floor: datetime,
    seam: datetime,
    descent_to: datetime,
    published: datetime,
    cells: int = 0,
) -> dict:
    """Put a lane at a caller-chosen point of its own descent, cut by the cutters themselves.

    `live` is written as the row's `curated_through`, which is the one number this section
    moves out from under a standing descent, and the drain pair is written as
    `drain_derived_through` of the cursor the cutters produced, because a hand-assembled pair
    would validate against nothing and the tests below would be reading their own fiction.
    `cells` is how many descent cells are cut below `descent_to`'s starting edge, so a test can
    say "this plan promised six windows and worked none" as a number.
    """
    plan = descending_plan(part_hours=PART_HOURS, floor=floor, seam=seam, published=published)
    # `descending_plan` seeds its frontier at the seam, which is where a descent is BORN. This
    # helper is about a descent already walking, so the frontier is the state the caller named.
    plan["descent_to"] = descent_to.isoformat()
    cursor: dict = {"plan": plan, "parts": {}}
    for _ in range(cells):
        cursor = cut_descent_cell(cursor, part_hours=PART_HOURS, max_open=99)
    await pool.execute(
        "UPDATE agents SET curated_through=$2, drain_through=$3, drain_cursor=$4::jsonb WHERE id=$1",
        UUID(str(agent["id"])),
        live,
        drain_derived_through(cursor),
        cursor,
    )
    return await agent_service.get_agent_by_id(UUID(str(agent["id"])))


def _lines(caplog, needle: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


@pytest.mark.asyncio
async def test_a_frontier_that_still_names_un_read_ground_refuses_the_hand_off(
    pool, monkeypatch, caplog
):
    """A descent that has merely run out of CUT windows has not run out of GROUND.

    The frontier is the lower edge of everything the plan has cut, and while it stands above
    the floor there are cells below it that no pass has read and no position describes. The
    loop's old exit reached this state as soon as its last cut window was published and read it
    as a finished drain — which is the hand-off of 4.7 million rows of un-read corpus, and here
    it is the whole subject: the frontier sits six cells above the floor, twelve days of
    eligible input sit below the frontier, and the lane must leave all three where they are.
    """
    now = datetime.now(UTC)
    watermark = now - timedelta(days=80)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    seam = now - timedelta(days=28)
    frontier = seam - timedelta(days=6)
    agent = await _standing_descent(
        pool,
        agent,
        live=watermark,
        floor=watermark,
        seam=seam,
        descent_to=frontier,
        published=watermark,
    )
    # Twelve days below the frontier is ground the plan owes and one run's gate cannot reach,
    # so the row is neither read nor promised here: the frontier stays above the floor, the row
    # stays un-read, and the hand-off has no business happening.
    await _doc(pool, owner, at=frontier - timedelta(days=12), name="below-the-frontier")
    stub = _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")

    outcome = await curation.run(
        agent, workspace, "20261120120000", curation_service.position_of(agent)
    )

    row = await _stored(pool, agent)
    assert "holds" in outcome, f"a frontier above its floor retired anyway: {outcome}"
    assert row["curated_through"] == watermark, (
        "the watermark moved to a position above ground the descent had cut and never read"
    )
    assert row["drain_through"] is not None and drain_of(row) is not None, (
        "the descent's pair left the row while six cells below its frontier were un-read"
    )
    assert stub.reads == [], f"a lane with no open window read anyway: {stub.reads}"


@pytest.mark.asyncio
async def test_a_watermark_that_fell_under_the_descent_extends_it_rather_than_retiring(
    pool, monkeypatch, caplog
):
    """The rewind case, which is the floor conjunct's entire reason for existing.

    `memory_service._rewind_curated_through` can land the live watermark under a standing
    descent — a file re-ingested re-opens rows the descent believed were curated by contract.
    The descent had already reached its floor, so on the frontier conjunct alone it would
    retire, and the hand-off would place the watermark above the re-opened ground: the same
    silent loss, arrived at by a different door. So the floor moves down to the watermark, the
    cells that promise opens are cut, one line names both instants, and the watermark is left
    exactly where the rewind put it.
    """
    now = datetime.now(UTC)
    floor = now - timedelta(days=40)
    rewound = now - timedelta(days=80)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    seam = floor + timedelta(days=12)
    agent = await _standing_descent(
        pool,
        agent,
        live=rewound,
        floor=floor,
        seam=seam,
        descent_to=floor,
        published=seam,
    )
    stub = _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")

    outcome = await curation.run(
        agent, workspace, "20261120120000", curation_service.position_of(agent)
    )

    row = await _stored(pool, agent)
    plan = row["drain_cursor"]["plan"]
    assert slot_instant(plan, "floor") == rewound, (
        "the descent kept a floor above the watermark that had fallen under it, so the "
        "re-opened ground stays below any edge it can ever reach"
    )
    assert slot_instant(plan, "descent_to") > rewound, (
        "the descent claims to have extended and its frontier is already at the new floor"
    )
    assert row["curated_through"] == rewound, (
        "the hand-off jumped the watermark over the ground the rewind re-opened"
    )
    assert "holds" in outcome, f"a descent below its own new floor retired: {outcome}"
    extended = _lines(caplog, "extends its descent")
    assert len(extended) == 1, f"the extension printed {len(extended)} lines, expected one"
    assert floor.isoformat() in extended[0] and rewound.isoformat() in extended[0], (
        f"the extension line names neither of the two instants it is extending between: {extended[0]}"
    )
    assert stub.turns == 0, "the cells this extension cut spent a paid model turn"


@pytest.mark.asyncio
async def test_a_descent_that_reached_its_floor_hands_the_watermark_the_published_edge(
    pool, monkeypatch, caplog
):
    """Retirement, asserted as a VALUE.

    The frontier is at the floor, no window stands, and the loader itself answers nothing
    between the floor and the plan's top: the three facts are all true, so `finish_drain` runs
    as it runs today and the watermark becomes `plan.published`. The assertion is the stored
    instant and not the absence of an exception, because `advance_watermark` answers a behind
    proposal by clamping it and logging: a hand-off that quietly wrote nothing would leave this
    test green if it asked only whether the call raised. The pair is gone in the same
    transaction, which is the other half — a retired drain whose pair survives re-delivers the
    ground it just handed over. The instant itself is read back out of retirement's own line and
    compared against the row, because a descent whose head cell is still ahead of its seam opens
    one more cell at the run's boundary before it can finish, and that cell's published edge is
    then the hand-off — which is the law working, not a test that drifted.
    """
    now = datetime.now(UTC)
    watermark = now - timedelta(days=40)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    seam = watermark + timedelta(days=12)
    agent = await _standing_descent(
        pool,
        agent,
        live=watermark,
        floor=watermark,
        seam=seam,
        descent_to=watermark,
        published=seam,
    )
    stub = _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")

    outcome = await curation.run(
        agent, workspace, "20261120120000", curation_service.position_of(agent)
    )

    row = await _stored(pool, agent)
    assert row["drain_through"] is None and drain_of(row) is None, (
        "the watermark moved and the pair that carried the descent stayed behind to re-deliver it"
    )
    assert "retired" in outcome, f"a completed descent returned no retirement line: {outcome}"
    retired = _lines(caplog, "retired its descent")
    assert len(retired) == 1, "retirement printed no single line"
    handed_off = datetime.fromisoformat(retired[0].split("published edge ")[1].split()[0])
    assert row["curated_through"] == handed_off, (
        "retirement named one published edge and the row carries another: stored "
        f"{row['curated_through']}, handed off {handed_off}"
    )
    assert handed_off > watermark, (
        "the hand-off did not move the watermark forward off the ground the descent covered"
    )
    assert stub.reads == [], "a descent with no open window asked the loader for a read"


@pytest.mark.asyncio
async def test_a_descent_that_published_real_rows_across_its_whole_band_still_retires(
    pool, monkeypatch, caplog
):
    """Retirement is judged on the ground NO cell covers, never on the ground the plan read.

    Reading a row does not make it ineligible: `has_input` is `load_batch`'s own UNION and keeps
    no record of what a committed pass published. A coverage probe aimed at the plan's whole span
    `(floor, plan top]` therefore aims at exactly the band a descent exists to read and prune, and
    it answers true for the rest of the plan's life the moment any window is published —
    `finish_drain` is then never reached, `curated_through` stays at the pre-descent instant while
    the lane keeps working, and every reader of that column reports a curator that stopped months
    ago. The existing retirement test above seeded no rows into that band, which is the only state
    where the old probe answers false, so CI could not see it.

    This is the same law driven over REAL rows: three documents sit inside the band, one run cuts
    every cell of a five-cell descent, works the head cell first, tiles down to the floor, and
    retires — and the whole-span probe is re-asked afterwards and still answers true. The hand-off
    happened anyway, which is the fix.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=5), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    watermark = agent["curated_through"]
    # Three rows, one at each end of the descent and one in the middle: fresh material above the
    # seam, mid-band material, and material in the floor cell itself. `back` and `offset` are set
    # so the head cell is 12 hours wide and the span below the seam is five whole cells — small
    # enough for one run to cut, work, and retire over, and real enough that the band between the
    # floor and the plan top holds published ground at the moment retirement is decided.
    fresh = datetime.now(UTC) - timedelta(hours=1)
    middle = datetime.now(UTC) - timedelta(days=3)
    deepest = watermark + timedelta(hours=6)
    await _doc(pool, owner, at=fresh, name="fresh")
    await _doc(pool, owner, at=middle, name="mid-band")
    await _doc(pool, owner, at=deepest, name="floor-cell")
    stub = _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")

    outcome = await curation.run(
        agent, workspace, "20261120120000", curation_service.position_of(agent)
    )

    row = await _stored(pool, agent)
    retired = _lines(caplog, "retired its descent")
    assert len(retired) == 1, (
        f"a descent that worked its whole band of real rows never printed its hand-off "
        f"(run returned: {outcome})"
    )
    assert "retired" in outcome, f"the run did not report its own retirement: {outcome}"
    handed_off = datetime.fromisoformat(retired[0].split("published edge ")[1].split()[0])
    covered = _worked_windows(caplog)
    assert len(covered) == 6, f"the run worked {len(covered)} windows, not head plus five cells"
    assert covered[0][0] >= watermark and covered[-1][0] == watermark, (
        f"the cells worked do not run from the head down to the floor: {covered}"
    )
    assert row["curated_through"] == handed_off, (
        "retirement named one published edge and the row carries another: stored "
        f"{row['curated_through']}, handed off {handed_off}"
    )
    assert row["curated_through"] > watermark, (
        "the hand-off left the watermark on the pre-descent instant it started behind"
    )
    assert drain_of(row) is None, "the pair survived the hand-off and will re-deliver the descent"
    assert {s for stamps in stub.stamps for s in stamps} >= {fresh, middle, deepest}, (
        "this run retired over a band it never read, so it is not the state the defect needs"
    )
    assert stub.turns == 3, f"three non-empty windows spent {stub.turns} model turns"
    # The rows the descent read are still eligible input — publishing them to the wiki does not
    # delete them — and the probe aimed at the plan's WHOLE span still answers true afterwards.
    # That is the assertion that keeps this test honest: the gate retires because it asks the
    # ground no cell covers, not because the corpus finally ran out of rows.
    floor = datetime.fromisoformat(retired[0].rsplit("floor it covered was ", 1)[1].strip())
    still_there = await scoped_curation_inputs.has_input(
        owner,
        internal=row["curator_wiki"] == "internal",
        after=floor,
        top=covered[0][1],
    )
    assert still_there is True, (
        "this test proves nothing unless eligible input stands inside the published band, "
        "because that is the state the old conjunct could never get past"
    )


@pytest.mark.asyncio
async def test_a_lane_re_delivered_after_retirement_reads_forward_not_down(
    pool, monkeypatch, caplog
):
    """Retirement ends a descent, and the next dispatch must not quietly start it again.

    The door in `plan_descent` asks whether un-curated input stands BELOW the first cell a
    descent would cut. Retirement moved the watermark onto the published edge, so a lane that
    has just finished its descent and then receives one fresh document has nothing under that
    seam: it goes back to the incremental read it used before it ever descended — the pair stays
    gone, the read starts at the instant the descent handed over, and no plan reaches the row.
    A second descent over ground the first one published would pay for a curated corpus twice,
    which is the defect the published seed exists to prevent, arriving one dispatch later.
    """
    now = datetime.now(UTC)
    watermark = now - timedelta(days=40)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    seam = watermark + timedelta(days=12)
    agent = await _standing_descent(
        pool,
        agent,
        live=watermark,
        floor=watermark,
        seam=seam,
        descent_to=watermark,
        published=seam,
    )
    _RunStub().install(monkeypatch)
    caplog.set_level(logging.INFO, logger="backend.services.scoped_curation_service")
    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    retired = (await _stored(pool, agent))["curated_through"]
    assert drain_of(await _stored(pool, agent)) is None, "the first run left a pair standing"

    # Input that becomes eligible only after the descent is over: the fresh document is the
    # whole reason the bound exists, and here it is the test that the lane still reads for it.
    await _doc(pool, owner, at=datetime.now(UTC), name="after-the-retirement")
    stub = _RunStub().install(monkeypatch)
    row = await _stored(pool, agent)
    await curation.run(row, workspace, "20261120120100", curation_service.position_of(row))

    after = await _stored(pool, agent)
    assert drain_of(after) is None, (
        "a lane that had just retired its descent descended again, over ground it had published"
    )
    assert stub.reads, "the re-delivered lane never read the fresh document it was waiting for"
    since, until, _cursor = stub.reads[0]
    assert since == retired, (
        f"the re-delivered lane read from {since} instead of the {retired} its descent handed over"
    )
    assert until > since, f"an incremental read that walks downward is a descent: {stub.reads}"


@pytest.mark.asyncio
async def test_a_descending_lane_is_pending_on_ground_below_its_published_edge(pool, monkeypatch):
    """The pending gate reads the descent's whole span, because the loader cannot see it.

    `load_batch` walks ASCENDING from the pair it is handed, and the pair a descending lane
    hands it is its published edge — a boundary sitting a whole cell below the seam by design.
    Ask that probe whether the lane has work and it steps forward from the published edge toward
    `now`, never once looking at the backlog the descent was seeded to walk, and answers "no
    changes": the beat stops dispatching a lane that is in the middle of its most expensive
    month. The answer is asked cheapest first — a standing window is owed work by itself, and the
    bounded EXISTS over the loader's UNION answers for the ground no window covers — and this test
    is the two answers side by side: the ascending probe says nothing is owed, `has_pending` says
    the row inside the open cell is.
    """
    from backend.services.scoped_curation_inputs import MINIMUM_BATCH_CHARS

    now = datetime.now(UTC)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    floor = now - timedelta(days=40)
    seam = now - timedelta(days=12)
    agent = await _standing_descent(
        pool,
        agent,
        live=floor,
        floor=floor,
        seam=seam,
        descent_to=seam - timedelta(hours=PART_HOURS),
        published=seam,
        cells=1,
    )
    # One row inside the open cell, and therefore strictly below the published edge every reader
    # of this lane's drain column sees.
    await _doc(pool, owner, at=seam - timedelta(hours=12), name="inside-the-open-cell")
    row = await _stored(pool, agent)

    assert await scoped_curation_inputs.has_pending(row, workspace) is True, (
        "a descending lane with a whole open cell of un-read input reported no changes"
    )
    published = drain_published(row["drain_cursor"])
    ascending = await scoped_curation_inputs.load_batch(
        owner,
        published,
        now,
        internal=row["curator_wiki"] == "internal",
        generation=workspace["curation_generation"],
        cursor={},
        max_chars=MINIMUM_BATCH_CHARS,
        max_items=1,
    )
    assert not ascending.documents, (
        "the ascending probe saw the backlog after all, so this test is proving nothing about "
        "the shape it exists to pin"
    )


@pytest.mark.asyncio
async def test_a_fully_published_descending_lane_owes_nothing_and_a_fresh_row_owes_one(pool):
    """Pending on a descent means OWED ground, not existing input.

    The same confusion that made retirement unreachable reaches the beat's cost gate one layer up,
    and its cost is paid in credits: a descending lane whose probe asks the plan's whole span is
    pending forever, because reading a row never makes it ineligible. `_dispatch_due_agent` then
    never takes its free `no_changes` skip, every due tick spends a metered run to read nothing,
    and `FREE_CURATOR_RUNS_PER_MONTH` is gone while the backlog still stands. So the gate asks
    exactly the three bands a future pass would still work, and this test walks them.

    A fully-published descent — frontier on the floor, no slot standing, published ground holding
    real rows — owes nothing. Pull the frontier back up the span and the cells below it are owed.
    Put one row above the plan's top and the head cell that will claim it is owed, which is the
    bound: a descending lane that slept there would sleep through the only reason it exists.
    """
    now = datetime.now(UTC)
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    floor = now - timedelta(days=40)
    seam = now - timedelta(days=3)
    await _doc(pool, owner, at=floor + timedelta(hours=6), name="inside-the-published-band")
    await _doc(pool, owner, at=seam - timedelta(hours=6), name="just-below-the-seam")
    published = await _standing_descent(
        pool,
        agent,
        live=floor,
        floor=floor,
        seam=seam,
        descent_to=floor,
        published=seam,
    )

    assert await scoped_curation_inputs.has_pending(published, workspace) is False, (
        "a descent that published every cell it cut is still asked to spend a metered run on "
        "rows it has already read, which is the allowance the founder lane cannot afford to lose"
    )

    un_cut = await _standing_descent(
        pool,
        agent,
        live=floor,
        floor=floor,
        seam=seam,
        descent_to=floor + timedelta(days=2),
        published=seam,
    )
    assert (
        await scoped_curation_inputs.has_pending(await _stored(pool, un_cut), workspace) is True
    ), (
        "ground below the frontier is cells no cutter has opened, and a lane that sleeps on its "
        "own un-cut backlog never drains it"
    )

    await _doc(pool, owner, at=datetime.now(UTC), name="above-the-plan-top")
    back_at_the_floor = await _standing_descent(
        pool,
        agent,
        live=floor,
        floor=floor,
        seam=seam,
        descent_to=floor,
        published=seam,
    )
    assert (
        await scoped_curation_inputs.has_pending(await _stored(pool, back_at_the_floor), workspace)
        is True
    ), (
        "a descending lane reported no changes with a fresh document above its plan top: the "
        "head cell that would claim it is cut by the next run, and there will be no next run"
    )


@pytest.mark.asyncio
async def test_the_stale_curator_watchdog_reports_the_edge_that_selected_it(
    pool, monkeypatch, caplog
):
    """The watchdog may only format the instant its own predicate compared.

    `GREATEST(curated_through, drain_through)` selects on the further of a lane's two positions,
    and a lane can hold one without the other as a matter of design: `end_user_service`'s
    archive-and-reset zeroes the external curator's watermark without touching its drain pair, and
    a descent seeded behind a NULL watermark takes its floor from the corpus minimum and never
    writes that watermark while it works. A report line built from `curated_through` alone formats
    `None` with a datetime spec, which raises inside the watchdog and reports NO curator at all —
    including the ones the watchdog exists for — every tick the beat runs it.
    """
    from backend.tasks import agent_schedules

    now = datetime.now(UTC)
    floor = now - timedelta(days=40)
    edge = now - timedelta(hours=72)
    agent, _workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    agent = await _standing_descent(
        pool,
        agent,
        live=floor,
        floor=floor,
        seam=now - timedelta(days=3),
        descent_to=floor + timedelta(days=2),
        published=edge,
    )
    await pool.execute(
        "UPDATE agents SET curated_through=NULL, last_run_outcome='failed' WHERE id=$1",
        UUID(str(agent["id"])),
    )
    # A synthetic lane has no email, and the report line leads with it; give it one so the
    # assertion below can distinguish "the date formatted" from "None printed somewhere".
    await pool.execute(
        "UPDATE users SET email=$2 WHERE id=$1",
        UUID(str(agent["user_id"])),
        f"descent_{agent['id']}@example.com",
    )
    # Something owed, or a lane with no work is correctly silent. The row sits in the descent's
    # un-cut ground, below its frontier, which is a band both the old whole-span probe and the
    # present owed-ground probe answer true for — so this test reaches the report line rather than
    # stopping at the cost gate, and what it finds there is the formatting bug.
    await _doc(pool, UUID(str(agent["user_id"])), at=floor + timedelta(hours=6), name="owed")
    # Declare the alert mode rather than read the ambient one: the line is built before delivery
    # is even asked about, so this watches the report and not the transport.
    monkeypatch.setattr(settings, "ALERT_SLACK_TEAM_ID", None)
    monkeypatch.setattr(settings, "ALERT_SLACK_CHANNEL_ID", None)
    caplog.set_level(logging.ERROR, logger="backend.tasks.agent_schedules")

    assert await agent_schedules._alert_stale_curators() == 1, (
        "a descending lane behind the cutoff on its only live position was not selected"
    )
    reports = [r.getMessage() for r in caplog.records if "stale curator watchdog" in r.getMessage()]
    assert len(reports) == 1, f"the watchdog printed {len(reports)} reports, expected one"
    assert edge.strftime("%Y-%m-%d %H:%M") in reports[0], (
        "the report does not name the edge that selected the lane: " + reports[0]
    )
    assert "None" not in reports[0], reports[0]


async def _bulk_docs(pool, owner: UUID, count: int, *, at: datetime, chars: int = 700) -> None:
    """`count` eligible rows sharing one instant, as one statement.

    The backlog's DEPTH is what this file's acceptance test must be able to state without
    paying for it in fixtures, and one row per statement would make the test slow for a reason
    that has nothing to do with what it proves. Each row carries `chars` of text so a batch
    sized by a run's budget fills up long before the newest row is reached, which is the shape
    that makes the old watermark read start at the wrong end.
    """
    await pool.executemany(
        "INSERT INTO files (owner_user_id,name,content_type,size_bytes,storage_key,"
        "uploaded_by,extracted_text,extraction_status,created_at) "
        "VALUES ($1,'backlog-'||$2,'text/plain',$3,'k-'||$2,$1,$4,'done',$5)",
        [  # the name and key are built in SQL from $2, which makes that argument text
            (owner, str(n), chars, "lesson " * (chars // 7), at) for n in range(count)
        ],
    )


BACKLOG = 2000


@pytest.mark.asyncio
async def test_the_first_committed_pass_reads_the_fresh_document_with_a_backlog_behind_it(
    pool, monkeypatch
):
    """AC (b): the bound, with a backlog behind it that is a constant and not an excuse.

    Two thousand older eligible rows share one afternoon and one fresh row sits an hour old.
    The claim is about the FIRST committed pass of the run, not about some later pass that
    eventually gets around to it: the batch's own `through` is the boundary that pass would
    checkpoint, and it must be the fresh row's stamp. Behind today's watermark read the same
    lane answers the opposite way, and this test carries its own counterfactual by asking that
    read explicitly at the run's own budget: ascending from a watermark behind four years of
    corpus, the batch fills with the oldest material and never reaches the row uploaded today.

    Backlog depth is deliberately not load-bearing on the assertion. Twenty thousand rows would
    make the same first pass read the same fresh row, which is the whole point of a window
    instead of a cursor.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    now = datetime.now(UTC)
    fresh_at = now - timedelta(hours=1)
    await _bulk_docs(pool, owner, BACKLOG, at=now - timedelta(days=5))
    await _doc(pool, owner, at=fresh_at, name="the-fresh-one", body="MARKER lesson " * 40)
    stub = _RunStub().install(monkeypatch, budget=60_000)
    watermark = (await _stored(pool, agent))["curated_through"]

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    assert stub.reads, "the run read nothing at all"
    seam = slot_instant((await _stored(pool, agent))["drain_cursor"]["plan"], "seam")
    assert stub.reads[0][0] == seam, (
        f"the run's first read started at {stub.reads[0][0]}, below the seam {seam}, so the "
        "fresh row waited behind the backlog exactly as it did before this card"
    )
    assert stub.throughs[0] == slot_instant(
        (await _stored(pool, agent))["drain_cursor"]["plan"], "head_top"
    ), (
        "the first committed pass did not commit the whole head cell it read: it stopped at "
        f"{stub.throughs[0]}"
    )
    assert fresh_at in stub.stamps[0], (
        "the fresh document was not in the first pass's batch, whatever its boundary says"
    )
    # The counterfactual, asked of the loader at the run's own budget: what the watermark read
    # returned before this card, over the same rows.
    old_read = await scoped_curation_inputs.load_batch(
        owner,
        watermark,
        now,
        internal=agent["curator_wiki"] == "internal",
        generation=workspace["curation_generation"],
        cursor={},
        max_chars=60_000,
        max_items=MAX_OPEN,
    )
    assert old_read.documents and old_read.through < fresh_at, (
        "the watermark read reached the fresh row on its first batch, so this fixture has no "
        "backlog to hide behind and the assertion above is proving nothing"
    )
    assert fresh_at not in [d["stamp"] for d in old_read.documents], (
        "today's watermark read carried the fresh row in its first batch after all"
    )


@pytest.mark.asyncio
async def test_the_descent_reads_exactly_the_rows_the_incremental_read_would_have_read(
    pool, monkeypatch
):
    """AC (d): the partition is a re-ordering of the same read, never a new read or a gap.

    The set of rows this descent reads is compared against the set of eligible rows above the
    lane's floor, which is precisely what the incremental read would have walked over the weeks
    that follow. Equality in both directions is the claim: a row missing from the descent is a
    row nobody curates, and a row the descent invented is ground outside this lane's contract.
    Reading one row twice is the same defect on the spend line, so the flat list is compared
    against its own length and the loader's own per-row keys alongside it.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=6), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    floor = (await _stored(pool, agent))["curated_through"]
    for hours in (1, 3, 7, 20, 30, 47, 60, 75, 96, 110, 130):
        await _doc(pool, owner, at=datetime.now(UTC) - timedelta(hours=hours), name=f"doc-{hours}")
    stub = _RunStub().install(monkeypatch)

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    read = [stamp for batch in stub.stamps for stamp in batch]
    expected = {
        r["created_at"]
        for r in await pool.fetch(
            "SELECT created_at FROM files WHERE owner_user_id=$1 AND created_at > $2", owner, floor
        )
    }
    # Identity is the row's own stamp rather than the loader's key, because the key carries a
    # content hash this test would otherwise have to re-derive. The count is what makes that
    # choice safe: two rows sharing an instant would collapse in the set and cannot in the list.
    assert set(read) == expected, (
        f"the descent's read set is not the incremental read's: missing {expected - set(read)}, "
        f"extra {set(read) - expected}"
    )
    assert len(read) == len(expected), (
        f"{len(read)} reads over {len(expected)} eligible rows means one row arrived twice: {read}"
    )
    keys = [key for batch in stub.keys for key in batch]
    assert len(keys) == len(set(keys)), f"one row was handed to the model twice: {keys}"


@pytest.mark.asyncio
async def test_two_dispatches_of_one_descent_never_read_one_window_twice(pool, monkeypatch):
    """A run that forgot its own committed pair would re-read the cell it just published.

    The pair lives in the row and nowhere else, so the only proof that a second dispatch
    resumes rather than restarts is that no position of the first dispatch appears again in the
    second. The check spans two runs because the defect it pins is exactly the between-runs one:
    the seed re-asking its door, a slot resolving to a window already published, and a
    customer's material paying for a second curation.

    The backlog is deeper than one run may hold open, which is what makes this two dispatches of
    ONE descent: six cells is the most a run opens, so a ten-day floor leaves three cells un-cut
    when the first dispatch runs out of slots and the pair has to survive into the second. A
    six-day backlog would not test this any more -- the first dispatch reaches its floor and
    retires, which is what the retirement test asserts.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=10), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    for hours in (2, 26, 50, 74, 98, 122, 146, 170):
        await _doc(pool, owner, at=datetime.now(UTC) - timedelta(hours=hours), name=f"span-{hours}")
    first = _RunStub().install(monkeypatch)
    row = await _stored(pool, agent)
    await curation.run(row, workspace, "20261120120000", curation_service.position_of(row))

    after_first = await _stored(pool, agent)
    assert drain_of(after_first) is not None, (
        "the first dispatch ended a ten-day descent it had not reached the floor of, so the "
        "second dispatch had no descent left to resume"
    )

    second = _RunStub().install(monkeypatch)
    row = await _stored(pool, agent)
    await curation.run(row, workspace, "20261120120100", curation_service.position_of(row))

    positions = [_position(since, cur) for since, _until, cur in first.reads + second.reads]
    assert len(positions) == len(set(positions)), (
        f"one dispatch read a position the other had already read: {positions}"
    )


@pytest.mark.asyncio
async def test_a_hand_off_proposed_behind_the_watermark_leaves_the_stored_value_kept(pool):
    """AC (j)'s last case, which no `raises` assertion can see.

    `advance_watermark` answers a behind proposal by clamping it and logging, so a hand-off that
    proposes a position under the live watermark neither raises nor corrupts anything: the row
    keeps what it had. That behaviour is stated here as the stored VALUE, because "no exception
    was raised" reads the same for the bug and for the fix.

    It is asked of `finish_drain` directly rather than of a run, and that is the honest shape. A
    run cannot arrive at retirement with its published edge behind the watermark, because any
    ground above that edge is a head cell the run cuts and publishes first, which lifts the edge
    as it goes. What still has to hold, for whatever does propose a behind hand-off, is that the
    watermark's own monotonicity is kept and that the pair is cleared in the same transaction.
    """
    from backend.services import agent_service

    now = datetime.now(UTC)
    live = now - timedelta(days=40)
    agent, _workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    seam = live + timedelta(days=12)
    published = live - timedelta(days=2)
    agent = await _standing_descent(
        pool,
        agent,
        live=live,
        floor=live,
        seam=seam,
        descent_to=live,
        published=published,
    )
    drain = drain_of(agent)

    async with get_pool().acquire() as conn, conn.transaction():
        await agent_service.finish_drain(
            conn,
            UUID(str(agent["id"])),
            drain[0],
            drain[1],
            curation_service.position_of(agent),
            curation_service.Position(published),
        )

    row = await _stored(pool, agent)
    assert row["curated_through"] == live, (
        f"a hand-off behind the watermark moved it: stored {row['curated_through']}, was {live}"
    )
    assert drain_of(row) is None, "the retired pair survived while its hand-off was clamped away"


@pytest.mark.asyncio
async def test_a_head_cell_past_its_budget_defers_to_the_next_run_and_descends_now(
    pool, monkeypatch
):
    """A busy newest end is the failure mode of any newest-first rule, and this lane refuses it.

    A run that resumed its head cell until the cell was empty would never reach the backlog under
    it, which is the old defect with its two ends swapped: fresh input permanently starves the
    past. The fairness bound is therefore a promise about a RUN and not about a window — one head
    cell is worked, and whatever is left of it waits for the next dispatch while this run
    descends. The head cell's slot surviving on the row is the other half of the same claim: the
    fresh ground is deferred, not finished, and it is the newest thing on the map when the next
    run resolves its slots.
    """
    agent, workspace = await _live_lane(pool, back=timedelta(days=40), offset=timedelta(hours=12))
    owner = UUID(str(agent["user_id"]))
    now = datetime.now(UTC)
    for minutes in (20, 40, 60, 80, 100, 120):
        await _doc(
            pool,
            owner,
            at=now - timedelta(minutes=minutes),
            name=f"head-{minutes}",
            body="lesson " * 150,
        )
    await _doc(pool, owner, at=now - timedelta(days=5), name="backlog-row")
    stub = _RunStub().install(monkeypatch, budget=512)

    await curation.run(agent, workspace, "20261120120000", curation_service.position_of(agent))

    row = await _stored(pool, agent)
    seam = slot_instant(row["drain_cursor"]["plan"], "seam")
    heads = [read for read in stub.reads if read[1] > seam]
    descents = [read for read in stub.reads if read[1] <= seam]
    assert heads and descents, (
        f"a head cell wider than its budget gave this run no descent at all "
        f"(heads={len(heads)}, descents={len(descents)})"
    )
    assert len({read[1] for read in heads}) == 1, (
        f"one run may work one head cell, and it read above the seam at {sorted({r[1] for r in heads})}"
    )
    parts = row["drain_cursor"]["parts"]
    assert parts, "the head cell this run could not finish was dropped rather than deferred"
    newest = newest_open_part(row["drain_cursor"])
    assert slot_instant(parts[newest], "from") >= seam, (
        "the unfinished head cell is not the newest open window, so the next run will not "
        "resume the fresh ground first"
    )
