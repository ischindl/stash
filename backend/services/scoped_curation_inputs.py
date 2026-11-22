"""Bounded, resumable input for all developer-curator source types.

The timestamp watermark never passes a partly read document. A cursor records
completed siblings and character offsets at that timestamp, so even one huge
file or a large group of simultaneous events can make progress across runs.

The `since` a caller hands here is the row a batch starts AT, and it is not always the
lane's live watermark: a descending lane hands this loader one bounded window's `from` and
that window's closed edge as `until`, so a pass reads its cell and no further. The cursor
below still carries a partly read document across that boundary — the window bounds what a
pass may consume, not what a resumable read remembers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from ..database import get_pool
from . import curation_service

# The loader's quantum: it cannot build a batch below this many characters, so it is
# also the cheapest slice a budget can meaningfully hand it. `input_budget` prices it
# in wall-seconds and refuses any wall that cannot pay for it.
MINIMUM_BATCH_CHARS = 1024
# The smallest slice of ONE document this loader will start. It is not the same fact as
# the batch floor above, and applying that floor per document is what made a pass carry
# one document: a 1,455-character grant spent its first document and could not start a
# second with the 376 characters left, so the pass's fixed cost of 4.7-13.9 seconds of
# model turn was amortised over a single file every time.
#
# 512 is bounded by the history envelope, not chosen by taste: `render_event` wraps an
# `event` row's slice in JSON keys, and the widest `event_type`/`tool_name` the schema
# allows (VARCHAR(64)/VARCHAR(128), 0001_initial_schema) render about 335 characters
# with no content at all. A slice below that bound binary-searches to zero characters,
# the offset never moves, and the cursor cannot advance.
#
# The law this obeys: the granted budget is an UPPER bound on a batch. Lowering the
# fill floor can only let more documents ride one grant, never let a batch exceed it.
MINIMUM_DOCUMENT_CHARS = 512


# What "eligible input" means, written ONCE. `load_batch` interpolates this text and the
# four existence questions this module is asked — the descent's door, the pending gate,
# a window's empty-window check, and a corpus's oldest instant — read the same fragment.
# That is not tidiness: each of those questions used to be its own copy of the UNION, and
# a copy that drifts answers "nothing pending" about rows the loader would happily read.
#
# The body references `$1` (the owner) and nothing else, which is what lets every caller
# number its own outer predicate `$2..` without renumbering this one. The classification of
# a row as end-user or internal is deliberately NOT here: it is the outer
# `((end_user_id IS NULL)=$k)`, so a probe can only ever count rows the lane may read.
#
# Keep the `agent-curate-%` exclusion in mind when editing: it is what stops the curator
# from curating its own wiki writes back into its own feed.
_INPUTS_CTE = """inputs AS (
                SELECT 'event' AS kind, he.id, he.created_at AS stamp,
                    'event:'||he.id::text AS key, 'session:'||he.session_id AS document_id,
                    he.session_id AS title, s.end_user_id, he.event_type, he.tool_name,
                    length(coalesce(he.content,'')) AS characters
                FROM history_events he JOIN sessions s ON s.owner_user_id=he.owner_user_id
                    AND s.session_id=he.session_id
                WHERE he.owner_user_id=$1 AND s.deleted_at IS NULL
                    AND he.session_id NOT LIKE 'agent-curate-%'
                UNION ALL
                SELECT 'file', f.id, f.created_at,
                    'file:'||f.id::text||':'||md5(f.extracted_text), 'file:'||f.id::text,
                    f.name, f.end_user_id, NULL, NULL, length(f.extracted_text)
                FROM files f WHERE f.owner_user_id=$1 AND f.deleted_at IS NULL
                    AND f.extraction_status='done' AND f.extracted_text IS NOT NULL
                UNION ALL
                SELECT 'source', d.id, d.updated_at,
                    'source:'||d.id::text||':'||md5(d.content), 'source:'||d.id::text,
                    d.name, s.end_user_id, NULL, NULL, length(d.content)
                FROM drive_documents d JOIN user_sources s ON s.id=d.source_id
                WHERE d.owner_user_id=$1 AND s.owner_user_id=$1 AND d.deleted_at IS NULL
                    AND d.extraction_status='done' AND d.content IS NOT NULL
            )"""


def render_event(row, chunk: str, offset: int) -> str:
    """The envelope a slice of one history event is handed to the model in.

    A file or drive-document slice is handed over bare -- only history events carry
    the envelope -- and the caller does not branch, so the kind test lives here.

    Module-level rather than a closure in the fill loop so the envelope's own size is
    testable: `MINIMUM_DOCUMENT_CHARS` is chosen against this rendering, and a bound on
    a private closure is not a bound anyone can check.
    """
    if row["kind"] != "event":
        return chunk
    return (
        json.dumps(
            {
                "event_type": row["event_type"],
                "tool_name": row["tool_name"],
                "content": chunk,
                "created_at": row["stamp"].isoformat(),
                "offset": offset,
                "total_characters": row["characters"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )


@dataclass
class Batch:
    documents: list[dict]
    through: datetime
    cursor: dict


async def load_batch(
    owner: UUID,
    since,
    until,
    *,
    internal: bool,
    generation: int,
    cursor: dict | None = None,
    max_chars: int,
    max_items: int | None = None,
) -> Batch:
    if max_chars < MINIMUM_DOCUMENT_CHARS:
        # Answering with an empty batch here would be read two ways, both wrong: `run`
        # raises "raise the run ceiling" over an empty batch that carries a cursor, and
        # `has_pending` reads no documents as a lane with nothing pending and puts it to
        # sleep. A budget this small is a caller bug and says so.
        raise ValueError(
            f"a curation batch budget of {max_chars} characters cannot start a document: "
            f"the smallest slice this loader writes is {MINIMUM_DOCUMENT_CHARS} characters"
        )
    baseline = since.isoformat() if since else None
    state = cursor or {}
    if state.get("since") != baseline or state.get("generation") != generation:
        state = {}
    documents = []
    done = list(state.get("done", []))
    partial = dict(state.get("partial", {}))
    remaining = max_chars
    limit = curation_service._MAX_EVENTS if max_items is None else max_items
    # Metadata only: never transfer an unbounded extracted document to the worker.
    async with get_pool().acquire() as conn, conn.transaction(isolation="repeatable_read"):
        rows = await conn.fetch(
            f"""
            WITH {_INPUTS_CTE}
            SELECT * FROM inputs WHERE ($2::timestamptz IS NULL OR stamp>$2) AND stamp<=$3
                AND ((end_user_id IS NULL)=$4) AND key<>ALL($5::text[])
            ORDER BY stamp,kind,id LIMIT $6
            """,
            owner,
            since,
            until,
            internal,
            done,
            limit + 1,
        )
        completed = []
        pending_stamp = None
        for index, row in enumerate(rows):
            if index >= limit or remaining < MINIMUM_DOCUMENT_CHARS:
                pending_stamp = row["stamp"]
                break
            offset = int(partial.get(row["key"], 0))
            table, column = {
                "event": ("history_events", "content"),
                "file": ("files", "extracted_text"),
                "source": ("drive_documents", "content"),
            }[row["kind"]]
            text = await conn.fetchval(
                f"SELECT substring(coalesce({column},'') FROM $2::integer FOR $3::integer) FROM {table} WHERE id=$1",
                row["id"],
                offset + 1,
                remaining,
            )

            # History envelopes count too. Binary-search the largest complete
            # JSON fragment fitting the remaining budget; never cut JSON bytes.
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(render_event(row, text[:mid], offset)) <= remaining:
                    lo = mid
                else:
                    hi = mid - 1
            content = render_event(row, text[:lo], offset)
            documents.append(
                {**dict(row), "content": content, "offset": offset, "next_offset": offset + lo}
            )
            remaining -= len(content)
            if offset + lo < row["characters"]:
                partial[row["key"]] = offset + lo
                pending_stamp = row["stamp"]
                break
            partial.pop(row["key"], None)
            completed.append(row)
        if pending_stamp is None:
            return Batch(documents, until, {})
        through = pending_stamp - timedelta(microseconds=1)
        # Old completed siblings are relevant only while the watermark stays
        # at their timestamp; newer timestamps start with an empty sibling set.
        if since != through:
            done = []
            partial = {
                r["key"]: partial[r["key"]]
                for r in rows
                if r["stamp"] == pending_stamp and r["key"] in partial
            }
        done.extend(r["key"] for r in completed if r["stamp"] == pending_stamp)
        return Batch(
            documents,
            through,
            {
                "since": through.isoformat(),
                "generation": generation,
                "done": done,
                "partial": partial,
            },
        )


async def has_input(owner: UUID, *, internal: bool, after: datetime | None, top: datetime) -> bool:
    """Whether ANY eligible input stands in `(after, top]` for this lane — one bounded EXISTS.

    This is the one source every existence question asks, because "is there input" has
    exactly one definition and it is `load_batch`'s: the same `_INPUTS_CTE`, the same
    `stamp > after` (strictly) and `stamp <= top` (inclusive) as the loader's `since` and
    `until`, and the same `((end_user_id IS NULL)=$k)` classification, so a probe can never
    count a row the lane would not be allowed to read. The two operators are the loader's
    own and may not be tidied here either: they are what makes one row belong to exactly one
    window when a span is cut into cells.

    A `SELECT EXISTS`, deliberately, and never a `load_batch` call with a small budget:
    `load_batch` builds a batch, binary-searches a render for every row it fetched, and
    opens a `repeatable_read` transaction to do it. A gate that asks the question four
    times a day must not pay for an answer it throws away.

    `after` may be None, which is the loader's own "read from the beginning of the feed"
    and is what a lane that has never run is asked about.
    """
    return await get_pool().fetchval(
        f"""
        WITH {_INPUTS_CTE}
        SELECT EXISTS (
            SELECT 1 FROM inputs
            WHERE ($2::timestamptz IS NULL OR stamp>$2) AND stamp<=$3 AND ((end_user_id IS NULL)=$4)
        )
        """,
        owner,
        after,
        top,
        internal,
    )


async def oldest_input(owner: UUID, *, internal: bool) -> datetime | None:
    """The oldest eligible input's instant, or None when the lane has no input at all.

    This is a descent's floor for the one lane that can have no watermark: a lane that never
    ran has no `curated_through` to stand behind, and its floor is this instant minus one
    microsecond, because the loader reads strictly after a window's `from` and a floor naming
    a real row would strand that row in no window at all.

    Asked over the same fragment, so the floor is an instant the lane can actually read.
    """
    return await get_pool().fetchval(
        f"WITH {_INPUTS_CTE} SELECT min(stamp) FROM inputs WHERE ((end_user_id IS NULL)=$2)",
        owner,
        internal,
    )


async def has_pending(agent: dict, workspace: dict, *, until: datetime | None = None) -> bool:
    """Only eligible source input, never the curator's own wiki writes.

    Asked of the pair the curator would actually read next, which is its drain pair while a
    history drain is in flight. A gate that consulted the incremental watermark instead
    would call a lane mid-drain caught up — or, from the other side, would see the gap
    between an old watermark and `now` as fresh input and keep a caught-up lane awake.

    A DESCENDING plan is asked differently, and the difference is which ground is still OWED
    rather than how much input exists: an open window, an un-cut cell between the floor and the
    frontier, or fresh material above the plan's top. The consequence worth naming is the quiet
    one — a lane that has published every window it cut, has no fresh input, and therefore
    answers false here takes the beat's free `no_changes` skip and defers its hand-off to the
    next run with a reason to fire. Nothing is un-curated in that state and nothing re-reads it:
    the drain column still names the published edge for every reader, and `finish_drain` runs on
    the first dispatch that has any input to read, which retires the pair before it works a cell.
    Spending a metered run per tick to clear a cursor is the same spend this gate exists to refuse.
    """
    from datetime import UTC

    from . import curation_service

    resolved = until or datetime.now(UTC)

    drain = curation_service.drain_of(agent)
    if drain is not None and curation_service.drain_descends(drain[1]):
        # A descending lane cannot be asked this way. `load_batch` walks ASCENDING from `since`,
        # and a descent's `since` is the committed boundary of its newest open window, which
        # sits a whole cell below the seam: the rows between that boundary and `now` are the
        # fresh material this card exists to reach, and an ascending probe from the boundary
        # steps over them on its way to `until` only after pricing a batch for them. What this
        # gate owes the beat is whether any ground is still OWED, and owed ground is exactly
        # three bands, asked cheapest first.
        #
        # Reading a row does not make it ineligible — `has_input` is the loader's own UNION and
        # keeps no record of what a committed pass published — so this may ask only ground the
        # plan has not already read. Ask it the plan's whole span `(floor, plan top]` and it asks
        # the band the descent exists to read and prune: the first published window makes it true
        # for the rest of the plan's life, the beat then spends a metered run on every due tick
        # to read nothing, and the monthly allowance is gone long before the backlog is.
        cursor = drain[1]
        plan = cursor["plan"]
        owner = UUID(str(agent["user_id"]))
        internal = agent["curator_wiki"] == "internal"
        if curation_service.newest_open_part(cursor) is not None:
            # A standing slot IS owed work, and needs no EXISTS to say so. A pass that stopped
            # mid-window committed a boundary strictly below its cell's closed edge — the loader
            # stops at a row boundary, so rows remain above it — and a pass that reached that
            # edge pruned the slot in the same transaction as that commit. An open slot with
            # nothing left in it is therefore a state this lane never leaves behind.
            return True
        if await has_input(
            owner,
            internal=internal,
            after=curation_service.slot_instant(plan, "floor"),
            top=curation_service.slot_instant(plan, "descent_to"),
        ):
            # Ground between the floor and the frontier: inside the descent's promise, below
            # anything any cutter has opened. This is the backlog the next cells are cut for.
            return True
        # Above the plan top is the fresh material no cell of this plan claims, and the head
        # cell that will claim it is cut by the next run. A descending lane that answered false
        # here would go to sleep on exactly the input this card was written to reach.
        return await has_input(
            owner, internal=internal, after=curation_service.drain_plan_top(cursor), top=resolved
        )

    since, cursor = curation_service.read_position_of(agent)
    batch = await load_batch(
        UUID(str(agent["user_id"])),
        since,
        resolved,
        internal=agent["curator_wiki"] == "internal",
        generation=workspace["curation_generation"],
        cursor=cursor,
        max_chars=MINIMUM_BATCH_CHARS,
        max_items=1,
    )
    return bool(batch.documents)
