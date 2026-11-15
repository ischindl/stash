"""Shared pytest fixtures for backend tests.

A fresh Postgres database is created for the entire test session, Alembic
migrations are applied once, and a FastAPI AsyncClient is yielded to each test.

Each test's side-effects are cleaned up via TRUNCATE on teardown, keeping
tests fully isolated without needing to recreate the schema between them.

Required env vars (defaults work against the Docker Compose postgres):
    TEST_DATABASE_URL  e.g. postgresql://stash:stash@localhost:5432/stash_test
"""

import asyncio
import os
import uuid
from contextlib import contextmanager

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Override DATABASE_URL before importing anything from the backend
_TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://stash:stash@localhost:5432/stash_test",
)
os.environ["TEST_DATABASE_URL"] = _TEST_DB_URL
os.environ["DATABASE_URL"] = _TEST_DB_URL

from backend import database as db_module  # noqa: E402
from backend.config import settings  # noqa: E402 — must come after env override
from backend.main import app  # noqa: E402 — must come after env override
from backend.services.page_actor import PageActor, install, uninstall  # noqa: E402


@pytest_asyncio.fixture(scope="session")
async def _db_pool():
    """Bootstrap the test database once per session."""
    # Ensure pgvector is available
    conn = await asyncpg.connect(_TEST_DB_URL)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.close()

    # Run Alembic migrations against the test DB
    import functools

    def _run_alembic():
        ini_path = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
        from alembic import command as alembic_cmd
        from alembic.config import Config

        cfg = Config(ini_path)
        alembic_cmd.upgrade(cfg, "head")

        # Managed-only chain (e.g. users.auth0_sub). Apply it unconditionally so
        # every test sees the managed schema regardless of which files ran first.
        managed_ini = os.path.join(os.path.dirname(__file__), "..", "managed", "alembic.ini")
        alembic_cmd.upgrade(Config(managed_ini), "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, functools.partial(_run_alembic))

    # Create the shared pool used by the app
    import json

    from pgvector.asyncpg import register_vector

    async def _init_connection(conn):
        await register_vector(conn)
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await conn.set_type_codec(
            "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    pool = await asyncpg.create_pool(_TEST_DB_URL, min_size=2, max_size=5, init=_init_connection)
    db_module.pool = pool
    yield pool
    await pool.close()
    db_module.pool = None


@pytest_asyncio.fixture
async def client(_db_pool):
    """An httpx AsyncClient wired to the FastAPI app.

    Does NOT use the full lifespan (avoids LISTEN/NOTIFY and background tasks
    in tests).  The pool is set directly on db_module above.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def pool(_db_pool):
    """Direct asyncpg pool access for low-level assertions."""
    yield _db_pool


def _managed_billing_settings(m: pytest.MonkeyPatch) -> None:
    """The four settings a managed installation carries. One definition, because
    "this deployment can charge" is one fact."""
    m.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    m.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_x")
    m.setattr(settings, "STRIPE_MONTHLY_PRICE_ID", "price_test_month")
    m.setattr(settings, "STRIPE_ANNUAL_PRICE_ID", "price_test_year")


@contextmanager
def managed_billing(monkeypatch: pytest.MonkeyPatch):
    """Hold the installation in its managed state for the duration of one block.

    For the half of a paired test that needs the billed answer while the other
    half runs on a self-host; `billing_on` is this same block stretched over a
    whole test.
    """
    with monkeypatch.context() as m:
        _managed_billing_settings(m)
        yield


@pytest.fixture
def billing_on(monkeypatch):
    """Model a deployment where billing IS configured (the managed install).

    The test suite runs with `STRIPE_SECRET_KEY` unset, which is a self-host: no
    plan is evaluated anywhere, so every plan gate answers unlimited. A test that
    asserts a paywall takes this fixture to say out loud which installation it
    models — without it, it is asserting cloud behaviour on a box that has no cloud.
    """
    with managed_billing(monkeypatch):
        yield


@pytest.fixture(autouse=True)
def page_actor():
    """Every page write names its writer, and the suite's default writer is a human.

    Each real lane installs its own actor in production code — an agent turn via
    `tool_loop`, an HTTP request via `backend/auth.py`, a worker/seed/script at its
    own edge. What no lane can supply is the identity a test injects into another
    layer to drive a write through it (the ingest, the search call, a service
    called directly), so this fixture is that stand-in and the suite's one
    default. It is also the switch a lane test turns off: a module that defines
    its own `page_actor` fixture shadows this one, which is how a test proves a
    lane declares its actor rather than inheriting a courtesy one. A test that
    needs a *named* human instead of an anonymous one overrides it too, which is
    the whole reason the injection point is a fixture and not a value buried in
    `_log_page_edit`. See docs/page-provenance.md.

    Installed through a token and reset on teardown because an ASGI request runs in
    its caller's context: without the reset, the actor a test's request installed
    would still be installed for the tests that follow it, and a later assertion
    about a *missing* actor would pass on that leftover.
    """
    token = install(PageActor.human())
    try:
        yield PageActor.human()
    finally:
        uninstall(token)


@pytest.fixture
def read_page_attribution(_db_pool):
    """Read back who a page write credited — the one place a lane test may.

    Pass the coroutine that performs the write; the helper runs it and answers
    with the exception it raised (a lane with no declared actor fails instead of
    writing empty attribution) or with the `page_edits` rows it left as
    `(agent_name, edited_by, session_id)`. Returning rows for the whole test
    rather than one page is deliberate: a seed lane writes many pages and the
    claim is that *every* one of them names the same writer.
    """

    async def read(write, *, page_id=None):
        try:
            await write()
        except Exception as exc:  # the write's own failure IS the finding
            return exc
        sql = "SELECT agent_name, edited_by, session_id FROM page_edits"
        args: list = []
        if page_id is not None:
            args.append(page_id)
            sql += " WHERE page_id = $1"
        rows = await _db_pool.fetch(sql + " ORDER BY created_at", *args)
        return [tuple(r) for r in rows]

    return read


@pytest.fixture(autouse=True)
def _no_first_day_curator_dispatch(monkeypatch):
    """Every event/transcript ingest fires a first-day curator check via
    Celery, and tests have no broker — neutralize the dispatch by default.
    Tests that assert on it monkeypatch their own capture over this."""
    from backend.tasks.agent_schedules import first_day_curator_tick

    monkeypatch.setattr(first_day_curator_tick, "delay", lambda *a, **k: None)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup(_db_pool):
    """Truncate all user-data tables after each test for full isolation."""
    yield
    rows = await _db_pool.fetch(
        "SELECT format('%I.%I', schemaname, tablename) AS name "
        "FROM pg_tables "
        "WHERE schemaname = 'public' "
        "AND tablename NOT IN ('alembic_version', 'alembic_version_managed') "
        "ORDER BY tablename"
    )
    table_names = ", ".join(row["name"] for row in rows)
    if table_names:
        await _db_pool.execute(f"TRUNCATE {table_names} CASCADE")


def unique_name(prefix: str = "user") -> str:
    """Generate a unique username safe for use in tests."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --- Shared cloud-agent fixture (mocks the sprite substrate seam) ---
import json as _json  # noqa: E402

from backend.services import agent_auth, sprite_agent_service, sprite_service  # noqa: E402


class FakeRedis:
    """Just enough of redis.asyncio for the per-session turn lock.

    `ttls` records the `ex` each key was set with and outlives the key's delete,
    so a test can read the TTL a lock was taken with after that lock is gone —
    which is the only way to check that a fence was the length of the run it
    bounds. `data` still says exactly what is live right now."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttls: dict[str, object] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.ttls[key] = ex
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        self.data.pop(key, None)


def stream_json_reply(text: str) -> list[str]:
    """A minimal well-formed claude stream-json transcript replying `text`."""
    return [
        _json.dumps({"type": "system", "subtype": "init"}),
        _json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": text},
                },
            }
        ),
        _json.dumps({"type": "result", "subtype": "success", "result": text}),
    ]


@pytest.fixture
def sprite_exec(monkeypatch):
    """Mock the sprite seam: capture exec argv, reply via a queue of canned
    transcripts (default: echo the prompt back).

    The fixture models a developer with a connected Claude key: STAS-131
    removed the no-credential local-mode CLAUDE fallback, so resolution must
    find a real credential to reach the exec seam at all."""
    calls: list[list[str]] = []
    replies: list = []

    async def fake_acquire(user_id):
        return sprite_service.Sprite(name="test-sprite")

    async def fake_credential(user_id, provider=None):
        if provider in (None, "anthropic"):
            return {
                "provider": "anthropic",
                "kind": "api_key",
                "secret": "sk-ant-test-key",
                "models_json": None,
            }
        return None

    async def fake_exec_stream(sprite, argv, *, env, cwd=None):
        calls.append(argv)
        lines, exit_code = (
            replies.pop(0) if replies else (stream_json_reply("Reply to: " + argv[2]), 0)
        )
        for line in lines:
            yield {"stream": "stdout", "data": (line + "\n").encode()}
        yield {"exit_code": exit_code}

    # Turn-setup file writes (credential files, the MCP .mcp.json sync) go
    # through write_file, which execs on the box — capture them instead so
    # they can't consume the canned exec replies.
    writes: list[tuple[str, str]] = []

    async def fake_write_file(sprite, abs_path, contents):
        writes.append((abs_path, contents))

    monkeypatch.setattr(sprite_service, "acquire", fake_acquire)
    monkeypatch.setattr(sprite_service, "exec_stream", fake_exec_stream)
    monkeypatch.setattr(sprite_service, "write_file", fake_write_file)
    fake_redis = FakeRedis()
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: fake_redis)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    monkeypatch.setattr(agent_auth, "_get_credential", fake_credential)

    class Seam:
        pass

    seam = Seam()
    seam.calls, seam.replies, seam.redis, seam.writes = calls, replies, fake_redis, writes
    return seam
