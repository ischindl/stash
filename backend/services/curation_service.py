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
from datetime import datetime
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
# migration 0219 backfills nothing.
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
    if until is not None:
        args.append(until)
        where += f" AND he.created_at <= ${len(args)}"
    return where


async def has_changes_since(
    owner_user_id: UUID,
    user_id: UUID,
    position: Position,
    wiki: str,
    folder_id: UUID | None = None,
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
    to protect."""
    if position.at is None:
        return True  # never curated → bootstrap.
    pool = get_pool()
    memory_ids = await files_tree_service.memory_subtree_folder_ids(owner_user_id)
    args: list = [owner_user_id]
    where = (
        f"he.owner_user_id = $1 {_CURATOR_FEED_ELIGIBILITY}{_wiki_event_scope(wiki)}"
        f"{position_bound(args, position)} AND {_canonical_rows()}"
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
        owner_user_id, position, None, _MAX_EVENTS, wiki, folder_id
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
    owner_user_id: UUID, wiki: str, position: Position, folder_id: UUID | None = None
) -> dict:
    """How many events this wiki's curator feed still has left to read.

    The figure counts history events in the curator's feed — the material a run
    is permitted to read. It deliberately excludes changed pages, new files,
    connected-source documents, and saves, so it must not be read as "all pending
    work"; it is the part of the backlog that moves the watermark.

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

    `position` is the curator's current position; `NEVER` means it has never run, so
    the whole corpus is still ahead (same reading as `has_changes_since`)."""
    pool = get_pool()
    args: list = [owner_user_id]
    where = _feed_conditions(wiki, position, None, args, folder_id, cursor_bound=instant_bound)
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


async def complete_through(
    owner_user_id: UUID,
    position: Position,
    until: datetime,
    wiki: str,
    folder_id: UUID | None = None,
) -> Position:
    """Where the curator's watermark may stand after one successful run.

    The feed is complete through `until` unless it overflowed _MAX_EVENTS, in which
    case it is complete through exactly the last event that fit: `Position(T_k,
    id_k)`, standing INSIDE the saturated instant rather than a microsecond before
    it. The old `-1µs` step is what made a tie a plateau — it landed behind the
    whole tie, so every run re-read the same first budget and the backlog never
    drained (STAS-235). A result is therefore always strictly ahead of the position
    it was read from: with no overflow the whole instant `until` is behind, which is
    ahead of any mid-tie position inside that instant; with overflow the event id
    advances within the instant.

    `wiki` must be the value the run's feed was read with: this decides how far
    that run's watermark may move, so a wider scope here would let the watermark
    step past events the curator was never shown. `folder_id` likewise must be
    the folder the scoped feed was cut by."""
    events, has_more = await _feed_events(
        owner_user_id, position, until, _MAX_EVENTS, wiki, folder_id
    )
    if not has_more:
        return Position(at=until)
    return Position(at=events[-1]["created_at"], event_id=events[-1]["event_id"])


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None
