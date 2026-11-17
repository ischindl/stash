"""The change feed the daily Memory curator reads.

`changes_since` is the incremental delta since the curator's watermark: new
history events (excluding the curator's own run sessions), changed pages
(excluding the Memory subtree), new files, changed Drive-folder documents,
and the user's connected sources as pointers (the agent pulls source
specifics with `stash search`) — the curator never sees its own output.
`has_changes_since` is the cheap EXISTS the beat task uses to skip idle users
without waking a sprite.

A caller names which wiki it reads: the owner's own (`internal`) or the
workspace's shared, anonymized one (`external`). The shared wiki may be built
only from sessions of end users who share, and that rule is enforced in SQL
rather than left to prompt prose — for the cheap gate too, so a curator is
never woken for a delta its own feed cannot show. `has_changes_since`,
`_feed_events`, `complete_through` and `curator_event_backlog` therefore all
splice the single clause in `_SHARE_WIKI_EVENT_SCOPE`, share the one eligibility
clause in `_CURATOR_FEED_ELIGIBILITY`, bound on the one cursor clause in
`position_bound`, and collapse copies with the one identity in `_event_identity`
— the gate splicing `_canonical_rows()` as well, because a cursor standing inside
a tie is exactly when a row count and the feed's answer would otherwise disagree.
The backlog reaches the same truth through the group instead: an identity's copies
all share one instant, so it asks `row_ahead` per row and calls a group pending only
when every copy is ahead, which is the same statement as "its canonical row is
ahead" without a per-row anti-join. A second near-identical query would let gate
and feed disagree, which is the failure this file exists to make impossible: the
gate says there is work, the feed and the watermark boundary read exactly that
work, and the backlog reports exactly what is left of it — so the backlog reads
zero precisely when the feed comes back empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ..database import get_pool
from . import files_tree_service, source_service

# Caps so a single delta stays bounded (a long-idle account's first delta, a
# high-volume account's busy day). The unit is a distinct event, not a row: a
# session re-pushed eight times spends one slot (see _event_identity). Overflowing
# _MAX_EVENTS never loses events: the watermark only advances through what fit (see
# complete_through), so the remaining distinct events are re-presented next run.
_MAX_EVENTS = 500
_MAX_PAGES = 100
_MAX_FILES = 100
_MAX_SAVES = 100
_MAX_SOURCE_DOCS = 100
_SNIPPET = 280

# The two wikis a curator writes. Which one a run belongs to is a property of
# the agent (`agents.curator_wiki`), and it decides what its feed may contain.
WIKI_INTERNAL = "internal"
WIKI_EXTERNAL = "external"
WIKI_VALUES = (WIKI_INTERNAL, WIKI_EXTERNAL)


# --- The watermark position --------------------------------------------------
#
# A feed read is ordered `(created_at, id)`, so a position on it is a PAIR: an
# instant plus one event id naming how far inside that instant the read reached.
# An instant alone was the old shape, and it plateaued — when one `created_at`
# held more distinct events than the feed budget, the overflow advance
# (`T - 1µs`) landed behind the whole tie, every later run re-read the same
# first-budget window, and the backlog never drained (STAS-235).
#
# `event_id=None` is a full position, not an unset half: it means the instant
# alone carries the read — every event at that instant is behind it. That is
# exactly what every pre-pair watermark already asserted, which is why
# migration 0242 backfills nothing.
#
# One order governs the pair, rendered twice from the same rule: `ahead()` in
# Python, `_position_sql` in SQL, which every reader reaches through
# `position_bound` (what may be read) and `position_ahead` (what may be written).
# A second encoding — a hand-copied OR chain, a `greatest(...)`, a ±1µs step — is
# how the feed, the gate, the backlog and the watermark would start disagreeing.


# The greatest uuid a row can have. A pair with no event half encodes its id as
# this, which is the SQL reading of "the whole instant is behind me": no id in
# that instant can exceed it, so the pair stands one step past its last event.
_MAX_EVENT_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


@dataclass(frozen=True, slots=True)
class Position:
    """How far a curator has read: through `event_id` at `at`, or through the
    whole instant when `event_id` is None."""

    at: datetime | None
    event_id: UUID | None = None

    def __post_init__(self) -> None:
        # An id at no instant is not a position — reject it where it is typed
        # rather than let a half pair reach SQL or the CAS.
        if self.at is None and self.event_id is not None:
            raise ValueError("curator position with an event id must carry its instant")

    def __str__(self) -> str:
        """The form an operator reads: a refused advance, a stale-watermark alert,
        and a conflict all name the position they are refusing, and the instant is
        the part a log is searched for. The event half appears only when the lane
        stands inside an instant, because "where did that run stop?" is exactly the
        question a mid-tie answer settles. The dataclass repr is kept for tests and
        debugging, where which value is which matters more than grep-ability."""
        if self.at is None:
            return "never"
        instant = str(self.at)
        return instant if self.event_id is None else f"{instant}#{self.event_id}"

    def ahead(self, other: Position) -> bool:
        """Strictly ahead of `other`, on the pair's encoded order.

        The compared value is `(at, event_id or _MAX_EVENT_ID)`, so:

        - `NEVER.ahead(x)` is False for every x, `NEVER` included — a curator
          that has never run has read nothing, so it is ahead of nothing.
        - any real position `.ahead(NEVER)` is True.
        - a later instant is ahead whatever either event half says.
        - at one instant, `Position(T, None).ahead(Position(T, e))` is True: a
          position that consumed the whole instant is ahead of one standing
          inside it, and the reverse is False. This is the direction
          `mark_curated` clamps on, so inverting it would either re-read a whole
          instant forever or skip an unread half of one.
        - two ids at one instant follow uuid order; equal ids are not ahead.

        `_position_sql` renders this same order for SQL."""
        if self.at is None:
            return False
        if other.at is None:
            return True
        if self.at != other.at:
            return self.at > other.at
        if self.event_id == other.event_id:
            return False
        if self.event_id is None:
            return True
        if other.event_id is None:
            return False
        return self.event_id > other.event_id


# Never curated: bounds nothing, is behind everything, and is what an agent row
# with no instant means — not a missing value to be guessed at.
NEVER = Position(None)


def position_of(agent: dict) -> Position:
    """The pair one agent row stores, as the one value it is.

    The only way to read a watermark out of a row: taking `curated_through` on its
    own loses the half that steps inside a tie. A row with no instant never ran;
    a row holding an id with no instant is corrupt, and says so here."""
    instant = agent.get("curated_through")
    event_id = agent.get("curated_through_event_id")
    if instant is None:
        if event_id is not None:
            raise ValueError(f"agent {agent.get('id')} stores an event id with no instant")
        return NEVER
    return Position(instant, UUID(str(event_id)) if event_id is not None else None)


def drain_of(agent: dict) -> tuple[datetime, dict] | None:
    """The drain position this row is resuming from, or None when no drain is in flight.

    A drain is in flight exactly when its pair carries a value: an instant, or a cursor
    naming where inside an instant its last committed pass stopped. A drain that has
    committed nothing carries neither, and reading the feed from its beginning IS the
    resume point for it — so seeding one is the absence of a pair rather than a row of its
    own, and no marker can drift out of sync with the work it claimed to describe.

    The cursor has three shapes and every one of them is read here, because this is where
    the row's drain state becomes a value a caller can use:

    - no `plan` key: a drain seeded at `drain_through` with no window cut yet — the pair
      today, with at most sparse `partial` offsets beside the instant. The column is the
      position, and `cut_drain_part` is the pure function that turns this state into a
      planned one.
    - `plan` plus `parts`: one position per part inside this same jsonb. The column beside
      them is DERIVED — the `from` of the oldest un-composed slot, written by the one
      UPDATE that commits or prunes a slot — and `drain_parts_of` refuses a row whose
      column contradicts that derivation, loudly and without repair.
    - anything else (a `parts` that is not a map, a `plan` whose `parts` key is absent,
      a slot missing its window): raises. An inconsistent cursor is not repaired in
      place, because repairing it in a reader would let two readers invent two histories
      for the same row.
    """
    drain_parts_of(agent)
    instant = agent.get("drain_through")
    cursor = agent.get("drain_cursor") or {}
    if instant is None and not cursor:
        return None
    return instant, cursor


# The part identity is its `from` instant as text: sorting part ids as strings sorts
# them as instants, so the compose order, the derivation order, and the order an
# operator greps in the logs are one order and not three.
DRAIN_PART_ID_FORMAT = "%Y%m%dT%H%M%S"

# The instant a part covering the whole corpus starts from. A drain seeded with no
# instant reads from before the first event ever written, and its first slot's `from`
# must still name a concrete instant: a NULL inside a slot would push a NULL into the
# derived column while a plan lives, and `drain_of` reads a NULL column as "seeded at
# the beginning" — the 140M-token re-read the derivation exists to prevent.
#
# 1900, not year one, because the sentinel must survive the column it is derived into:
# measured on this suite's asyncpg against `timestamptz`, a year-one write comes back
# NAIVE (every later instant probed, down to 1900, comes back aware), and a naive
# sentinel contradicts its own derivation the moment any reader re-validates the row.
# The honest limit is one clause wide: history rows stamped before 1900 — only ever a
# client's deliberately absurd `created_at` — are behind this sentinel, and they remain
# reachable the way they always were, by the pair-drain seed that names no instant at
# all. No recorded corpus this card has measured lives there.
DRAIN_BEGINNING = datetime(1900, 1, 1, tzinfo=UTC)


def part_id_of(instant: datetime) -> str:
    """The part's name: its `from` instant, UTC, to the second."""
    return instant.astimezone(UTC).strftime(DRAIN_PART_ID_FORMAT)


def slot_instant(slot: dict, field: str) -> datetime | None:
    """One of a slot's instants, parsed. JSON carries instants as text, so the map
    round-trips them as ISO strings and every reader parses here, once."""
    value = slot.get(field)
    if value is None:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def drain_parts_of(agent: dict) -> dict | None:
    """The parts map this row's drain carries, or None while it has none.

    Validation, not repair: a cursor this function can name is a cursor every reader
    may then trust, and a cursor it cannot name raises with the agent in the message.
    The named shapes of nonsense are the ones Design enumerates — a `parts` that is
    not a map, a `plan` whose `parts` key is absent, a slot missing its window or its
    state, and a column that contradicts the derivation the column is supposed to
    carry. A plan whose `parts` map is empty is NOT nonsense: it is the fully-composed
    state that stands between the compose pass pruning the last slot and `finish_drain`
    retiring the pair, and retirement must be able to ask it about the edge."""
    cursor = agent.get("drain_cursor") or {}
    plan = cursor.get("plan")
    parts = cursor.get("parts")
    if plan is None and parts is None:
        return None
    agent_id = agent.get("id")
    if plan is None or not isinstance(plan, dict):
        raise ValueError(
            f"curator {agent_id} drain cursor carries parts with no plan: a parts map "
            "with no plan is a history no reader can reconstruct"
        )
    if "parts" not in cursor or not isinstance(parts, dict):
        raise ValueError(
            f"curator {agent_id} drain cursor carries a plan with no usable parts map: "
            "the plan names windows the row does not carry"
        )
    for part_id, slot in parts.items():
        if (
            not isinstance(slot, dict)
            or slot_instant(slot, "from") is None
            or slot_instant(slot, "to") is None
        ):
            raise ValueError(
                f"curator {agent_id} drain slot {part_id} does not name its window: "
                "a slot without its from and to cannot be composed or resumed"
            )
        if slot.get("state") not in ("open", "done"):
            raise ValueError(
                f"curator {agent_id} drain slot {part_id} carries state "
                f"{slot.get('state')!r}: a slot is open or done, no other word is legal"
            )
    if parts:
        derived = drain_derived_through(cursor)
        stored = agent.get("drain_through")
        if stored is None or stored != derived:
            raise ValueError(
                f"curator {agent_id} drain column stores {stored} while its parts derive "
                f"{derived}: the column is written as a derivation, so a mismatch is a "
                "hand-edit or a bug, and no reader may guess which history to believe"
            )
    return parts


def drain_derived_through(cursor: dict) -> datetime | None:
    """The instant the drain column must carry while parts exist: the `from` of the
    OLDEST slot.

    Oldest means the one not yet COMPOSED — a `done` slot still holding its report is
    history not yet folded into the wiki, and every reader that asks the column (`readout
    position_of` and its four askers) asks how much history is unfolded. None means the
    map is empty, which is the state the compose pass leaves between pruning the last
    slot and `finish_drain` clearing the pair; the writer of that transition names the
    edge it composed through, because a NULL here would read as "seeded at the
    beginning" to every reader of `drain_of`.

    String-sorting the keys sorts the instants: `part_id_of` names a slot by its `from`
    to the second, so this is a min over keys and not a scan or a cleverness."""
    parts = cursor.get("parts") or {}
    if not parts:
        return None
    oldest = parts[min(parts)]
    return slot_instant(oldest, "from")


def drain_open_parts(cursor: dict) -> int:
    """How many of this cursor's slots are still open — the count the planning gate
    bounds and the retirement condition empties."""
    parts = cursor.get("parts") or {}
    return sum(1 for slot in parts.values() if slot.get("state") == "open")


def drain_newest_edge(cursor: dict) -> datetime | None:
    """The newest instant any slot of this cursor has committed, or None while none has.

    Retirement asks whether this edge is at or past the plan's target, and asks the
    pending-input gate AT that edge — not at the column, which stands at the oldest
    un-composed slot and would answer 'pending' forever while any part is open."""
    parts = cursor.get("parts") or {}
    edges = [
        instant
        for instant in (slot_instant(slot, "through") for slot in parts.values())
        if instant is not None
    ]
    return max(edges) if edges else None


def drain_composable_parts(cursor: dict) -> list[str]:
    """The slots a compose pass may fold into the wiki NOW, in the order it must fold
    them — ascending `part_id`, which is ascending instant.

    Only the CONTIGUOUS `done` prefix counted from the oldest slot qualifies. A `done`
    slot sitting behind an `open` one has an older window still owed to the wiki, and
    folding the newer report first would put the older knowledge on the wiki AFTER the
    newer — the arrival-order accident AC (b) exists to forbid. Waiting on the older
    slot costs a dispatch and buys the one property the composition is defined by: what
    the wiki ends up holding is a function of the report set plus part order, never of
    which worker finished first.

    Empty means nothing is composable: either the oldest slot is still open, or nothing
    is `done`. A pass that composes nothing must not prune anything, which is why the
    answer here is a list and not a bool."""
    parts = cursor.get("parts") or {}
    ready: list[str] = []
    for part_id in sorted(parts):
        if parts[part_id].get("state") != "done":
            break
        ready.append(part_id)
    return ready


def drain_is_planned(agent: dict) -> bool:
    """Whether this row's drain is a fanned-out drain — a drain whose history is cut
    into parts — rather than the single resumable pair a legacy drain carries.

    The test is the `parts` key's presence, checked through `drain_parts_of` so a
    cursor that merely LOOKS planned but names nonsense raises rather than reading
    as unplanned: a corrupt plan silently re-seeded is the duplicate read this whole
    mechanism was built to make impossible. A legacy pair cursor (no plan, no parts)
    answers False, which is what keeps an in-flight serial drain on the path it
    started on."""
    return drain_parts_of(agent) is not None


def drain_slot_position(agent: dict, part_id: str) -> tuple[Position, datetime] | None:
    """The position a part's pass fences on and its closed upper edge, or None when
    the slot is absent or already `done` (its window has an author; no turn is owed).

    The position is the slot's committed pair — its `through` and `cursor` when a
    previous pass of this part left one, its `from` and seeded offsets otherwise —
    so a resumed part continues inside its own window instead of re-reading it.
    `drain_parts_of` has already validated the slot's shape by the time this reads
    it, which is why this function only asks the slot's own two questions."""
    parts = drain_parts_of(agent)
    if parts is None:
        return None
    slot = parts.get(part_id)
    if slot is None or slot.get("state") != "open":
        return None
    at_iso = slot.get("through") or slot.get("from")
    to_at = slot_instant(slot, "to")
    if at_iso is None or to_at is None:
        raise ValueError(
            f"curator {agent.get('id')} drain slot {part_id} names no readable position: "
            "a slot with neither its committed through nor its from cannot be fenced on"
        )
    at = at_iso if isinstance(at_iso, datetime) else datetime.fromisoformat(at_iso)
    cursor = slot.get("cursor") or {}
    return Position(at, cursor.get("id")), to_at


def read_position_of(agent: dict) -> tuple[datetime | None, dict]:
    """Where this curator's feed is read from: the drain pair, or the incremental pair.

    Two positions live on an agent row and exactly one of them is live at a time. While a
    history drain is in flight it owns the read — its backlog sits BEHIND the watermark,
    so a reader that consulted the watermark would skip everything the drain had not
    reached and call the lane caught up. Every RUN asks here — a run's first pass and the
    next dispatch that resumes it — so the passes of one lane cannot disagree about what
    it has left to read.

    Readers that want the live instant alone, rather than the pair a pass fences on, ask
    `readout_position_of`: the console readout, the recompute gate, the credits-spent
    alert, and the pending-input check that decides whether a lane is worth waking. Both
    functions answer the same question in the one shape their callers can use, which is
    why neither of them may be re-implemented at a call site.
    """
    drain = drain_of(agent)
    if drain is not None:
        return drain
    return agent.get("curated_through"), agent.get("curation_cursor") or {}


def readout_position_of(agent: dict) -> Position:
    """Which position is live for this row, as the pair every counting helper already takes.

    The readout sibling of `read_position_of`, for the readers that report a number or
    gate a dispatch instead of running a pass: that one hands back `(instant, cursor)`,
    because a run must fence on its event half, while a readout is a scalar instant — the
    shape `has_changes_since` and `curator_event_backlog` and the HTTP payloads take.

    That asymmetry is a bound, not a rounding. A drain pair carries NO event half, so
    during a drain the answer here is the whole instant: a lane parked inside a saturated
    instant is read from BEFORE that instant rather than reported as though the tie it
    stands in had already been consumed. The conservative direction is the point — the
    alternative is a backlog that reads zero while a tie tail is still unread.

    A drain that has committed nothing is seeded by its absence: it carries no pair, so
    between its dispatch and its first committed pass this still names the watermark.
    Nothing stored separates that window from "no drain", and a marker column to separate
    them would be a second position counter — exactly what these two functions exist to
    make impossible.

    Askers, so that a fifth cannot join silently: `curator_readout` (the developer
    console and the Settings rows), the `POST /me/memory/recompute` gate, the
    credits-spent clause of the stale-curators alert, and `_pending_input`. A run asks
    `read_position_of` instead, and `GET /me/changes` stays caller-relative on purpose —
    `drain_pass_budget` recognises a drain pass by comparing the request's position to a
    row's stored pair, so a route that shifted it would stop recognising that pass.
    """
    drain = drain_of(agent)
    if drain is not None:
        return Position(drain[0])
    return position_of(agent)


def _budget_kept(events: list[dict], max_event_chars: int | None) -> int | None:
    """How many leading events a character budget can carry, or None when all of them fit.

    One definition, because two readers of the same feed have to cut it in the same place:
    this is what `complete_through` commits through and what `changes_since` refuses to
    show, so the last event a curator is handed is the last event its run may record. Two
    rules keep a drain from stalling on it: a timestamp group is never split (an event is
    shown and committed with its whole instant or not at all), and an oversized FIRST group
    is never refused — that would make the budget smaller than the smallest pass the feed
    can make, and the drain would read the same characters forever (#1272).

    The characters counted are each event's whole content, not the snippet the feed renders
    it as: the budget bounds the work a pass takes on, and the model reads past the snippet
    whenever an event matters.
    """
    if max_event_chars is None:
        return None
    characters = 0
    for kept, event in enumerate(events):
        characters += len(event["content"] or "")
        if characters > max_event_chars and event["created_at"] > events[0]["created_at"]:
            return kept
    return None


def drain_budget_of(agent: dict) -> int:
    """The input characters one draining pass of this curator may carry.

    Sized by the run's own wall, not by its ceiling: a drain exists because the backlog
    outran the wall, and a slice sized by the ceiling is a slice the wall cannot finish.
    It reuses the scoped lane's rate constant rather than stating a second one.

    The budget is a property of the ROW's wall, which is why every part of one planned
    drain shares it: two slots standing at the same seam carry one identical budget, so
    `drain_pass_budget` counts ambiguity per row, not per instant.
    """
    from . import agent_service, scoped_curation_service

    return scoped_curation_service.input_budget(agent_service.run_wall_seconds(agent))


def drain_frontier(cursor: dict, standing: datetime | None) -> datetime:
    """Where the next window would open: the newest `to` among the slots, or the row's
    column when no slot stands (and the beginning when neither names an instant).

    The column is NOT the frontier while slots exist — it names the OLDEST un-composed
    `from`, and a plan built from that would re-cut a window the drain already
    committed. One reader of the seam serves the cutter and the coordinator both, so a
    plan cannot be chased from one edge and cut from another.
    """
    parts = cursor.get("parts")
    if parts:
        return max(slot_instant(slot, "to") for slot in parts.values())
    return standing if standing is not None else DRAIN_BEGINNING


def drain_seam_target(anchor: datetime, part_hours: int, now: datetime) -> datetime:
    """The last seam at or before `now` on the grid `anchor + k * part_hours`.

    A plan chasing live time is planned up to HERE and no further, because a window is
    never clamped to a live `now`: a window that closes mid-second is a seam no
    granularity promised, the next cut opens FROM that mid-second instant, and part ids
    name seconds — so the next pass reaches for an id the slot beside it already spent.
    The live proof of this card is what found the pair: two coordinator passes 56
    microseconds apart, and the second died naming two plans over one seam.
    """
    step = timedelta(hours=part_hours)
    return now - ((now - anchor) % step)


def cut_drain_part(
    cursor: dict,
    standing: datetime | None,
    *,
    part_hours: int,
    target: datetime,
    max_open: int,
) -> dict | None:
    """The drain cursor with one more part window cut into it, or None when no window
    may be cut right now. Pure: it reads the cursor it is given and returns a new one,
    because the row's pair must not move under an unplanned read while a window is
    being imagined.

    The plan is generated forward and never materialised whole: the next window opens
    where the last one closed, so a drain's whole history needs no scan of a 549M-character
    backlog to plan — a boundary is a constant-time addition. The cut refuses when open
    parts already number `max_open` (the coordinator passes 2 x the endpoint bound, so
    claims exist only where a worker could soon run them) and when the last window
    already reaches `target`; if rows appear past the target, the caller raises the
    target and cuts again, which is what retirement consults.

    `target` is a seam the caller derived (`drain_seam_target`), never a raw clock
    reading: a window closes exactly `part_hours` after it opens, so every seam of a
    plan stays on the grid its first cut fixed. The exception is the sentinel's first
    window below, which spans the empty head to the target it is handed.

    `standing` is the row's column at cut time, and it is consulted only when the map
    is empty: a seeded cursor cuts its first window from it (None means the beginning,
    and the first slot says so with `DRAIN_BEGINNING` rather than a NULL the derivation
    would choke on), and a fully-composed cursor continues from the edge the compose
    pass wrote there. A cursor that already has slots continues from the newest `to`
    among them — the column would name the OLDEST un-composed `from`, and a plan built
    from that would re-cut a window the drain already committed.

    A seeded cursor's sparse `partial` offsets MOVE into the first slot's cursor — the
    parts map, once present, is authoritative, and leaving the offsets behind at the
    top level would be a second copy of a resumable position."""
    plan = cursor.get("plan")
    parts = cursor.get("parts")
    if plan is not None and plan.get("part_hours") != part_hours:
        raise ValueError(
            f"drain plan was cut at part_hours={plan.get('part_hours')} and carries "
            f"that granularity; a caller asking for {part_hours} must await a new plan, "
            "not shift the seams of this one"
        )
    if plan is None and parts is None:
        frontier = standing if standing is not None else DRAIN_BEGINNING
        plan = {
            "part_hours": part_hours,
            "frontier": frontier.isoformat(),
            "target": target.isoformat(),
        }
        parts = {}
        seed_cursor = {"partial": cursor["partial"]} if "partial" in cursor else {}
    elif plan is None or not isinstance(plan, dict) or not isinstance(parts, dict):
        raise ValueError("cut_drain_part: cursor is neither unplanned nor a valid plan")
    else:
        seed_cursor = {}
        if drain_open_parts(cursor) >= max_open:
            return None
    from_at = drain_frontier(cursor, standing)
    if from_at >= target:
        return None
    if from_at == DRAIN_BEGINNING:
        # The first window of a corpus-covering drain spans the empty head: from the
        # sentinel to the caller's target, however many `part_hours` that is. Hours are
        # the granularity of RECORDED history, and year one is not recorded history —
        # clamping this window to one part's width would open the planning gate's whole
        # allowance of empty slots, each claiming a dispatch to read no rows, before the
        # corpus's first event. The sentinel's lower bound reads strictly after itself,
        # so the target must be the first event's own instant (the coordinator supplies
        # it) or a row at the corpus minimum belongs to no window.
        to_at = target
    else:
        # Only a window whose close has already been reached is opened: a window that
        # would overshoot `target` is a seam the grid has not earned yet, and a partial
        # window in its place would move every later seam off the grid.
        if from_at + timedelta(hours=part_hours) > target:
            return None
        to_at = from_at + timedelta(hours=part_hours)
    part_id = part_id_of(from_at)
    if part_id in parts:
        raise ValueError(
            f"cut_drain_part would cut window {part_id} over a slot the cursor already "
            "carries: two plans cannot own one seam"
        )
    slot = {
        "from": from_at.isoformat(),
        "to": to_at.isoformat(),
        "through": None,
        "cursor": seed_cursor,
        "state": "open",
        "claim_expires_at": None,
    }
    parts = {**parts, part_id: slot}
    return {k: v for k, v in cursor.items() if k != "partial"} | {"plan": plan, "parts": parts}


async def drain_pass_budget(
    owner_user_id: UUID,
    position: Position,
    wiki: str,
    folder_id: UUID | None = None,
    until: datetime | None = None,
) -> int | None:
    """The pass budget of the drain standing AT `position`, or None when no drain does.

    This is how the feed knows it is being read by a drain rather than by a person or by an
    incremental run: a drain pass always reads at its own stored position, so a request
    naming a different one is not that pass and is served the whole feed as always. Without
    that discrimination the budget would truncate a human's look at the same lane.

    `until` is the window the request reads to, carried so the recognition sees the
    whole request shape. It changes no answer: the budget is a property of the row's
    wall, and every part of one planned drain shares that wall, so a part reading its
    own closed window is sized by the same number as the drain it belongs to.

    Exactly one curator row can answer, because `one_curator_per_scope` makes (user, wiki,
    folder) unique; two answers would mean the index is not what this reads it as, and that
    is reported rather than averaged over.
    """
    from . import agent_service

    if position.at is None:
        return None
    standing = [
        row
        for row in await agent_service.list_curators(owner_user_id)
        if row["curator_wiki"] == wiki
        and row["curator_folder_id"] == folder_id
        and _row_stands_at(row, position.at)
    ]
    if len(standing) > 1:
        raise RuntimeError(
            f"{len(standing)} curators of {owner_user_id} drain {wiki}"
            f"{' folder ' + str(folder_id) if folder_id else ''} from "
            f"{position.at}: one feed request cannot be bounded by two walls"
        )
    return drain_budget_of(standing[0]) if standing else None


def _row_stands_at(agent: dict, at: datetime) -> bool:
    """Whether this row's drain holds a standing pair exactly at `at`.

    A planned row stands at every slot's committed instant — or at a slot's `from`
    while that slot has committed nothing. Two slots of one row standing at ONE instant
    is not an accident to refuse: a part that finished at its `to` and the next part
    seeded at that same `to` stand at the seam together, and both carry the identical
    budget because the budget comes from the row's wall. So the ambiguity that
    `drain_pass_budget` refuses is per row, never per instant: one feed request still
    cannot be bounded by two walls, but two slots of one wall bound it just fine."""
    parts = drain_parts_of(agent)
    if parts is not None:
        return any(
            (slot_instant(slot, "through") or slot_instant(slot, "from")) == at
            for slot in parts.values()
        )
    drain = drain_of(agent)
    return drain is not None and drain[0] == at


def _position_sql(instant: str, event_id: str) -> str:
    """One pair as a comparable SQL row, in `ahead`'s order.

    Both halves take both shapes: a column name or a `$n` parameter. The casts are
    what make a parameter's NULL event id a definite uuid instead of a
    three-valued comparison, which is why the whole-instant reading needs no
    branch — `COALESCE(NULL, _MAX_EVENT_ID)` IS that reading."""
    return f"({instant}::timestamptz, COALESCE({event_id}::uuid, '{_MAX_EVENT_ID}'::uuid))"


def position_bound(args: list, position: Position) -> str:
    """The `AND` clause admitting rows strictly ahead of a position.

    ONE clause covers both shapes of the pair: the position's event half is
    COALESCE'd to `_MAX_EVENT_ID`, which makes `he.id > MAX` false for every row
    and collapses a whole instant to the index range `created_at > at`. There is
    deliberately no mid-tie branch in Python choosing between two clauses — that
    would be a second encoding of the order, and the four readers splicing this
    one are why the gate, the feed, the backlog and the boundary cannot disagree.

    Assumes the `history_events` alias `he`, as every reader here does, and
    appends its parameters to `args` (positional, like every other clause). The
    never-curated position bounds nothing — the whole corpus is ahead."""
    if position.at is None:
        return ""
    args.extend([position.at, position.event_id])
    instant, event = len(args) - 1, len(args)
    return (
        f" AND he.created_at >= ${instant}"
        f" AND (he.created_at > ${instant}"
        f" OR he.id > COALESCE(${event}::uuid, '{_MAX_EVENT_ID}'::uuid))"
    )


def instant_bound(args: list, position: Position) -> str:
    """The `AND` clause admitting the position's own instant and everything after it.

    The coarser sibling of `position_bound`, for the one reader that cannot use the
    sharp form: the backlog groups by event identity, and every copy of an identity
    shares one `created_at`, so a scan that starts at the cursor's INSTANT still
    sees all of a group's copies — which is what lets it decide pending-ness by
    looking at them instead of probing per row. `row_ahead` then asks the sharp
    question row by row, so nothing the cursor has passed disappears from the
    figures. Never-curated bounds nothing, as in `position_bound`."""
    if position.at is None:
        return ""
    args.append(position.at)
    return f" AND he.created_at >= ${len(args)}"


def until_bound(args: list, until: datetime | None) -> str:
    """The `AND` clause closing a read window at `until`, spliced by every reader
    that reads a bounded one.

    The operator stays `<=`, and the reason is a loss, not a style choice. Parts
    tile history as `[from_i, to_i]` with `from_{i+1} = to_i`, so the seam between
    two adjacent windows is ONE instant both parts must agree about. The lower
    bound (`position_bound`) admits rows strictly ahead of its pair — a drain pair
    carries no event half, so a drain seam is read as `created_at > to_i`. If this
    clause closed with `<` instead, a row standing exactly at the seam would be in
    neither part: never shown, yet claimed as read by the boundary the earlier
    part earns — `complete_through` answers `Position(at=until)` for a feed that
    did not overflow, which is only sound because the feed read `<= until`. With
    `<=` the row at the seam belongs to the part that could see it, and the next
    part's strict lower bound cannot admit it twice.

    A future reader tempted to tidy the pair into a half-open range would strand
    every row that shares an instant with a seam — this paragraph is the argument
    to re-read before doing it.

    Assumes the `history_events` alias `he`, as every reader here does, and
    appends its parameter to `args` (positional, like every other clause). `None`
    is "bounded by nothing": the reader runs to now, served exactly what it was
    served before windows existed."""
    if until is None:
        return ""
    args.append(until)
    return f" AND he.created_at <= ${len(args)}"


def position_ahead(args: list, position: Position, instant_ref: str, id_ref: str) -> str:
    """A boolean SQL expression: the pair at `instant_ref`/`id_ref` is strictly
    ahead of `position`, in the order `ahead` implements.

    The compare-and-set in `agent_service.mark_curated` splices this as its
    monotonic clamp, and `memory_service` splices it to ask which lanes have
    already read past a rewind target: both compare a stored pair against a
    proposed one, so both use `_position_sql` rather than the instant alone. A
    stored position with no instant is ahead of nothing, which is why the
    `IS NOT NULL` guard leads — an uncurated lane must never clamp a proposal.

    The two callers differ only in what their left-hand pair is: the CAS and the
    rewind query name two columns of an agent row, `row_ahead` names the two
    columns of a history event.

    `position` is what the caller holds and must be a real position: a
    never-curated one has no SQL bound to render, and naming that here beats
    silently matching every row."""
    if position.at is None:
        raise ValueError("a never-curated position has no SQL bound to compare against")
    args.extend([position.at, position.event_id])
    instant, event = len(args) - 1, len(args)
    return (
        f"({instant_ref} IS NOT NULL"
        f" AND {_position_sql(instant_ref, id_ref)} > {_position_sql(f'${instant}', f'${event}')})"
    )


def row_ahead(args: list, position: Position, instant_ref: str, id_ref: str) -> str:
    """A boolean SQL expression: the ROW at `instant_ref`/`id_ref` is ahead of
    `position`.

    `position_bound` is this same predicate as a WHERE term, which drops the rows
    behind the cursor; this is the form an aggregate needs, so a reader can ask the
    question per row and still count the rows it answers False for. Its one caller
    is the backlog's `GROUP BY`, and that difference is the whole point: a
    re-imported session's copies stay in `raw_rows` after the cursor has passed
    them, which is the amplification the figure exists to show.

    Unlike `position_ahead`, a never-curated position qualifies every row — a
    curator that has read nothing has everything ahead of it, the same reading
    `position_bound` gives by bounding nothing."""
    if position.at is None:
        return "true"
    return position_ahead(args, position, instant_ref, id_ref)


# An event reaches the shared, anonymized wiki only through the end user its
# session belongs to — the privacy rule the prompt used to be trusted to
# enforce. The fragment names `he`, the `history_events` alias every reader
# gives it, so the SAME text is spliced into the feed, the watermark advance,
# and the beat's gate. That sharing is the point: gate-true has to mean
# feed-non-empty, and three separately-derived predicates drift — the beat
# dispatches a curator whose feed is empty (a sprite wake and a metered run
# burned on nothing) or stays silent while the feed has work.
#
def cleared_project_clause(sessions_alias: str) -> str:
    """The predicate that withholds a project the developer has not cleared.

    Per-project clearance is the developer's own veto over their workspace: an
    event filed in a project that is not cleared reaches no shared completion,
    even from an end user who shares their own history. The Default folder is
    the unfiled catch-all and carries no routing decision either way (see
    `_project_share_wiki`), so it is never the veto.

    One definition, spliced under whichever `sessions` alias a reader already
    uses: the beat's gate and the backlog have to mean what a shared completion
    will actually read, or the beat burns a sprite wake and a metered run on an
    empty completion.
    """
    return (
        f"NOT EXISTS (SELECT 1 FROM session_folders cf "
        f"WHERE cf.id = {sessions_alias}.session_folder_id "
        f"AND NOT cf.is_default AND NOT cf.share_wiki)"
    )


# An event the wiki cannot attribute to a sharing end user — a session with no
# end user, or an event whose session row is gone — does not reach it, and
# neither does one filed in a project the developer has not cleared. The owner's
# own wiki filters nothing: it is the owner's own memory.
_SHARE_WIKI_EVENT_SCOPE = (
    "AND EXISTS (SELECT 1 FROM sessions wse "
    "JOIN end_users we ON we.id = wse.end_user_id "
    "WHERE wse.owner_user_id = he.owner_user_id "
    "AND wse.session_id = he.session_id "
    f"AND we.share_wiki AND {cleared_project_clause('wse')})"
)

# What the curator is permitted to read, as one clause. Its own run transcripts
# (`agent-curate-%` sessions) are the one thing an owner-scoped feed must never
# show: they would echo-loop the daily gate and pollute the wiki, and filtering
# them after the query would let them consume feed slots that belong to real
# activity. The feed, the beat's gate, the watermark boundary, and the backlog
# all splice this one text, because "the gate fired" has to mean "the feed has
# work", and a backlog that counted these rows could never reach zero — every
# successful run appends its own transcript behind the position it just wrote.
# Every session id the curator's runs mint starts with this, whichever wiki it
# writes. Sessions have no agent foreign key, so this prefix is the only signal
# that a session belongs to the curator rather than to a person. It lives with
# the feed because the feed is what decides those transcripts are internal
# noise; `sprite_agent_service.scheduled_session_prefix` reads it back from
# here when it mints the ids.
CURATOR_SESSION_ID_PREFIX = "agent-curate-"


def curator_run_exclusion_clause(sessions_column: str) -> str:
    """The predicate that withholds the curator's own run transcripts.

    The curator must never feed on itself, so the feed has always refused
    `agent-curate-%` sessions — and the human-read Sessions list adopts that
    same decision rather than re-deciding it: what the machine calls noise
    cannot be content for the person on the very next screen. Spliced under
    whichever column a reader aliases its sessions to, exactly like
    `cleared_project_clause`.
    """
    return f"{sessions_column} NOT LIKE '{CURATOR_SESSION_ID_PREFIX}%'"


_CURATOR_FEED_ELIGIBILITY = (
    "AND (he.session_id IS NULL OR " + curator_run_exclusion_clause("he.session_id") + ")"
)

# One event, no matter how many times its session was re-uploaded. A re-import
# (onboarding, a client resending a transcript) inserts rows that differ only in
# their surrogate id: `push_events_batch` keeps the client's own `created_at` and
# content verbatim, so a session pushed eight times holds each of its events
# eight times. Counting rows therefore overstates both the founder-visible
# backlog and what a run actually consumed, by however many times the history
# happened to be re-sent (~8x on the account that motivated this).
#
# Each part earns its place:
#   session_id — the event belongs to one transcript; two sessions saying the
#     same words at the same instant are two events.
#   event_type — a `tool_use` and its `tool_result` legitimately share a
#     timestamp and must not collapse into each other.
#   created_at — the same content at genuinely different times IS two events
#     (an agent that said "done" twice said it twice).
#   md5(content) — content decides. Grouping on raw content would sort megabytes
#     of transcript text, so the repo already hashes in SQL (migrations 0142 and
#     0187). Deliberately NOT the `content_hash` column: the push path never
#     writes it (only the embedding task does, after the fact), so freshly
#     imported rows are NULL, and NULLs group as equal — the copies this exists
#     to separate would collapse together. `agent_name` is deliberately OUT, so
#     a re-import labelled by a different client still collapses to what it is.
#
# md5 can collide, and a collision would silently drop one event from curation.
# At this scale (thousands of events per owner, not billions) the probability is
# negligible — stated plainly rather than pretending it is impossible.
_IDENTITY_COLUMNS = ("session_id", "event_type", "created_at")


def _event_identity(alias: str) -> str:
    """The event identity documented above, qualified to one table alias.

    One definition serves both renderings the feed's anti-join needs: a row is
    canonical when no lower `id` carries the same identity, which is one
    comparison between two qualified copies of this tuple rather than the
    identity written out twice where it could drift apart.
    """
    columns = [f"{alias}.{column}" for column in _IDENTITY_COLUMNS]
    columns.append(f"md5({alias}.content)")
    return ", ".join(columns)


# One rendering of "this row is the copy the feed offers", naming the alias `he`
# the way every reader here names it. The feed has always spliced it; the gate and
# the backlog splice it too now that a watermark can stand inside a tie.
#
# The two claims are NOT interchangeable and the difference is the backlog: rows
# this predicate rejects are not pending work (their canonical twin was offered
# whenever that twin was behind the cursor), but they are rows a run read, so the
# `raw_rows` half of the backlog still counts them. Splicing this into a backlog
# WHERE would therefore collapse `raw_rows` into `distinct_events` and erase the
# re-import amplification the pair exists to expose — so the backlog aggregates it
# per identity group instead of filtering on it.
def _canonical_rows() -> str:
    """The `AND` term admitting only canonical rows: no lower `id` carries this
    row's identity, so of a transcript's N re-imported copies only the first is
    ever offered to a curator."""
    return (
        "NOT EXISTS ( "
        "SELECT 1 FROM history_events dup "
        "WHERE dup.owner_user_id = he.owner_user_id "
        f"AND ({_event_identity('dup')}) = ({_event_identity('he')}) "
        "AND dup.id < he.id)"
    )


def _wiki_event_scope(wiki: str) -> str:
    """The `AND` clause limiting an event query to what one wiki may read.

    Empty for the owner's own wiki. `wiki` has exactly two values and no
    default anywhere: a typo has to fail where it is typed rather than quietly
    curate the wrong wiki with the wrong scope."""
    if wiki == WIKI_INTERNAL:
        return ""
    if wiki == WIKI_EXTERNAL:
        return _SHARE_WIKI_EVENT_SCOPE
    raise ValueError(f"unknown wiki {wiki!r}; expected {WIKI_INTERNAL!r} or {WIKI_EXTERNAL!r}")


def _folder_event_scope(folder_id: UUID, args: list) -> str:
    """The `AND` clause limiting an event query to one session folder's sessions.

    An EXISTS over sessions rather than a join: the feed's hot inner query reads
    the owner+created_at index and must keep its shape at every scope, and like
    the sharing scope a whole session is either in the folder or not, so a
    duplicate's identity group is never split by the clause — the gate/feed
    equivalence survives folder scoping for the same reason it survives dedupe.
    """
    args.append(folder_id)
    return (
        "AND EXISTS (SELECT 1 FROM sessions fse "
        "WHERE fse.owner_user_id = he.owner_user_id "
        "AND fse.session_id = he.session_id "
        f"AND fse.session_folder_id = ${len(args)})"
    )


def _feed_conditions(
    wiki: str,
    position: Position,
    until: datetime | None,
    args: list,
    folder_id: UUID | None = None,
    cursor_bound=position_bound,
) -> str:
    """Build the one WHERE clause that says what one wiki's curator may read.

    Owner scope, the eligibility clause, the sharing scope, the folder scope, the
    cursor, and the closing instant, in one place: the feed, the watermark
    boundary, and the backlog are the same question asked three ways, and a
    hand-copied approximation of this clause is how the four readers start
    disagreeing. `args` is appended to (params are positional) and must already
    hold the owner id as $1. `NEVER` is "never curated", which bounds nothing —
    the whole corpus is ahead, matching `has_changes_since` returning True for
    that case. `folder_id` narrows a folder-scoped curator's feed to its own
    folder; None is the workspace-wide reading.

    The canonical-row test is deliberately NOT here: only the feed may drop
    non-canonical copies from what it SHOWS, while the backlog still has to count
    them as raw rows. That term is `_canonical_rows`, spliced by the readers that
    mean it.

    `cursor_bound` is the clause the cursor takes. The feed wants the sharp form,
    `position_bound`, because it must not READ past the cursor; the backlog groups
    by identity and so scans from the cursor's instant (`instant_bound`) to keep
    each group's copies together, asking the sharp question per row instead."""
    where = f"he.owner_user_id = $1 {_CURATOR_FEED_ELIGIBILITY}{_wiki_event_scope(wiki)}"
    if folder_id is not None:
        where += _folder_event_scope(folder_id, args)
    where += cursor_bound(args, position)
    where += until_bound(args, until)
    return where


async def has_changes_since(
    owner_user_id: UUID,
    user_id: UUID,
    position: Position,
    wiki: str,
    folder_id: UUID | None = None,
    until: datetime | None = None,
) -> bool:
    """True if anything this wiki's curator cares about is ahead of `position`.

    A cheap gate — the beat task skips a curator run (and the sprite wake) when
    False. Its event clause carries the same scope AND the same cursor the feed
    does, which is what makes "the gate fired" mean "the feed has something to
    read". Every other clause stays owner-wide: they are filtered downstream, so
    they can only over-trigger, and tightening one clause without the others is
    exactly how the gate and the feed stop agreeing.

    `folder_id` scopes a folder-bound curator: then events are the whole gate —
    the scoped feed reads nothing else (the folder's wiki pages are the
    curator's artifact, excluded the way Memory is), so the page and
    owner-wide branches could only fire runs with nothing to read.

    The event half also splices the canonical-row test, and it did not used to. While
    the cursor was an instant that test was unnecessary here: every copy of an
    identity shares one `created_at`, so a group was either wholly inside or wholly
    outside the window. A cursor standing INSIDE a tie breaks that — the duplicates
    of an event already shown carry HIGHER ids, so they sit ahead of the cursor
    while their canonical twin sits behind it, and an EXISTS over bare rows would
    fire the beat forever on copies the feed will never offer. The test is cheap
    where it matters: the id/created_at walk stops at the first qualifying row
    rather than hashing the whole window. This is the equivalence this file exists
    to protect.

    `until` closes the event clause at a window's upper edge — spliced by the ONE
    clause builder `until_bound`, so a windowed gate excludes exactly the rows the
    windowed feed and the windowed backlog exclude. It bounds the event branch
    only: the page/file/drive/save branches ask `updated_at > <lower instant>` and
    a windowed gate can therefore over-fire on material newer than the window,
    never under-fire on material inside it — over-firing costs a dispatch that
    finds nothing to read, under-firing strands work, and the gate chooses the
    first. An unwindowed gate (`until=None`) is served exactly what it was served
    before windows existed."""
    if position.at is None:
        return True  # never curated → bootstrap.
    pool = get_pool()
    memory_ids = await files_tree_service.memory_subtree_folder_ids(owner_user_id)
    args: list = [owner_user_id]
    where = (
        f"he.owner_user_id = $1 {_CURATOR_FEED_ELIGIBILITY}{_wiki_event_scope(wiki)}"
        f"{position_bound(args, position)}{until_bound(args, until)} AND {_canonical_rows()}"
    )
    if folder_id is not None:
        where += _folder_event_scope(folder_id, args)
        branches = [f"EXISTS (SELECT 1 FROM history_events he WHERE {where})"]
    else:
        args.append(position.at)
        instant = len(args)
        args.append(list(memory_ids) or None)
        excluded = len(args)
        page_scope = (
            f"AND (${excluded}::uuid[] IS NULL "
            f"OR folder_id IS NULL OR folder_id <> ALL(${excluded}))"
        )
        branches = [
            f"EXISTS (SELECT 1 FROM history_events he WHERE {where})",
            f"EXISTS (SELECT 1 FROM pages WHERE owner_user_id = $1 "
            f"AND updated_at > ${instant} {page_scope})",
            f"EXISTS (SELECT 1 FROM files WHERE owner_user_id = $1 AND created_at > ${instant})",
            f"EXISTS (SELECT 1 FROM drive_documents WHERE owner_user_id = $1 AND updated_at > ${instant} "
            "AND extraction_status = 'done' AND deleted_at IS NULL)",
            f"EXISTS (SELECT 1 FROM x_save_docs WHERE owner_user_id = $1 AND updated_at > ${instant} "
            "AND hydration_status = 'done' AND deleted_at IS NULL)",
            f"EXISTS (SELECT 1 FROM instagram_save_docs WHERE owner_user_id = $1 "
            f"AND updated_at > ${instant} "
            "AND hydration_status = 'done' AND deleted_at IS NULL)",
        ]
    exists = await pool.fetchval(
        f"SELECT {' OR '.join(branches)}",
        *args,
        column=0,
    )
    return bool(exists)


def _project_share_wiki(event: dict) -> bool | None:
    """Whether the project this event is filed in is cleared for the shared wiki.

    None means "not filed in a project": no folder at all, or the scope's
    Default folder. Default is the catch-all for sessions nobody deliberately
    placed, so it carries no routing decision of its own — normalizing it here
    is the one place that rule lives, so the SQL stays a plain read.

    A non-null answer is therefore the single signal that this event belongs to
    a project, and whether that project's history may be distilled across users.
    """
    if event.get("session_folder_id") is None or event.get("session_folder_is_default"):
        return None
    return event["session_folder_share_wiki"]


async def changes_since(
    owner_user_id: UUID,
    user_id: UUID,
    position: Position,
    wiki: str,
    folder_id: UUID | None = None,
    until: datetime | None = None,
    *,
    max_event_chars: int | None = None,
) -> dict:
    """The delta the curator reads: history events, changed pages (excl. Memory),
    new files, changed Drive-folder documents, newly hydrated X/Instagram saves,
    and connected-source pointers.

    `max_event_chars` belongs to a history drain, and only to one: it cuts the event list
    to what one pass of that lane's wall can carry, by the same budget its commit cuts by
    (see `_budget_kept`). Without it the feed is the feed it has always been, bounded by
    `_MAX_EVENTS`, which is what an incremental batch has always reached its wall on.

    `wiki` scopes the events only — see `_feed_events`. Pages, files, saves, and
    source pointers come from the caller's own scope and are filtered
    downstream, so they stay owner-wide here.

    `folder_id` marks the folder-scoped curator: its work set is its folder's
    events and its folder's pages, nothing else — files, Drive documents, saves,
    and source pointers come back empty because a scoped curator has no wiki
    outside the folder to fold them into. The scoping is in SQL, not in prose:
    material outside the folder never reaches the scoped curator's prompt.

    `until` closes the EVENT half of the feed at a window's upper edge, through
    the one builder `until_bound` that every windowed reader splices. The page,
    file, save, and source sections keep their lower-bound-only reading: they
    report what changed since the caller's position, which for a windowed drain
    pass means nothing the pass may act on is missing — the tiling claims, and
    the tests, are about event rows excluding/including the same seam. An
    unbounded caller (`until=None`) is served the feed exactly as it was served
    before windows existed."""
    pool = get_pool()
    memory_ids = await files_tree_service.memory_subtree_folder_ids(owner_user_id)
    exclude = list(memory_ids) or None

    events, history_has_more = await _feed_events(
        owner_user_id, position, until, _MAX_EVENTS, wiki, folder_id
    )
    kept = _budget_kept(events, max_event_chars)
    if kept is not None:
        # The cut half stays pending rather than vanishing: the drain's next dispatch
        # reads it from the position this pass commits, and `history_has_more` is how the
        # feed says so without inventing a second account of what is left.
        events = events[:kept]
        history_has_more = True
    history = [
        {
            "session_id": e.get("session_id"),
            "agent_name": e.get("agent_name"),
            "event_type": e.get("event_type"),
            "content": (e.get("content") or "")[:_SNIPPET],
            "created_at": _iso(e.get("created_at")),
            "user": e.get("user"),
            "user_share_wiki": e.get("user_share_wiki"),
            "session_folder": e.get("session_folder"),
            "session_folder_share_wiki": _project_share_wiki(e),
        }
        for e in events
    ]

    if folder_id is not None:
        # A folder curator's own wiki is its compiled artifact, not input — the
        # same exclusion that keeps Memory out of the workspace feed. The run
        # reads the pages it maintains through ls/read before folding in.
        pages = []
    else:
        page_rows = await pool.fetch(
            """
            SELECT id, name, folder_id, updated_at,
                   left(coalesce(content_markdown, ''), $4) AS snippet
            FROM pages
            WHERE owner_user_id = $1
              AND ($5::uuid[] IS NULL OR folder_id IS NULL OR folder_id <> ALL($5))
              AND ($2::timestamptz IS NULL OR updated_at > $2)
            ORDER BY updated_at DESC LIMIT $3
            """,
            owner_user_id,
            position.at,
            _MAX_PAGES,
            _SNIPPET,
            exclude,
        )
        pages = [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "folder_id": str(r["folder_id"]) if r["folder_id"] else None,
                "updated_at": _iso(r["updated_at"]),
                "snippet": r["snippet"],
            }
            for r in page_rows
        ]

    if folder_id is None:
        files, source_docs, saves, sources = await _owner_wide_sections(
            pool, owner_user_id, user_id, position.at
        )
    else:
        files = source_docs = saves = sources = []

    return {
        "since": _iso(position.at),
        "counts": {
            "history": len(history),
            "pages": len(pages),
            "files": len(files),
            "source_docs": len(source_docs),
            "saves": len(saves),
            "sources": len(sources),
        },
        "history": history,
        "history_has_more": history_has_more,
        "pages": pages,
        "files": files,
        "source_docs": source_docs,
        "saves": saves,
        "sources": sources,
    }


async def _owner_wide_sections(pool, owner_user_id: UUID, user_id: UUID, since: datetime | None):
    """The delta halves only a workspace-wide curator reads: new files, changed
    Drive documents, newly hydrated saves, and connected-source pointers. A
    folder-scoped curator has no wiki outside its folder to fold them into, so
    they are not fetched for it at all."""
    file_rows = await pool.fetch(
        """
        SELECT id, name, created_at, left(coalesce(extracted_text, ''), $4) AS snippet
        FROM files
        WHERE owner_user_id = $1 AND ($2::timestamptz IS NULL OR created_at > $2)
        ORDER BY created_at DESC LIMIT $3
        """,
        owner_user_id,
        since,
        _MAX_FILES,
        _SNIPPET,
    )
    files = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "created_at": _iso(r["created_at"]),
            "snippet": r["snippet"],
        }
        for r in file_rows
    ]

    # Changed Drive-folder documents, as items rather than source pointers — a
    # picked Drive folder is the user's curated document set (edited outside
    # Stash), so an edit there is curation input the same way an upload is.
    # `updated_at` moves only on a real change: the sync upsert bumps it when
    # Drive's modifiedTime differs, and extraction bumps it when the new body
    # lands. Gating on 'done' presents a doc only once its text is readable.
    source_doc_rows = await pool.fetch(
        """
        SELECT path, name, updated_at, left(coalesce(content, ''), $4) AS snippet
        FROM drive_documents
        WHERE owner_user_id = $1
          AND ($2::timestamptz IS NULL OR updated_at > $2)
          AND extraction_status = 'done' AND deleted_at IS NULL
        ORDER BY updated_at DESC LIMIT $3
        """,
        owner_user_id,
        since,
        _MAX_SOURCE_DOCS,
        _SNIPPET,
    )
    source_docs = [
        {
            "path": r["path"],
            "name": r["name"],
            "updated_at": _iso(r["updated_at"]),
            "snippet": r["snippet"],
        }
        for r in source_doc_rows
    ]

    # Newly hydrated X/Instagram saves, as items rather than source pointers —
    # a save the user made is deliberate curation input, like an upload.
    save_rows = await pool.fetch(
        """
        SELECT source, kind, name, url, updated_at, snippet FROM (
            SELECT 'x' AS source, kind, name,
                   'https://x.com/i/status/' || external_ref AS url,
                   updated_at, left(coalesce(content, ''), $4) AS snippet
            FROM x_save_docs
            WHERE owner_user_id = $1
              AND ($2::timestamptz IS NULL OR updated_at > $2)
              AND hydration_status = 'done' AND deleted_at IS NULL
            UNION ALL
            SELECT 'instagram', kind, name,
                   'https://www.instagram.com/p/' || external_ref || '/',
                   updated_at, left(coalesce(content, ''), $4)
            FROM instagram_save_docs
            WHERE owner_user_id = $1
              AND ($2::timestamptz IS NULL OR updated_at > $2)
              AND hydration_status = 'done' AND deleted_at IS NULL
        ) all_saves
        ORDER BY updated_at DESC LIMIT $3
        """,
        owner_user_id,
        since,
        _MAX_SAVES,
        _SNIPPET,
    )
    saves = [
        {
            "source": r["source"],
            "kind": r["kind"],
            "name": r["name"],
            "url": r["url"],
            "updated_at": _iso(r["updated_at"]),
            "snippet": r["snippet"],
        }
        for r in save_rows
    ]

    all_sources = await source_service.list_sources(owner_user_id, user_id)
    sources = [
        {"source": s.get("source"), "type": s.get("type"), "display_name": s.get("display_name")}
        for s in all_sources
        if not str(s.get("type", "")).startswith("native_")
    ]
    return files, source_docs, saves, sources


async def _feed_events(
    owner_user_id: UUID,
    position: Position,
    until: datetime | None,
    limit: int,
    wiki: str,
    folder_id: UUID | None = None,
) -> tuple[list[dict], bool]:
    """The curator's event feed, oldest first. Returns (events, has_more).

    The curator's own run transcripts (`agent-curate-%` sessions) are excluded
    in SQL — feeding them back would echo-loop the daily gate and pollute the
    wiki, and filtering after the query would let them consume feed slots that
    belong to real activity.

    `wiki` decides which events are in the feed at all, in SQL: the shared wiki
    reads only sessions whose end user still shares it. Scoping here rather than
    downstream is what lets the feed, the gate, and `complete_through` tell one
    story, and it means an opted-out session cannot consume the slots a
    shareable session needs.

    The end user still rides along on every event that does reach the feed,
    because the curator's roster names people by what they share: the flag
    identifies the users whose material lands only in their own wiki, and it
    cannot be recovered once their sessions are absent from the feed.

    Events also carry the session's folder (name and id) or null — the personal
    curator attributes learning to that folder's context — plus that folder's
    shared-wiki clearance and whether it is the Default one, which the external
    curator routes the separate, per-project opt-in by.

    One row per event identity, not per insert (`_canonical_rows`), so a transcript
    re-pushed eight times buys the per-run budget once. Eligibility and sharing
    scope are applied *before* dedupe — never inside the scoping clause — so an
    out-of-scope row can never become the representative of anything the wiki then
    reads. A duplicate's twin is always eligible too (the identity carries
    `session_id`, so both copies share it).

    The id is projected as `event_id` because it is half of a position: when the
    feed saturates its budget inside one `created_at`, `complete_through` writes
    the last shown event's id as the curator's new position, and a feed that did
    not name the events it showed could not say where it stopped.

    The canonical test is an anti-join rather than `DISTINCT ON`, with the narrow
    id/created_at selection and the LIMIT pushed inside it, because the feed is the
    hot path. `DISTINCT ON` has to order the whole window by the identity before the
    oldest-first limit can apply: 1945ms and a 28MB disk spill on a founder-scale
    317k-row window, against 27ms end to end walking the owner+created_at index, the
    walk stopping once it has read the 724 rows that hold the budget. Only the
    `limit + 1` winners are re-joined for their transcript text."""
    pool = get_pool()
    args: list = [owner_user_id]
    where = _feed_conditions(wiki, position, until, args, folder_id)
    rows = await pool.fetch(
        f"SELECT picked.id AS event_id, he.session_id, he.agent_name, he.event_type, "
        f"he.content, he.created_at, "
        f"eu.name AS user, eu.share_wiki AS user_share_wiki, "
        f"sf.name AS session_folder, sf.id AS session_folder_id, "
        f"sf.share_wiki AS session_folder_share_wiki, "
        f"sf.is_default AS session_folder_is_default "
        f"FROM ( "
        f"  SELECT he.id, he.created_at "
        f"  FROM history_events he "
        f"  WHERE {where} "
        f"  AND {_canonical_rows()} "
        f"  ORDER BY he.created_at, he.id "
        f"  LIMIT {limit + 1} "
        f") picked "
        f"JOIN history_events he ON he.id = picked.id "
        f"LEFT JOIN sessions s ON s.owner_user_id = he.owner_user_id "
        f"  AND s.session_id = he.session_id "
        f"LEFT JOIN end_users eu ON eu.id = s.end_user_id "
        f"LEFT JOIN session_folders sf ON sf.id = s.session_folder_id "
        f"ORDER BY picked.created_at, picked.id",
        *args,
    )
    has_more = len(rows) > limit
    return [dict(r) for r in rows[:limit]], has_more


async def curator_event_backlog(
    owner_user_id: UUID,
    wiki: str,
    position: Position,
    folder_id: UUID | None = None,
    until: datetime | None = None,
) -> dict:
    """How many events this wiki's curator feed still has left to read.

    The figure counts history events in the curator's feed — the material a run
    is permitted to read. It deliberately excludes changed pages, new files,
    connected-source documents, and saves, so it must not be read as "all pending
    work"; it is the part of the backlog a run may read, which is the half that
    moves whichever position the lane is currently working from.

    Its WHERE clause is `_feed_conditions` — the feed's own scope clauses, not a
    hand-copied approximation of them — with the cursor taken as `instant_bound` and
    applied row by row by `row_ahead`, which is what makes both halves of the
    invariant true: `distinct_events` is zero exactly when the feed returns nothing,
    and no group it counts has a copy the feed would not have offered. The curator's
    `agent-curate-%` transcripts are in neither, because every successful run
    appends its transcript behind the position it just wrote: a backlog that
    counted those rows would grow with the work it is supposed to measure and
    could never report zero.

    `distinct_events` collapses identity groups, so a session re-pushed eight times
    contributes one event rather than eight, and `raw_rows` is reported beside it so
    the gap explains itself instead of reading as a bug. The scoping clause is why
    the two wikis disagree: the shared wiki only ever counts sessions of end users
    who still share it.

    One `GROUP BY` over the identity does the collapsing, carrying each group's
    pending flag and its ahead-of-the-cursor row count, so a single scan yields all
    three figures. It is not written as `count(DISTINCT (identity))` because that
    form sorts the whole window on a content-width key: 1053ms and a 54MB disk spill
    against 741ms and 24MB over a founder-scale window, and a tie (~12ms) once the
    curator is caught up.

    A group is pending when EVERY row of it is ahead of the position — the same claim
    as 'its canonical row is ahead', because the canonical row is the group's lowest
    id, every copy shares the group's instant, and one copy left behind the cursor
    drags the whole group behind with it. The two figures still differ: `raw_rows`
    counts rows that are individually ahead, so the copies an already-shown event
    carries are reported there and nowhere else — not work, but exactly the
    amplification this pair exists to expose.

    The scan therefore bounds on the cursor's INSTANT (`instant_bound`) and leaves
    the sharp cursor to `row_ahead`, asked per row inside the aggregates: the wider
    scan is what keeps each group's copies in one read, and the per-row question is
    what keeps `raw_rows` honest about rows it reports as no work. Splicing the
    feed's per-row anti-join `_canonical_rows()` here instead — this query's first
    shape — measured 3.3–6.5s over a 50k-row window holding 10k identities of which
    2k shared one instant, because every probe re-walks its own tie (16.8M buffer
    hits), against 114–163ms for this one scan; with the cursor standing mid-tie, 2.2s
    against 18ms. The pair clause itself costs nothing: 48–55ms with it over that
    window, against 83–97ms over the same window with the instant cursor this task
    replaced.

    `sum` answers NULL over a window holding no groups while `count` answers 0, so
    the sum is coalesced: a drained backlog reads as the same shape as a full one.

    `position` is the instant the caller chose to count from, and the figures are
    relative to it and to nothing else. A readout asks at the position its lane is
    live on — the drain's own while a drain runs, via `readout_position_of` — while
    the feed route asks at the position the request named. `NEVER` means nothing has
    been read, so the whole corpus is still ahead (same reading as
    `has_changes_since`).

    `until` closes the count at a window's upper edge — the feed's own
    `until_bound` spliced through `_feed_conditions`, the same single clause the
    feed and the gate use, so a windowed figure and a windowed feed cannot count
    different rows. An identity group shares one `created_at`, so the bound either
    admits a whole group or drops the whole group: no group is counted half.
    `until=None` counts to now, as this figure always did."""
    pool = get_pool()
    args: list = [owner_user_id]
    where = _feed_conditions(wiki, position, until, args, folder_id, cursor_bound=instant_bound)
    ahead = row_ahead(args, position, "he.created_at", "he.id")
    row = await pool.fetchrow(
        f"SELECT count(*) FILTER (WHERE all_ahead) AS distinct_events, "
        f"coalesce(sum(rows_ahead), 0)::bigint AS raw_rows, "
        f"count(DISTINCT session_id) FILTER (WHERE all_ahead) AS distinct_sessions "
        f"FROM ( "
        f"  SELECT he.session_id, "
        f"  bool_and({ahead}) AS all_ahead, "
        f"  count(*) FILTER (WHERE {ahead}) AS rows_ahead "
        f"  FROM history_events he "
        f"  WHERE {where} "
        f"  GROUP BY {_event_identity('he')} "
        f") grouped",
        *args,
    )
    return {
        "distinct_events": row["distinct_events"],
        "raw_rows": row["raw_rows"],
        "distinct_sessions": row["distinct_sessions"],
    }


async def curator_readout(
    owner_user_id: UUID, agent: dict, *, folder_id: UUID | None = None
) -> dict:
    """What one curator still has to read, counted from the position that is live on it.

    `curator_event_backlog` untouched, plus the two keys that make its answer readable
    rather than merely numeric. `read_from` is the instant the count was taken from and
    `draining` says whether that instant belongs to a history drain rather than to the
    nightly watermark. They exist because the backlog alone cannot say WHICH instant it
    was measured from: a page rendering it beside `curated_through` would be showing a
    number from one position next to an instant from the other, and a client left to pick
    between the two columns would be the second counter this file refuses to have.

    `read_from` is the datetime itself, so FastAPI serialises it the way it already
    serialises `curated_through` in the same payload; the two keys are added to an
    existing route rather than publishing a second numeral to be reconciled.
    """
    position = readout_position_of(agent)
    backlog = await curator_event_backlog(owner_user_id, agent["curator_wiki"], position, folder_id)
    return {**backlog, "read_from": position.at, "draining": drain_of(agent) is not None}


async def complete_through(
    owner_user_id: UUID,
    position: Position,
    until: datetime,
    wiki: str,
    folder_id: UUID | None = None,
    *,
    max_event_chars: int | None = None,
) -> Position:
    """Where the curator's watermark may stand after one successful run.

    The feed is complete through `until` unless it overflowed _MAX_EVENTS or the
    optional event-text budget, in which case it is complete through exactly the
    last event that fit: `Position(T_k, id_k)`, standing INSIDE the saturated
    instant rather than a microsecond before
    it. The old `-1µs` step is what made a tie a plateau — it landed behind the
    whole tie, so every run re-read the same first budget and the backlog never
    drained (STAS-235). A result is therefore always strictly ahead of the position
    it was read from: with no overflow the whole instant `until` is behind, which is
    ahead of any mid-tie position inside that instant; with overflow the event id
    advances within the instant.

    `wiki` must be the value the run's feed was read with: this decides how far
    that run's watermark may move, so a wider scope here would let the watermark
    step past events the curator was never shown. `folder_id` likewise must be
    the folder the scoped feed was cut by.

    `max_event_chars` is the same budget the feed was cut by — pass it and the two
    agree by construction, because both cuts are `_budget_kept` over the same rows.
    Omit it and the position is bounded by `_MAX_EVENTS` alone, which is the shape
    every incremental run has always had."""
    events, has_more = await _feed_events(
        owner_user_id, position, until, _MAX_EVENTS, wiki, folder_id
    )
    cut = _budget_kept(events, max_event_chars)
    if cut is not None:
        last = events[cut - 1]
        return Position(at=last["created_at"], event_id=last["event_id"])
    if not has_more:
        return Position(at=until)
    return Position(at=events[-1]["created_at"], event_id=events[-1]["event_id"])


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None
