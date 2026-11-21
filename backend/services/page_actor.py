"""Who a page write is credited to: one actor, read at the one place that writes.

Page provenance lives in four columns — `page_edits.session_id`,
`page_edits.agent_name`, `pages.last_edit_session_id`,
`pages.last_edit_agent_name`. They used to be three pairs of optional
per-call arguments, and the busiest writer (the REST routes the `stash` CLI
calls) never passed them, so every CLI and MCP write was recorded with nothing
in them and an operator probing for curator writes counted zero real ones.
A parameter a caller may omit cannot be told apart from one nobody meant to
pass, so the writer is now a scoped value and a write with no actor in context
raises. See `docs/page-provenance.md` for what each column answers.

Three kinds of writer:

- `human()` — a person: the web UI, or any credential with no dispatched run
  behind it. Both columns stay NULL, which is what makes
  `agent_name IS NULL` read as "a human wrote this".
- `agent(agent_name, session_id)` — a dispatched run (a chat turn, a curator
  pass). Both columns are filled, so the run's writes are countable without a
  time window.
- `system(name)` — a server lane with no run session: the clip-import worker, a
  seed, an importer. A name with a NULL session, so it is neither a human nor
  a run.

The identity an agent run carries back from the CLI is a claim, not a
credential: it is validated against `history_events` in `backend/auth.py` and
must never be consulted for permission.
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

# The wire names: the run's session id plus the `agents` row's id. Both values
# are ASCII by construction, which is why the id and not the display name
# travels: a header value must survive the ASCII wire, a name need not. The
# server reads them case-insensitively (Starlette headers); `cli/client.py`
# repeats this spelling because it ships standalone. One spelling, both sides.
AGENT_SESSION_HEADER = "X-Stash-Agent-Session"
AGENT_ID_HEADER = "X-Stash-Agent-Id"

# The environment names `sprite_agent_service` injects into a run's harness so
# the `stash` CLI inside it can carry the pair (session id + agent id) back on
# every request.
AGENT_SESSION_ENV = "STASH_AGENT_SESSION"
AGENT_ID_ENV = "STASH_AGENT_ID"


def _name(value: str, label: str) -> str:
    """The one validation every named writer passes through."""
    if not value or not value.strip():
        raise ValueError(f"PageActor: {label} must be a non-empty name, got {value!r}")
    return value.strip()


@dataclass(frozen=True)
class PageActor:
    """The writer of a page write. `agent_name=None` means a human, and only a human."""

    agent_name: str | None
    session_id: str | None

    @classmethod
    def human(cls) -> PageActor:
        return cls(agent_name=None, session_id=None)

    @classmethod
    def agent(cls, agent_name: str, session_id: str) -> PageActor:
        return cls(
            agent_name=_name(agent_name, "agent_name"), session_id=_name(session_id, "session_id")
        )

    @classmethod
    def system(cls, name: str) -> PageActor:
        """A server lane with no run session: it names itself and leaves the session NULL."""
        return cls(agent_name=_name(name, "name"), session_id=None)

    @property
    def is_human(self) -> bool:
        return self.agent_name is None


_actor_ctx: contextvars.ContextVar[PageActor | None] = contextvars.ContextVar(
    "stash_page_actor", default=None
)


def current_actor() -> PageActor:
    """The writer this write must be credited to. A missing actor is a defect at
    the edge that started the work, so it fails loud instead of recording NULL."""
    actor = _actor_ctx.get()
    if actor is None:
        raise RuntimeError(
            "page_actor: no page-write actor in context, so this write cannot name "
            "its writer. Run it inside page_actor(...): backend/auth.py installs the "
            "actor per HTTP request, tool_loop.stream_tool_loop per agent turn, and a "
            "lane with no request (worker, seed, script) declares its own PageActor."
        )
    return actor


def install(actor: PageActor) -> contextvars.Token[PageActor | None]:
    """Install `actor` and return the token that undoes it.

    For edges that outlive the block they install into — the HTTP request edge
    and the agent-turn loop — which reset the token when their work ends. Every
    other caller uses `page_actor` below."""
    return _actor_ctx.set(actor)


def uninstall(token: contextvars.Token[PageActor | None]) -> None:
    _actor_ctx.reset(token)


@asynccontextmanager
async def page_actor(actor: PageActor) -> AsyncIterator[None]:
    """Install `actor` for the writes inside the block."""
    token = install(actor)
    try:
        yield
    finally:
        uninstall(token)
