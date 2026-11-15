"""Every lane that writes a page names itself, or its write does not happen.

`current_actor()` raising is only worth something if each lane actually installs
an actor, and a lane can only be proved by driving THAT lane: the HTTP request
edge, an agent turn, and a worker/seed/script are four different pieces of code,
and the one thing they must not share is a courtesy actor supplied from above.

So this module switches the suite's human default OFF (the `page_actor` fixture
below shadows the one in conftest.py) and then asks each lane, in turn, who its
writes were credited to. A lane that never declared an actor fails here — and the
first test is the proof that this module's off-switch has teeth, so the tests
below it earn their green rather than inheriting it.

The names asserted here are the ones docs/page-provenance.md lists.
"""

import argparse
import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.routers import demo as demo_router
from backend.services import (
    clip_router,
    demo_service,
    files_tree_service,
    url_import_service,
)
from backend.services.page_actor import current_actor, install, uninstall
from backend.tasks import clips

from .test_admin_discover_skills import ADMIN, _fake_github
from .test_curator import _register

ADMIN_SECRET = ADMIN["X-Admin-Token"]


@pytest.fixture
def page_actor():
    """Switch the suite's human default off for this module.

    Every test here asks which actor a lane installs. With the human default left
    installed, a lane that declares nothing would still write a plausible row and
    each test would pass while crediting the fixture. Installed through a token so
    the absence ends with the test that asked for it.
    """
    token = install(None)
    try:
        yield None
    finally:
        uninstall(token)


@pytest.fixture
def _admin_secret(monkeypatch):
    monkeypatch.setattr("backend.routers.admin.settings.ADMIN_PASSWORD", ADMIN_SECRET)


async def _rows(pool, page_id=None):
    sql = "SELECT agent_name, edited_by, session_id FROM page_edits"
    args = []
    if page_id is not None:
        args.append(page_id)
        sql += " WHERE page_id = $1"
    return [tuple(r) for r in await pool.fetch(sql + " ORDER BY created_at", *args)]


def _load(script_path: str) -> dict:
    """Run one of `scripts/`'s files — a directory of runnable scripts rather than
    an importable package — and hand back its module globals, which is where a
    test can reach both the wrapper and the body it wraps."""
    path = Path(__file__).resolve().parents[2] / script_path
    spec = importlib.util.spec_from_file_location(f"stash_script_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return vars(module)


@pytest.mark.asyncio
async def test_a_lane_that_declared_nothing_writes_nothing(_db_pool):
    """The off-switch proof. If this ever passes with a row written, the shadow
    fixture above stopped shadowing and every lane test below this one is
    vacuous — which is why it runs first and asserts the absence, not a name."""
    owner = UUID(int=1)
    with pytest.raises(RuntimeError, match="no page-write actor"):
        await files_tree_service.create_page(owner, "undeclared.md", owner, None, content="x")
    assert await _rows(_db_pool) == []


@pytest.mark.asyncio
async def test_the_url_import_worker_credits_its_pages_to_the_worker(
    client: AsyncClient, _db_pool, monkeypatch
):
    """The URL-import worker has no request and no turn: nothing upstream could
    have named the write for it, so it declares `Clip import` at its own edge.
    `edited_by` stays the owner — that is the column an operator must not probe."""
    key, uid = await _register(client)
    [import_id] = await url_import_service.create_url_imports(
        owner_user_id=uid,
        created_by=uid,
        items=[{"url": "https://example.com/long-read", "title": None}],
    )

    async def answering_fetch(url: str) -> tuple[bytes, str]:
        return (
            b"<html><head><title>Long read</title></head><body><h1>Long read</h1>"
            + b"<p>" * 1
            + ("<p>Some substantial article body with enough words to extract. " * 6).encode()
            + b"</p></body></html>",
            "text/html; charset=utf-8",
        )

    monkeypatch.setattr(clip_router, "_fetch", answering_fetch)
    await clips._process_one(import_id)

    rows = await _rows(_db_pool)
    assert rows, "the worker's import was expected to produce a page"
    assert set(rows) == {("Clip import", uid, None)}, (
        "a server lane names itself and has no run session; edited_by stays the owner"
    )


@pytest.mark.asyncio
async def test_the_demo_seed_credits_its_pages_to_the_seed(_db_pool):
    """`seed_demo` runs from the app lifespan, the janitor path, and this suite's
    own fixture — three entry points, no request behind any of them. Declaring it
    at the entry points would mean declaring it three times, so it declares itself
    once inside `seed_demo` and every page it writes says 'Demo seed'."""
    seeded = await demo_service.seed_demo()
    assert seeded is not None

    rows = await _rows(_db_pool)
    assert rows, "the demo seed writes its knowledge-base page"
    assert {r[0] for r in rows} == {"Demo seed"}
    assert {r[2] for r in rows} == {None}


@pytest.mark.asyncio
async def test_the_anonymous_demo_publish_route_credits_its_page(client: AsyncClient, _db_pool):
    """The demo router is Step 0's fifth lane: `/api/v1/demo/*` has no
    `get_current_user` at all, so it can neither inherit a request actor nor claim
    a turn's. It publishes into the Demo scope, and now says so — a page on the
    landing-page demo is a machine write, and `edited_by` alone used to make it
    look like the Demo user's owner clicked save."""
    await demo_service.seed_demo()

    resp = await client.post(
        "/api/v1/demo/pages",
        json={
            "title": "Attribution probe deck",
            "html": "<html><body><section class='slide'><h1>Hi</h1></section></body></html>",
            "html_layout": "fixed-aspect",
        },
    )
    assert resp.status_code == 201, resp.text

    rows = await _rows(_db_pool, UUID(resp.json()["page_id"]))
    owner_user_id, owner_id = await demo_service.get_demo_scope()
    assert rows == [(demo_router.DEMO_PUBLISH_ACTOR.agent_name, owner_id, None)]
    assert owner_id == owner_user_id, "the Demo scope is one system user; both roles are it"


@pytest.mark.asyncio
async def test_the_discover_import_over_the_admin_route_credits_its_pages(
    client: AsyncClient, _db_pool, monkeypatch, _admin_secret
):
    """`POST /admin/discover-skills/import` is gated by X-Admin-Token, not by
    `get_current_user`, so the request edge installs nothing for it: the route
    declares the same writer the script does. The import is the curator's Discover
    catalog, so its pages are a machine's by definition."""
    _fake_github(monkeypatch, {"cooking/SKILL.md": b"---\nname: Cooking\n---\nBody"})

    resp = await client.post(
        "/api/v1/admin/discover-skills/import",
        json={"repo_url": "https://github.com/acme/skills"},
        headers={"X-Admin-Token": ADMIN_SECRET},
    )
    assert resp.status_code == 200, resp.text

    rows = await _rows(_db_pool)
    assert rows, "the imported skill folder's SKILL.md is a page"
    assert {r[0] for r in rows} == {"GitHub skill import"}
    assert {r[2] for r in rows} == {None}


@pytest.mark.asyncio
async def test_the_runnable_scripts_install_their_actor_before_any_write(_db_pool, monkeypatch):
    """Both lane scripts are `main()` -> wrapper -> work, and the wrapper is where
    the actor goes. Driving the wrapper with its real body swapped for a recorder
    proves the placement without running a seed or a network import: the body sees
    the actor the wrapper declared, and that same body unwrapped cannot write at
    all — which is what a forgotten declaration now costs."""
    cases = [
        (
            "scripts/import_github_skills.py",
            "GITHUB_IMPORT_ACTOR",
            "_import_all_with_actor",
            "_import_all",
            (["https://github.com/acme/skills"], UUID(int=2), UUID(int=3)),
            {"dry_run": False},
        ),
        (
            "scripts/seed_dev_data.py",
            "DEV_SEED_ACTOR",
            "_seed_with_actor",
            "_seed",
            (argparse.Namespace(),),
            {},
        ),
    ]
    for script, actor_name, wrapper_name, body_name, args, kwargs in cases:
        script_globals = _load(script)
        declared = script_globals[actor_name]

        seen: list = []

        async def recorder(*_args, **_kwargs):  # noqa: ANN002, ANN003
            seen.append(current_actor())
            return 0

        monkeypatch.setitem(script_globals, body_name, recorder)
        await script_globals[wrapper_name](*args, **kwargs)

        assert seen == [declared], f"{script} must install its actor around its body"
        assert await _rows(_db_pool) == [], "the recorder wrote nothing; only the actor moved"

        # The same body, unwrapped, is what a forgotten declaration looks like.
        with pytest.raises(RuntimeError, match="no page-write actor"):
            await recorder()
