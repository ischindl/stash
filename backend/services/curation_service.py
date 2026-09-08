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
clause in `_CURATOR_FEED_ELIGIBILITY`, and collapse copies with the one identity
in `_event_identity`. A second near-identical query would let gate and feed
disagree, which is the failure this file exists to make impossible: the gate says
there is work, the feed and the watermark boundary read exactly that work, and
the backlog reports exactly what is left of it — so the backlog reads zero
precisely when the feed comes back empty.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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

# An event reaches the shared, anonymized wiki only through the end user its
# session belongs to — the privacy rule the prompt used to be trusted to
# enforce. The fragment names `he`, the `history_events` alias every reader
# gives it, so the SAME text is spliced into the feed, the watermark advance,
# and the beat's gate. That sharing is the point: gate-true has to mean
# feed-non-empty, and three separately-derived predicates drift — the beat
# dispatches a curator whose feed is empty (a sprite wake and a metered run
# burned on nothing) or stays silent while the feed has work.
#
# An event the wiki cannot attribute to a sharing end user — a session with no
# end user, or an event whose session row is gone — does not reach it. The
# owner's own wiki filters nothing: it is the owner's own memory.
_SHARE_WIKI_EVENT_SCOPE = (
    "AND EXISTS (SELECT 1 FROM sessions wse "
    "JOIN end_users we ON we.id = wse.end_user_id "
    "WHERE wse.owner_user_id = he.owner_user_id "
    "AND wse.session_id = he.session_id "
    "AND we.share_wiki)"
)

# What the curator is permitted to read, as one clause. Its own run transcripts
# (`agent-curate-%` sessions) are the one thing an owner-scoped feed must never
# show: they would echo-loop the daily gate and pollute the wiki, and filtering
# them after the query would let them consume feed slots that belong to real
# activity. The feed, the beat's gate, the watermark boundary, and the backlog
# all splice this one text, because "the gate fired" has to mean "the feed has
# work", and a backlog that counted these rows could never reach zero — every
# successful run appends its own transcript behind the position it just wrote.
_CURATOR_FEED_ELIGIBILITY = "AND (he.session_id IS NULL OR he.session_id NOT LIKE 'agent-curate-%')"

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
    since: datetime | None,
    until: datetime | None,
    args: list,
    folder_id: UUID | None = None,
) -> str:
    """Build the one WHERE clause that says what one wiki's curator may read.

    Owner scope, the eligibility clause, the sharing scope, the folder scope,
    and the time window, in one place: the feed, the watermark boundary, and
    the backlog are the same question asked three ways, and a hand-copied
    approximation of this clause is how the four readers start disagreeing.
    `args` is appended to (params are positional) and must already hold the
    owner id as $1. `since=None` is "never curated", which bounds nothing — the
    whole corpus is ahead, matching `has_changes_since` returning True for that
    case. `folder_id` narrows a folder-scoped curator's feed to its own folder;
    None is the workspace-wide reading.
    """
    where = f"he.owner_user_id = $1 {_CURATOR_FEED_ELIGIBILITY}{_wiki_event_scope(wiki)}"
    if folder_id is not None:
        where += _folder_event_scope(folder_id, args)
    if since is not None:
        args.append(since)
        where += f" AND he.created_at > ${len(args)}"
    if until is not None:
        args.append(until)
        where += f" AND he.created_at <= ${len(args)}"
    return where


async def has_changes_since(
    owner_user_id: UUID,
    user_id: UUID,
    since: datetime | None,
    wiki: str,
    folder_id: UUID | None = None,
) -> bool:
    """True if anything this wiki's curator cares about changed after `since`.

    A cheap gate — the beat task skips a curator run (and the sprite wake) when
    False. Its event clause carries the same scope the feed does, which is what
    makes "the gate fired" mean "the feed has something to read". Every other
    clause stays owner-wide: they are filtered downstream, so they can only
    over-trigger, and tightening one clause without the others is exactly how
    the gate and the feed stop agreeing.

    `folder_id` scopes a folder-bound curator: then events are the whole gate —
    the scoped feed reads nothing else (the folder's wiki pages are the
    curator's artifact, excluded the way Memory is), so the page and
    owner-wide branches could only fire runs with nothing to read.

    The event half stays an EXISTS over *rows* even though the feed now collapses
    duplicates: every row in an identity group shares one `created_at`, so a
    group is either entirely inside the window or entirely outside it. Collapsing
    copies can therefore never empty a window that had rows, and can never fill
    an empty one — "gate fired ⇔ feed has work" survives dedupe without putting
    the identity into the gate. Keeping it an EXISTS is also why the gate stays
    cheap: it stops at the first row instead of hashing and grouping the window.
    This is the equivalence this file exists to protect."""
    if since is None:
        return True  # never curated → bootstrap.
    pool = get_pool()
    memory_ids = await files_tree_service.memory_subtree_folder_ids(owner_user_id)
    args: list = [owner_user_id, since]
    where = (
        f"he.owner_user_id = $1 AND he.created_at > $2 {_CURATOR_FEED_ELIGIBILITY}"
        f"{_wiki_event_scope(wiki)}"
    )
    if folder_id is not None:
        where += _folder_event_scope(folder_id, args)
        branches = [f"EXISTS (SELECT 1 FROM history_events he WHERE {where})"]
    else:
        page_scope = "AND ($3::uuid[] IS NULL OR folder_id IS NULL OR folder_id <> ALL($3))"
        args.append(list(memory_ids) or None)
        branches = [
            f"EXISTS (SELECT 1 FROM history_events he WHERE {where})",
            f"EXISTS (SELECT 1 FROM pages WHERE owner_user_id = $1 AND updated_at > $2 {page_scope})",
            "EXISTS (SELECT 1 FROM files WHERE owner_user_id = $1 AND created_at > $2)",
            "EXISTS (SELECT 1 FROM drive_documents WHERE owner_user_id = $1 AND updated_at > $2 "
            "AND extraction_status = 'done' AND deleted_at IS NULL)",
            "EXISTS (SELECT 1 FROM x_save_docs WHERE owner_user_id = $1 AND updated_at > $2 "
            "AND hydration_status = 'done' AND deleted_at IS NULL)",
            "EXISTS (SELECT 1 FROM instagram_save_docs WHERE owner_user_id = $1 AND updated_at > $2 "
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
    since: datetime | None,
    wiki: str,
    folder_id: UUID | None = None,
) -> dict:
    """The delta the curator reads: history events, changed pages (excl. Memory),
    new files, changed Drive-folder documents, newly hydrated X/Instagram saves,
    and connected-source pointers.

    `wiki` scopes the events only — see `_feed_events`. Pages, files, saves, and
    source pointers come from the caller's own scope and are filtered
    downstream, so they stay owner-wide here.

    `folder_id` marks the folder-scoped curator: its work set is its folder's
    events and its folder's pages, nothing else — files, Drive documents, saves,
    and source pointers come back empty because a scoped curator has no wiki
    outside the folder to fold them into. The scoping is in SQL, not in prose:
    material outside the folder never reaches the scoped curator's prompt."""
    pool = get_pool()
    memory_ids = await files_tree_service.memory_subtree_folder_ids(owner_user_id)
    exclude = list(memory_ids) or None

    events, history_has_more = await _feed_events(
        owner_user_id, since, None, _MAX_EVENTS, wiki, folder_id
    )
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
            since,
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
            pool, owner_user_id, user_id, since
        )
    else:
        files = source_docs = saves = sources = []

    return {
        "since": _iso(since),
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
    since: datetime | None,
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

    One row per event identity, not per insert: a row is canonical when no lower
    `id` carries the same identity, so a transcript re-pushed eight times buys the
    per-run budget once. Eligibility and sharing scope are applied *before* dedupe
    — never inside the scoping clause — so an out-of-scope row can never become the
    representative of anything the wiki then reads. A duplicate's twin is always
    eligible too (the identity carries `session_id`, so both copies share it) and
    deliberately carries no watermark bound: a twin older than the position means
    this row was already deliverable then, which is exactly why it is not offered
    again now.

    The canonical test is an anti-join rather than `DISTINCT ON`, with the narrow
    id/created_at selection and the LIMIT pushed inside it, because the feed is the
    hot path. `DISTINCT ON` has to order the whole window by the identity before the
    oldest-first limit can apply: 1945ms and a 28MB disk spill on a founder-scale
    317k-row window, against 27ms end to end walking the owner+created_at index, the
    walk stopping once it has read the 724 rows that hold the budget. Only the
    `limit + 1` winners are re-joined for their transcript text."""
    pool = get_pool()
    args: list = [owner_user_id]
    where = _feed_conditions(wiki, since, until, args, folder_id)
    rows = await pool.fetch(
        f"SELECT he.session_id, he.agent_name, he.event_type, he.content, he.created_at, "
        f"eu.name AS user, eu.share_wiki AS user_share_wiki, "
        f"sf.name AS session_folder, sf.id AS session_folder_id, "
        f"sf.share_wiki AS session_folder_share_wiki, "
        f"sf.is_default AS session_folder_is_default "
        f"FROM ( "
        f"  SELECT he.id, he.created_at "
        f"  FROM history_events he "
        f"  WHERE {where} "
        f"  AND NOT EXISTS ( "
        f"    SELECT 1 FROM history_events dup "
        f"    WHERE dup.owner_user_id = he.owner_user_id "
        f"      AND ({_event_identity('dup')}) = ({_event_identity('he')}) "
        f"      AND dup.id < he.id) "
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
    owner_user_id: UUID, wiki: str, position: datetime | None, folder_id: UUID | None = None
) -> dict:
    """How many events this wiki's curator feed still has left to read.

    The figure counts history events in the curator's feed — the material a run
    is permitted to read. It deliberately excludes changed pages, new files,
    connected-source documents, and saves, so it must not be read as "all pending
    work"; it is the part of the backlog that moves the watermark.

    Its WHERE clause is `_feed_conditions` — the feed's own clause, not a hand-
    copied approximation of it — which is what makes both halves of the invariant
    true: `distinct_events` is zero exactly when the feed returns nothing, and it
    never counts a row the feed could not have shown. The curator's own
    `agent-curate-%` transcripts are in neither, because every successful run
    appends its transcript behind the position it just wrote: a backlog that
    counted those rows would grow with the work it is supposed to measure and
    could never report zero.

    `distinct_events` collapses identity groups, so a session re-pushed eight times
    contributes one event rather than eight, and `raw_rows` is reported beside it so
    the gap explains itself instead of reading as a bug. The scoping clause is why
    the two wikis disagree: the shared wiki only ever counts sessions of end users
    who still share it.

    One `GROUP BY` over the identity does the collapsing, carrying each group's size
    so a single scan yields distinct events and raw rows together. It is not written
    as `count(DISTINCT (identity))` because that form sorts the whole window on a
    content-width key: 1053ms and a 54MB disk spill against 741ms and 24MB over a
    founder-scale window, and a tie (~12ms) once the curator is caught up.

    `sum` answers NULL over a window holding no groups while `count` answers 0, so
    the sum is coalesced: a drained backlog reads as the same shape as a full one.

    `position` is the curator's current watermark; None means it has never run, so
    the whole corpus is still ahead (same reading as `has_changes_since`)."""
    pool = get_pool()
    args: list = [owner_user_id]
    where = _feed_conditions(wiki, position, None, args, folder_id)
    row = await pool.fetchrow(
        f"SELECT count(*) AS distinct_events, "
        f"coalesce(sum(group_rows), 0)::bigint AS raw_rows, "
        f"count(DISTINCT session_id) AS distinct_sessions "
        f"FROM ( "
        f"  SELECT he.session_id, count(*) AS group_rows "
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


async def complete_through(
    owner_user_id: UUID,
    since: datetime | None,
    until: datetime,
    wiki: str,
    folder_id: UUID | None = None,
) -> datetime:
    """How far the curator's watermark may advance after a successful run.

    The feed is complete through `until` unless it overflowed _MAX_EVENTS, in
    which case it is only complete through the last event that fit — minus a
    microsecond, so events sharing that exact timestamp are re-presented next
    run rather than skipped. Overflow therefore drains run by run and no event
    is ever silently dropped from curation.

    `wiki` must be the value the run's feed was read with: this decides how far
    that run's watermark may move, so a wider scope here would let the watermark
    step past events the curator was never shown. `folder_id` likewise must be
    the folder the scoped feed was cut by."""
    events, has_more = await _feed_events(owner_user_id, since, until, _MAX_EVENTS, wiki, folder_id)
    if not has_more:
        return until
    return events[-1]["created_at"] - timedelta(microseconds=1)


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None
