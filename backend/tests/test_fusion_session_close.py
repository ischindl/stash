"""A `fusion-task-*` session closes when Fusion's pusher says so, and only then.

Fusion's engine writes its task transcripts into Stash under its API key, as the
`fusion` agent, into a session id it mints itself (`fusion-task-<task uuid>` — the
task uuid is literally recoverable from the id, which is why this family is
server-created yet not the server's to close). The engine has the one fact the
server can never have: its workflow loop for that task is over. So the division of
labor is the pusher decides, the server stamps — the ordinary ingestion path,
`CLOSE_EVENT_TYPE` through `push_event`/`push_events_batch` into the one SQL write
site (`session_service.upsert_session`), with no fusion-specific code anywhere.

That asymmetry is what STAS-303's decision protects. The server-created
scheduled/curator family gets a server-side close because *nothing else* can emit
one for it; this family must not get that treatment, because closing on
`assistant_message` would end a session while the engine still streams into it —
an approved-then-revised workflow keeps pushing, and a session still open is the
truth. (`test_migration_close_curator_runs.py` holds the other half: 0226's scope
clause leaves these ids alone.)

These tests are the forward half of AC2b: whichever of the two ingestion doors the
pusher uses, its `session_end` lands the close, at the instant the pusher stamped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.database import get_pool
from backend.services import memory_service, session_service

from .conftest import unique_name

AGENT = "fusion"

# A checkout's worktree, as the engine reports it.
CWD = "/srv/fusion/worktrees/fn-1234"


async def _owner() -> UUID:
    return UUID(
        str(
            await get_pool().fetchval(
                "INSERT INTO users (name, display_name) VALUES ($1, 'Fusion owner') RETURNING id",
                unique_name("fusion"),
            )
        )
    )


def _task_session() -> str:
    """The engine's id shape: `fusion-task-` + the Fusion task's own uuid."""
    return f"fusion-task-{UUID(int=0x1234)}"


async def _push(owner: UUID, session_id: str, event_type: str, *, at: datetime | None = None):
    return await memory_service.push_event(
        owner,
        AGENT,
        event_type,
        "step 3 done",
        owner,
        session_id,
        metadata={"cwd": CWD},
        created_at=at,
    )


async def _push_batch(owner: UUID, session_id: str, event_type: str, *, at: datetime):
    await memory_service.push_events_batch(
        owner,
        owner,
        [
            {
                "agent_name": AGENT,
                "event_type": event_type,
                "content": "step 3 done",
                "session_id": session_id,
                "metadata": {"cwd": CWD},
                "created_at": at,
            }
        ],
    )


async def _finished_at(owner: UUID, session_id: str):
    session = await session_service.get_session(owner, session_id)
    assert session is not None, "the pusher's first event creates the session row"
    return session["finished_at"]


@pytest.mark.asyncio
async def test_a_fusion_session_stays_open_while_the_pusher_is_streaming(_db_pool):
    """The server must not infer this family's end: an in-flight fusion session
    has a final-looking assistant message and no close, and reading it as finished
    would be the lie in the other direction."""
    owner, session_id = await _owner(), _task_session()

    await _push(owner, session_id, "session_start")
    await _push(owner, session_id, "assistant_message")
    await _push(owner, session_id, "tool_use")

    assert await _finished_at(owner, session_id) is None


@pytest.mark.asyncio
async def test_the_pushers_session_end_closes_it_at_the_instant_the_pusher_stamped(_db_pool):
    """The close rides on the emitter's event time, not on ingest-now: a pusher
    that batches its tail and delivers late still reports when its run ended."""
    owner, session_id = await _owner(), _task_session()
    ended_at = datetime.now(UTC) - timedelta(hours=3)

    await _push(owner, session_id, "session_start", at=ended_at - timedelta(minutes=30))
    await _push(owner, session_id, "assistant_message", at=ended_at - timedelta(minutes=1))
    await _push(owner, session_id, session_service.CLOSE_EVENT_TYPE, at=ended_at)

    assert await _finished_at(owner, session_id) == ended_at


@pytest.mark.asyncio
async def test_the_batch_door_closes_it_the_same_way(_db_pool):
    """Which of the two ingestion doors the engine drives is its business; both
    have to carry the close, or the pusher's choice silently un-closes sessions."""
    owner, session_id = await _owner(), _task_session()
    ended_at = datetime.now(UTC) - timedelta(minutes=20)

    await _push_batch(owner, session_id, "session_start", at=ended_at - timedelta(minutes=5))
    assert await _finished_at(owner, session_id) is None

    await _push_batch(owner, session_id, session_service.CLOSE_EVENT_TYPE, at=ended_at)

    assert await _finished_at(owner, session_id) == ended_at


@pytest.mark.asyncio
async def test_a_replayed_older_close_cannot_undo_a_newer_one(_db_pool):
    """An approved plan that came back revised keeps pushing; a re-delivered
    transcript tail must not walk a real close backward and re-open the run."""
    owner, session_id = await _owner(), _task_session()
    now = datetime.now(UTC)

    await _push(owner, session_id, session_service.CLOSE_EVENT_TYPE, at=now)
    await _push(owner, session_id, "user_message", at=now - timedelta(hours=1))
    await _push(owner, session_id, session_service.CLOSE_EVENT_TYPE, at=now - timedelta(hours=1))

    assert await _finished_at(owner, session_id) == now


@pytest.mark.asyncio
async def test_an_aborted_run_closes_on_the_same_event_as_a_finished_one(_db_pool):
    """The close is decided by the event's name, never its prose: an interrupted
    fusion run whose pusher reports the interruption is over, exactly as one that
    finished — and content-free closing is also what keeps a run's text out of the
    ticket and pull-request hint paths a close must never trigger."""
    owner, session_id = await _owner(), _task_session()

    await _push(owner, session_id, "assistant_message")
    await memory_service.push_event(
        owner,
        AGENT,
        session_service.CLOSE_EVENT_TYPE,
        "Session ended. Aborted by the operator.",
        owner,
        session_id,
    )

    assert await _finished_at(owner, session_id) is not None
