"""Bounded, resumable input for all developer-curator source types.

The timestamp watermark never passes a partly read document. A cursor records
completed siblings and character offsets at that timestamp, so even one huge
file or a large group of simultaneous events can make progress across runs.
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
            """
            WITH inputs AS (
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
            )
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


async def has_pending(agent: dict, workspace: dict, *, until: datetime | None = None) -> bool:
    """Only eligible source input, never the curator's own wiki writes.

    Asked of the pair the curator would actually read next, which is its drain pair while a
    history drain is in flight. A gate that consulted the incremental watermark instead
    would call a lane mid-drain caught up — or, from the other side, would see the gap
    between an old watermark and `now` as fresh input and keep a caught-up lane awake.
    """
    from datetime import UTC

    from . import curation_service

    since, cursor = curation_service.read_position_of(agent)
    batch = await load_batch(
        UUID(str(agent["user_id"])),
        since,
        until or datetime.now(UTC),
        internal=agent["curator_wiki"] == "internal",
        generation=workspace["curation_generation"],
        cursor=cursor,
        max_chars=MINIMUM_BATCH_CHARS,
        max_items=1,
    )
    return bool(batch.documents)
