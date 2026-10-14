"""The server closes the sessions its own scheduled runs create (STAS-303, AC2a).

A scheduled run's session had no possible emitter of the close signal: the plugin
is not involved, the harness lives on a sprite, and the dispatcher writes no
history events at all (`tasks/agent_schedules.py` meters `curator_runs`/`agents`
and nothing else). So every run — 899 of them on the founder stack — left a
`sessions` row open forever, reading "still running" to every reader. The close
rule (task doc `close-rule`) names evidence E2 as the fix's place: `run_scheduled`
is where the server observes its own run die, so `run_scheduled` emits the close.

The seam here is `run_chat` / `scoped_curation_service.run` — patched to do what
the real turn does observably (push its final `assistant_message` into the session
it was handed). `run_chat`'s own behavior belongs to the existing sprite suite;
what is under test is the wrapper's contract: whichever sessions a dispatch was
handed, and whether it returned, raised, or produced nothing, the run's sessions
end up closed — through the ordinary ingestion path, never by writing the column.

Each test therefore checks the four things the rule obliges:

- the close row exists with the shared `CLOSE_EVENT_TYPE` name and its content is
  the server's neutral note (no ticket/PR hint for the hint-gated push path);
- `sessions.finished_at` is stamped at the *stored* instant of that close event,
  and equals the readers' derivation (`MAX(he.created_at)`,
  `routers/agents.py:79` + `:129`) — the consistency obligation in `close-rule` §7;
- the session ids closed are exactly the ids the run's builders minted (pinned
  against `build_digest_turn`, so the two spellings cannot drift apart);
- a run that never wrote a row mints none, and a failing close never replaces the
  run's own result.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend.database import get_pool
from backend.services import (
    agent_service,
    curation_service,
    memory_service,
    scoped_curation_service,
    session_service,
    sprite_agent_service,
)
from backend.services.sprite_agent_service import RUN_CLOSE_NOTE, scheduled_session_ids

from .conftest import FakeRedis, unique_name

# The manual dispatch's stamp shape (`%Y%m%d%H%M%S`, agent_schedules.py:232) — the
# longest one a run can carry, so a session-id regression shows up here first.
STAMP = "20261006120000"


async def _owner() -> UUID:
    return UUID(
        str(
            await get_pool().fetchval(
                "INSERT INTO users (name, display_name) VALUES ($1, 'Run owner') RETURNING id",
                unique_name("close"),
            )
        )
    )


async def _scheduled_agent(owner: UUID) -> dict:
    """A non-curators scheduled agent: its session prefix is `agent-sched-`."""
    return await agent_service.create_agent(
        owner,
        {
            "name": "Daily summary",
            "run_mode": "scheduled",
            "schedule_cron": "0 6 * * *",
            "schedule_prompt": "Summarize yesterday.",
            "model_provider": "anthropic",
        },
    )


async def _curator(owner: UUID, *, digest: bool = False) -> dict:
    agent = await agent_service.get_or_create_curator(owner, wiki="internal")
    if digest:
        pool = get_pool()
        await pool.execute(
            "UPDATE agents SET digest_provider = 'anthropic', digest_model_id = 'haiku' "
            "WHERE id = $1",
            UUID(str(agent["id"])),
        )
        agent = await agent_service.get_agent(owner, UUID(str(agent["id"])))
        assert agent["digest_provider"] == "anthropic"
    return agent


def _record_turn(
    seen: list[str],
    *,
    reply: str = "All done.",
    push: bool = True,
    raise_after_push: bool = False,
):
    """Stand in for the turn itself: record the session id it was handed, and
    leave behind the event the real `run_chat` leaves behind."""

    async def fake_run_chat(owner_user_id, owner_name, user_id, session_id, message, **kwargs):
        seen.append(session_id)
        if push:
            await memory_service.push_event(
                owner_user_id,
                kwargs["agent_name"],
                "assistant_message",
                reply,
                user_id,
                session_id,
            )
        if raise_after_push:
            raise RuntimeError("the harness died mid-turn")
        return reply

    return fake_run_chat


async def _session_row(owner: UUID, session_id: str) -> dict | None:
    row = await get_pool().fetchrow(
        "SELECT finished_at, last_event_at FROM sessions WHERE owner_user_id = $1 AND session_id = $2",
        owner,
        session_id,
    )
    return dict(row) if row else None


async def _close_events(owner: UUID, session_id: str) -> list[tuple]:
    rows = await get_pool().fetch(
        "SELECT created_at, content FROM history_events "
        "WHERE owner_user_id = $1 AND session_id = $2 AND event_type = $3 "
        "ORDER BY created_at",
        owner,
        session_id,
        session_service.CLOSE_EVENT_TYPE,
    )
    return [(r["created_at"], r["content"]) for r in rows]


async def _last_event_instant(owner: UUID, session_id: str):
    """The readers' own derivation of 'when this run ended' — `MAX(he.created_at)`
    is what `routers/agents.py` projects as `last_event_at` and shows as the run's
    `finished_at` for any resolved status (`:79`, `:129`)."""
    return await get_pool().fetchval(
        "SELECT MAX(created_at) FROM history_events WHERE owner_user_id = $1 AND session_id = $2",
        owner,
        session_id,
    )


async def assert_closed(owner: UUID, session_id: str) -> None:
    closes = await _close_events(owner, session_id)
    assert [content for _, content in closes] == [RUN_CLOSE_NOTE], (
        f"{session_id}: exactly one close, under the shared event name"
    )
    row = await _session_row(owner, session_id)
    assert row is not None
    assert row["finished_at"] == closes[0][0], (
        "the close must land at the instant its event row was stored"
    )
    assert row["finished_at"] == await _last_event_instant(owner, session_id), (
        "the stored close must agree with what the run list displays"
    )
    assert row["last_event_at"] == row["finished_at"]


async def assert_untouched(owner: UUID, session_id: str) -> None:
    assert await _session_row(owner, session_id) is None, (
        "a close must never mint a session row the run never wrote"
    )
    assert await _close_events(owner, session_id) == []


async def test_plain_scheduled_run_closes_its_session(monkeypatch, _db_pool):
    """The shape that produced most of the founder stack's open rows."""
    owner = await _owner()
    agent = await _scheduled_agent(owner)
    seen: list[str] = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", _record_turn(seen))

    result = await sprite_agent_service.run_scheduled(
        agent, STAMP, curation_service.position_of(agent)
    )

    assert result == "All done."
    assert seen == [f"agent-sched-{agent['id']}-{STAMP}"]
    assert seen == list(scheduled_session_ids(agent, STAMP))
    await assert_closed(owner, seen[0])


async def test_curator_run_closes_both_sessions_of_a_two_phase_run(monkeypatch, _db_pool):
    """A digest+writer run writes two sessions and closes both, digest first."""
    owner = await _owner()
    agent = await _curator(owner, digest=True)
    seen: list[str] = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", _record_turn(seen))

    await sprite_agent_service.run_scheduled(agent, STAMP, curation_service.position_of(agent))

    digest_id, _digest_prompt = await sprite_agent_service.build_digest_turn(agent, STAMP)
    assert seen == [digest_id, f"{sprite_agent_service.scheduled_session_prefix(agent)}{STAMP}"], (
        "both phases ran, in their real order"
    )
    assert scheduled_session_ids(agent, STAMP) == tuple(seen), (
        "the close must name the ids the builders mint, not a copy of the formula"
    )
    writer_id = seen[1]
    await assert_closed(owner, digest_id)
    await assert_closed(owner, writer_id)


async def test_a_run_that_raises_still_closes_the_session_it_wrote(monkeypatch, _db_pool):
    """E2 on the death path: the raise is the run's outcome, the close is the
    session's — and the run's own exception must survive the `finally`."""
    owner = await _owner()
    agent = await _curator(owner)
    seen: list[str] = []
    monkeypatch.setattr(
        sprite_agent_service,
        "run_chat",
        _record_turn(
            seen,
            reply=sprite_agent_service.RUN_FAILED_PREFIX + " quota",
            raise_after_push=True,
        ),
    )

    with pytest.raises(RuntimeError, match="harness died"):
        await sprite_agent_service.run_scheduled(agent, STAMP, curation_service.position_of(agent))

    await assert_closed(owner, seen[0])


async def test_an_empty_run_closes_and_mints_nothing(monkeypatch, _db_pool):
    """A turn refused before its first event wrote no transcript: closing would
    invent one, so `run_scheduled` skips it (and never inserts a row)."""
    owner = await _owner()
    agent = await _scheduled_agent(owner)
    seen: list[str] = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", _record_turn(seen, reply="", push=False))

    await sprite_agent_service.run_scheduled(agent, STAMP, curation_service.position_of(agent))

    assert seen == [f"agent-sched-{agent['id']}-{STAMP}"]
    await assert_untouched(owner, seen[0])


async def test_a_failing_close_is_recorded_and_never_replaces_the_run(
    monkeypatch, caplog, _db_pool
):
    """The close is bookkeeping: it may not eat the run's result. Precedent is the
    dispatcher's own `_resolve_escaped_run`, which logs rather than raises for the
    same reason."""
    owner = await _owner()
    agent = await _scheduled_agent(owner)
    seen: list[str] = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", _record_turn(seen))

    real_push = memory_service.push_event

    async def failing_close(*args, **kwargs):
        if kwargs.get("session_id") and args[2] == session_service.CLOSE_EVENT_TYPE:
            raise RuntimeError("the database is having a day")
        return await real_push(*args, **kwargs)

    monkeypatch.setattr(memory_service, "push_event", failing_close)

    with caplog.at_level("ERROR"):
        result = await sprite_agent_service.run_scheduled(
            agent, STAMP, curation_service.position_of(agent)
        )

    assert result == "All done.", "the run's own outcome survives a failed close"
    assert any("could not close session" in record.message for record in caplog.records), (
        "the failed close has to be loud in the log, not silent"
    )
    assert await _close_events(owner, seen[0]) == []
    row = await _session_row(owner, seen[0])
    assert row["finished_at"] is None, "an unemitted close leaves the session honestly open"


async def test_a_failing_existence_probe_never_replaces_the_run(monkeypatch, caplog, _db_pool):
    """The skip-above is itself a database read, and it runs in the `finally` too.

    A pool that goes away during shutdown would otherwise raise out of the close
    and turn a run that returned into a run that raised: the dispatcher would
    record the lane as failed, fire the failure alert, and step past the lane until
    the nightly tick — a false 'failed' curator run in the very log this card fixes.
    """
    owner = await _owner()
    agent = await _scheduled_agent(owner)
    seen: list[str] = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", _record_turn(seen))

    async def failing_probe(*_args, **_kwargs):
        raise RuntimeError("the pool went away")

    monkeypatch.setattr(session_service, "get_session", failing_probe)

    with caplog.at_level("ERROR"):
        result = await sprite_agent_service.run_scheduled(
            agent, STAMP, curation_service.position_of(agent)
        )

    assert result == "All done.", "a close that could not even look is not the run's failure"
    assert any("could not close session" in record.message for record in caplog.records)
    assert await _close_events(owner, seen[0]) == []
    assert (await _session_row(owner, seen[0]))["finished_at"] is None


async def test_scoped_workspace_run_closes_the_session_its_lane_minted(monkeypatch, _db_pool):
    """The developer-platform lane mints its session id inside
    `scoped_curation_service.run` — a second spelling of the same id, under the
    workspace owner. This pins both spellings against each other and closes it."""
    owner = await _owner()
    agent = await _curator(owner)
    session_id = f"{sprite_agent_service.scheduled_session_prefix(agent)}{STAMP}"
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: FakeRedis())

    async def workspace_for_agent(_agent):
        return {"id": str(uuid4())}

    async def scoped_run(run_agent, _workspace, run_stamp, _read_position):
        # The lane's own spelling of the id, from scoped_curation_service.run.
        assert scheduled_session_ids(agent, run_stamp) == (
            f"{curation_service.CURATOR_SESSION_ID_PREFIX}{run_agent['id']}-{run_stamp}",
        ), "the scoped lane and the sprite lane must name one and the same session"
        await memory_service.push_event(
            UUID(str(run_agent["user_id"])),
            run_agent["name"],
            "assistant_message",
            "Curated 3 pages.",
            UUID(str(run_agent["user_id"])),
            session_id,
        )
        return "Curated 3 pages."

    monkeypatch.setattr(scoped_curation_service, "workspace_for_agent", workspace_for_agent)
    monkeypatch.setattr(scoped_curation_service, "run", scoped_run)

    result = await sprite_agent_service.run_scheduled(
        agent, STAMP, curation_service.position_of(agent)
    )

    assert result == "Curated 3 pages."
    await assert_closed(owner, session_id)
