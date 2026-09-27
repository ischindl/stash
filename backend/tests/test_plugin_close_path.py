"""The hook the product ships is what closes a session — proved without a deploy.

The founder-stack proof this card asks for is a deploy measurement: restart the
stack, run one recorded session, read the row. A worktree cannot restart that
stack, so the deploy stays with the operator. What that measurement would have
established is not deploy-specific: the shipped end-of-session emitter has to
leave a row the product reads as closed, through the client it really uses and
the payload it really sends — which is a timestamp-less one, so a test that
hand-stamped its own close would prove nothing about the product.

This runs the real emitter (`stream_session_end`, the function every
`on_session_end` hook calls) and the real `StashClient` against the real app. The
socket is the only thing replaced, and `StashClient` swallows push failures, so
the assertions are read back out of the database rather than out of the call.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from stashai.plugin import hooks as plugin_hooks
from stashai.plugin import scope as plugin_scope
from stashai.plugin.stash_client import StashClient

from .conftest import unique_name

# Claude Code hands its SessionEnd hook a JSON object on stdin and the adapter
# turns it into the event the emitter consumes. Loaded the way `stash hook run`
# loads it — from the shipped asset directory, which is not an importable
# package — so this test reads the adapter the product reads.
_ADAPT = (
    Path(__file__).resolve().parents[2]
    / "stashai"
    / "plugin"
    / "assets"
    / "claude"
    / "scripts"
    / "adapt.py"
)


def _claude_adapter():
    spec = importlib.util.spec_from_file_location("claude_hook_adapt", _ADAPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _InProcessSocket:
    """`StashClient`'s HTTP layer, answered by the app on the running loop.

    Swapping this attribute and nothing else keeps the auth headers, the body,
    the error mapping and the retry queue of the real client in the path; only
    the wire is missing.
    """

    def __init__(self, send):
        self._send = send

    def request(self, method, path, headers=None, json=None, **_ignored):
        # The hook is synchronous, so it runs on a worker thread while the test's
        # loop stays free to serve what it sends.
        return self._send(method, path, headers or {}, json)

    def close(self):
        pass


def _hook_client(api_key: str, data_dir: Path, loop) -> StashClient:
    """The product's own client, with the socket answered in-process."""

    async def _deliver(method, path, headers, body):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            return await ac.request(method, path, headers=headers, json=body)

    def _bridge(method, path, headers, body):
        return asyncio.run_coroutine_threadsafe(_deliver(method, path, headers, body), loop).result(
            timeout=60
        )

    client = StashClient(base_url="http://test", api_key=api_key, data_dir=data_dir)
    client._http = _InProcessSocket(_bridge)
    return client


async def _register(client) -> str:
    name = unique_name("hookclose")
    resp = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@test.local"},
    )
    assert resp.status_code == 201
    return resp.json()["api_key"]


@pytest.mark.asyncio
async def test_the_shipped_session_end_hook_closes_the_session(client, pool, tmp_path, monkeypatch):
    api_key = await _register(client)

    # The plugin streams only when it is configured and the session's folder is
    # in scope, both read from ~/.stash/config.json. Pointed at a scratch one,
    # this test cannot be changed by whoever runs it being signed in for real.
    plugin_config = tmp_path / ".stash" / "config.json"
    plugin_config.parent.mkdir(parents=True)
    plugin_config.write_text(json.dumps({"api_key": api_key}))
    monkeypatch.setattr(plugin_hooks, "_CONFIG_FILE", plugin_config)
    monkeypatch.setattr(plugin_scope, "_CONFIG_FILE", plugin_config)

    loop = asyncio.get_running_loop()
    session_id = "hook-closed-session"
    cwd = str(tmp_path)

    def _run_the_hook():
        hook_client = _hook_client(api_key, tmp_path / "plugin-data", loop)
        cfg = {"agent_name": "claude", "client": "claude_code"}
        state = {"session_id": session_id, "cwd": cwd}

        hook_client.push_event(
            agent_name="claude",
            event_type="user_message",
            content="a turn of a real session",
            session_id=session_id,
        )
        plugin_hooks.stream_session_end(
            hook_client,
            cfg,
            state,
            _claude_adapter().adapt_stop({"session_id": session_id, "cwd": cwd}),
        )
        hook_client.close()

    await asyncio.to_thread(_run_the_hook)

    row = await pool.fetchrow(
        """
        SELECT s.finished_at, s.agent_name, e.created_at AS close_stored_at, e.content
          FROM sessions s
          LEFT JOIN history_events e
            ON e.session_id = s.session_id AND e.event_type = 'session_end'
         WHERE s.session_id = $1
        """,
        session_id,
    )
    assert row is not None, "the hook must have created the session row"
    assert row["agent_name"] == "claude"
    assert row["close_stored_at"] is not None, "the hook's close event must be stored"
    assert row["content"].startswith("Session ended."), "the close is the hook's own event"
    assert row["finished_at"] == row["close_stored_at"], (
        "the close is the instant the close event was stored — the shipped payload"
        " carries no event time of its own"
    )


@pytest.mark.asyncio
async def test_a_session_the_hook_never_ended_stays_open(client, pool, tmp_path):
    """The mirror image, because a close stamped too eagerly would pass the test
    above by accident: turns with no `session_end` leave the row open, which is
    exactly what the sessions list reads."""
    api_key = await _register(client)
    loop = asyncio.get_running_loop()

    def _run_one_turn():
        hook_client = _hook_client(api_key, tmp_path, loop)
        hook_client.push_event(
            agent_name="claude",
            event_type="assistant_message",
            content="still talking",
            session_id="hook-open-session",
        )
        hook_client.close()

    await asyncio.to_thread(_run_one_turn)

    assert (
        await pool.fetchval(
            "SELECT finished_at FROM sessions WHERE session_id = $1", "hook-open-session"
        )
        is None
    )
