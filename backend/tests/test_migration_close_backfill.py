"""The backfill gives closed sessions their end time and invents none.

`0221_backfill_finished_at_from_close_events` runs against databases whose
`sessions.finished_at` is NULL for every real session, because the server stored
agent close events for months without consuming them. Two failures are possible
and only one is visible in the product: too little leaves a finished session
reading as in progress, too much puts a fabricated end time on a session that is
still live — the corruption the column exists to remove. So this asserts both
halves against the migration's own `upgrade()`: a session backed by a close event
gets that event's time (even when a later turn follows it, and even when the
value already stored is earlier), while a session whose rows look exactly like a
transcript import — months of turns, no close — keeps NULL.

It also asserts the statement is safe to re-run: `GREATEST` plus the WHERE must
leave an already-correct row untouched, so a retry cannot walk a close backward.
"""

import importlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import unique_name

BACKFILL = "backend.migrations.versions.0221_backfill_finished_at_from_close_events"

_T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
_LATER = _T0 + timedelta(hours=3)
_EVIDENCE = _T0 + timedelta(hours=2)


def _insert(conn, statement, **params):
    return conn.execute(text(statement), params).scalar_one()


def _session(conn, owner, external_id, finished_at=None):
    return _insert(
        conn,
        "INSERT INTO sessions (owner_user_id, created_by, session_id, agent_name, "
        "last_event_at, finished_at) "
        "VALUES (:owner, :owner, :external_id, 'claude', :last_event_at, :finished_at) "
        "RETURNING id",
        owner=owner,
        external_id=external_id,
        last_event_at=_T0,
        finished_at=finished_at,
    )


def _event(conn, owner, external_id, event_type, at):
    conn.execute(
        text(
            "INSERT INTO history_events (owner_user_id, created_by, session_id, agent_name, "
            "event_type, content, created_at) "
            "VALUES (:owner, :owner, :external_id, 'claude', :event_type, 'x', :at)"
        ),
        {
            "owner": owner,
            "external_id": external_id,
            "event_type": event_type,
            "at": at,
        },
    )


def _seed_world(conn) -> dict:
    """Every shape a database can be in, at once.

    `closed_by_evidence` is the case the migration exists for. `imported_session`
    is the transcript-import shape that must NOT be touched: three turns with
    historical timestamps and no close event, which is indistinguishable from a
    session that ended — and that is exactly why recency is not evidence.
    """
    owner = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Backfill owner') RETURNING id",
        name=unique_name("backfill-owner"),
    )
    stranger = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Other owner') RETURNING id",
        name=unique_name("backfill-stranger"),
    )

    _session(conn, owner, "closed-by-evidence")
    _event(conn, owner, "closed-by-evidence", "user_message", _T0)
    _event(conn, owner, "closed-by-evidence", "session_end", _EVIDENCE)

    _session(conn, owner, "closed-then-trailing-turn")
    _event(conn, owner, "closed-then-trailing-turn", "user_message", _T0)
    _event(conn, owner, "closed-then-trailing-turn", "session_end", _EVIDENCE)
    _event(conn, owner, "closed-then-trailing-turn", "user_message", _LATER)

    _session(conn, owner, "close-stamped-earlier", finished_at=_T0)
    _event(conn, owner, "close-stamped-earlier", "session_end", _EVIDENCE)

    _session(conn, owner, "close-stamped-later", finished_at=_LATER)
    _event(conn, owner, "close-stamped-later", "session_end", _EVIDENCE)

    _session(conn, owner, "imported-session")
    for offset, event_type in enumerate(("user_message", "assistant_message", "user_message")):
        _event(conn, owner, "imported-session", event_type, _T0 + timedelta(minutes=offset))

    # Two owners have used the same external id, so the match has to be per owner.
    _session(conn, stranger, "closed-by-evidence")

    # A close whose session row is long gone: no row to repair, no error.
    _event(conn, owner, "session-without-a-row", "session_end", _EVIDENCE)

    return {"owner": owner, "stranger": stranger}


def _finished(conn, external_id, owner):
    return conn.execute(
        text(
            "SELECT finished_at FROM sessions "
            "WHERE session_id = :external_id AND owner_user_id = :owner"
        ),
        {"external_id": external_id, "owner": owner},
    ).scalar()


def _run_upgrade(conn) -> None:
    """The migration's real `upgrade()` — not a copy of its statements.

    `upgrade()` runs through `op.get_bind()`, the exact call path an
    `alembic upgrade head` at boot takes.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    backfill = importlib.import_module(BACKFILL)
    with Operations.context(MigrationContext.configure(conn)):
        backfill.upgrade()


async def _rolled_back(work) -> None:
    """Run `work(sync_connection)` and put every byte back afterwards."""
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            await conn.run_sync(work)
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_backfill_closes_evidenced_sessions_and_leaves_the_rest_open():
    def work(conn):
        world = _seed_world(conn)

        _run_upgrade(conn)

        assert _finished(conn, "closed-by-evidence", world["owner"]) == _EVIDENCE
        # A turn after the close does not move the close: the session ended when
        # its harness said it ended.
        assert _finished(conn, "closed-then-trailing-turn", world["owner"]) == _EVIDENCE
        # GREATEST: an earlier stored value advances, a later one never moves back.
        assert _finished(conn, "close-stamped-earlier", world["owner"]) == _EVIDENCE
        assert _finished(conn, "close-stamped-later", world["owner"]) == _LATER
        # The transcript-import shape stays NULL, and so does the same external
        # id under another owner — neither session has a close event of its own.
        assert _finished(conn, "imported-session", world["owner"]) is None
        assert _finished(conn, "closed-by-evidence", world["stranger"]) is None

    await _rolled_back(work)


@pytest.mark.asyncio
async def test_re_running_the_backfill_changes_nothing():
    def work(conn):
        world = _seed_world(conn)
        ids = (
            "closed-by-evidence",
            "close-stamped-earlier",
            "close-stamped-later",
            "imported-session",
        )

        _run_upgrade(conn)
        once = {external_id: _finished(conn, external_id, world["owner"]) for external_id in ids}

        _run_upgrade(conn)

        after = {external_id: _finished(conn, external_id, world["owner"]) for external_id in ids}
        assert after == once
        assert once["imported-session"] is None

    await _rolled_back(work)
