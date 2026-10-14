"""The run-session close pass uses stored evidence and invents no end times.

`close_finished_curator_run_sessions` decides, from what a database already holds,
which server-created run sessions ended. Both failure directions matter: too
little leaves a finished run reading "still running" forever (the 899-row bug this
change exists for), and too much stamps a live run as over — the invention
`sessions.finished_at` exists to prevent. So the test seeds every shape a
production database actually contains and asserts both halves against the
migration's own `upgrade()`: the runs with proof close at the instant their last
event was stored, and every session whose only evidence is silence — or which
belongs to a family whose close its emitter owns — keeps NULL.

Run-session ids come from `scheduled_session_prefix` rather than literals, so the
migration's frozen prefixes cannot drift away from the service's live one without
this file reddening.
"""

import importlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.services.sprite_agent_service import scheduled_session_prefix
from backend.tests.migration_ids import module_for_slug

from .conftest import unique_name

CLOSE_PASS = module_for_slug("close_finished_curator_run_sessions")

_T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
_TURN = _T0 + timedelta(minutes=4)
_LAST = _T0 + timedelta(minutes=9)
_AFTER = _LAST + timedelta(minutes=20)

_CURATOR_PREFIX = scheduled_session_prefix({"id": str(uuid4()), "is_curator": True})
_SCHED_PREFIX = scheduled_session_prefix({"id": str(uuid4()), "is_curator": False})


def curator_run(stamp: str, suffix: str = "") -> str:
    """One curator run's session id — `suffix` is the digest phase's `-digest`."""
    return f"{_CURATOR_PREFIX}{stamp}{suffix}"


def sched_run(stamp: str) -> str:
    return f"{_SCHED_PREFIX}{stamp}"


def _insert(conn, statement, **params):
    return conn.execute(text(statement), params).scalar_one()


def _session(conn, owner, external_id, last_event_at, finished_at=None):
    return _insert(
        conn,
        "INSERT INTO sessions (owner_user_id, created_by, session_id, agent_name, "
        "last_event_at, finished_at) "
        "VALUES (:owner, :owner, :external_id, 'Memory curator', :last_event_at, :finished_at) "
        "RETURNING id",
        owner=owner,
        external_id=external_id,
        last_event_at=last_event_at,
        finished_at=finished_at,
    )


def _event(conn, owner, external_id, event_type, at, content="x"):
    conn.execute(
        text(
            "INSERT INTO history_events (owner_user_id, created_by, session_id, agent_name, "
            "event_type, content, created_at) "
            "VALUES (:owner, :owner, :external_id, 'Memory curator', :event_type, :content, :at)"
        ),
        {
            "owner": owner,
            "external_id": external_id,
            "event_type": event_type,
            "content": content,
            "at": at,
        },
    )


def _seed_world(conn) -> dict:
    """Every run shape a database can be in, at once.

    The closed ones are the cases the pass exists for; the NULL ones are the
    promises it makes while doing so — and each of those is a shape that *looks*
    finishable, which is why they have to be asserted.
    """
    owner = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Run owner') RETURNING id",
        name=unique_name("run-owner"),
    )
    stranger = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Other owner') RETURNING id",
        name=unique_name("other-owner"),
    )

    # E1: the run recorded its terminal message.
    finished_run = sched_run("20260601090000")
    _session(conn, owner, finished_run, _LAST)
    _event(conn, owner, finished_run, "user_message", _T0)
    _event(conn, owner, finished_run, "assistant_message", _LAST)

    # A failed run is terminal too: the failure marker is still the last word.
    failed_run = curator_run("20260601090000")
    _session(conn, owner, failed_run, _LAST)
    _event(conn, owner, failed_run, "user_message", _T0)
    _event(conn, owner, failed_run, "assistant_message", _LAST, content="Run failed: quota")

    # The digest phase of that same lane, closed the same way.
    digest = curator_run("20260601090000", "-digest")
    _session(conn, owner, digest, _TURN)
    _event(conn, owner, digest, "assistant_message", _TURN)

    # E3 and nothing else: the lane's earlier run wrote no final message, but the
    # server let a later run of that lane start.
    superseded = curator_run("20260601080000")
    _session(conn, owner, superseded, _TURN)
    _event(conn, owner, superseded, "user_message", _T0)
    successor = curator_run("20260602090000")
    _session(conn, owner, successor, _AFTER + timedelta(minutes=5))
    _event(conn, owner, successor, "user_message", _AFTER)
    _event(conn, owner, successor, "assistant_message", _AFTER + timedelta(minutes=5))

    # Silence is not evidence: the lane's most recent run, interrupted before its
    # final message, has proof of nothing but quiet.
    quiet = sched_run("20260609090000")
    _session(conn, owner, quiet, _TURN)
    _event(conn, owner, quiet, "user_message", _T0)

    # A row alone is not evidence either: no events at all, however recent.
    eventless = sched_run("20260608090000")
    _session(conn, owner, eventless, _LAST)

    # Another family, same shape: a `fusion-task-*` session's close belongs to its
    # pusher, so this pass must not reach into it even though it carries both an
    # assistant_message and a later session of its lane.
    fusion_open, fusion_later = f"fusion-task-{uuid4()}", f"fusion-task-{uuid4()}"
    _session(conn, owner, fusion_open, _LAST)
    _event(conn, owner, fusion_open, "assistant_message", _LAST)
    _session(conn, owner, fusion_later, _AFTER)
    _event(conn, owner, fusion_later, "assistant_message", _AFTER)

    # A close the pusher already stamped is never overwritten, even by a later
    # event: the server's own evidence does not outrank an emitted close.
    emitted_close = curator_run("20260603090000")
    _session(conn, owner, emitted_close, _LAST, finished_at=_T0)
    _event(conn, owner, emitted_close, "assistant_message", _LAST)

    # The same lane string under another owner: a successor has to belong to the
    # same owner to prove anything, so this interrupted run stays open.
    other_owner = curator_run("20260601090000")
    _session(conn, stranger, other_owner, _TURN)
    _event(conn, stranger, other_owner, "user_message", _T0)

    return {
        "owner": owner,
        "stranger": stranger,
        "finished_run": finished_run,
        "failed_run": failed_run,
        "digest": digest,
        "superseded": superseded,
        "quiet": quiet,
        "eventless": eventless,
        "fusion_open": fusion_open,
        "emitted_close": emitted_close,
    }


def _finished(conn, external_id, owner):
    return conn.execute(
        text(
            "SELECT finished_at FROM sessions "
            "WHERE session_id = :external_id AND owner_user_id = :owner"
        ),
        {"external_id": external_id, "owner": owner},
    ).scalar()


def _displayed_end(conn, external_id, owner):
    """What the run list shows as a run's end — the projection `routers/agents.py`
    reads — so a stored close that disagrees with it is a visible contradiction."""
    return conn.execute(
        text(
            "SELECT MAX(created_at) FROM history_events "
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

    close_pass = importlib.import_module(CLOSE_PASS)
    with Operations.context(MigrationContext.configure(conn)):
        close_pass.upgrade()


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
async def test_the_pass_closes_evidenced_runs_and_leaves_the_rest_open():
    def work(conn):
        world = _seed_world(conn)

        _run_upgrade(conn)

        assert _finished(conn, world["finished_run"], world["owner"]) == _LAST
        # The closed instant is the instant the run list displays as the run's end.
        assert _finished(conn, world["finished_run"], world["owner"]) == _displayed_end(
            conn, world["finished_run"], world["owner"]
        )
        assert _finished(conn, world["failed_run"], world["owner"]) == _LAST, (
            "a failed run is a finished run"
        )
        assert _finished(conn, world["digest"], world["owner"]) == _TURN
        assert _finished(conn, world["superseded"], world["owner"]) == _TURN, (
            "a successor on the lane proves the interrupted run is over"
        )

        assert _finished(conn, world["quiet"], world["owner"]) is None, (
            "the lane's most recent quiet run has evidence of nothing but silence"
        )
        assert _finished(conn, world["eventless"], world["owner"]) is None
        assert _finished(conn, world["fusion_open"], world["owner"]) is None, (
            "a fusion session closes when its pusher says so, not when a pass guesses"
        )
        assert _finished(conn, world["emitted_close"], world["owner"]) == _T0, (
            "an emitted close is never overwritten by server-side evidence"
        )
        assert _finished(conn, world["failed_run"], world["stranger"]) is None, (
            "a successor must belong to the same owner to prove anything"
        )

    await _rolled_back(work)


@pytest.mark.asyncio
async def test_re_running_the_pass_changes_nothing():
    def work(conn):
        world = _seed_world(conn)
        ids = (
            world["finished_run"],
            world["failed_run"],
            world["digest"],
            world["superseded"],
            world["quiet"],
            world["eventless"],
            world["emitted_close"],
        )

        _run_upgrade(conn)
        once = {external_id: _finished(conn, external_id, world["owner"]) for external_id in ids}

        _run_upgrade(conn)

        after = {external_id: _finished(conn, external_id, world["owner"]) for external_id in ids}
        assert after == once
        assert once[world["quiet"]] is None

    await _rolled_back(work)


@pytest.mark.asyncio
async def test_a_lane_still_matches_across_the_two_run_stamp_lengths():
    """Stamps grew from 12 to 14 digits (`agent_schedules.py:231`: to the minute
    for cron, to the second for the manual path), so a lane's history mixes both —
    the same trap that made a session-id width guard necessary. Lane matching that
    assumed one length would stop proving E3 for every old run in a live database.
    """
    short_stamp_run = curator_run("202606010900")
    long_stamp_run = curator_run("20260602090000")

    def work(conn):
        owner = _insert(
            conn,
            "INSERT INTO users (name, display_name) VALUES (:name, 'Mixed stamps') RETURNING id",
            name=unique_name("mixed-stamps"),
        )
        _session(conn, owner, short_stamp_run, _TURN)
        _event(conn, owner, short_stamp_run, "user_message", _T0)
        _session(conn, owner, long_stamp_run, _AFTER)
        _event(conn, owner, long_stamp_run, "assistant_message", _AFTER)

        _run_upgrade(conn)

        assert _finished(conn, short_stamp_run, owner) == _TURN, (
            "the older, shorter-stamped run is closed by its 14-digit successor"
        )
        assert _finished(conn, long_stamp_run, owner) == _AFTER

    await _rolled_back(work)
