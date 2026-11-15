"""Who a page write is credited to, and that a write with no writer cannot happen.

`page_edits.edited_by` is the scope OWNER, because a dispatched curator's
credential IS the owner's key — so that column can never tell a curator's edit
from the owner's click. The writer is the `(agent_name, session_id)` pair: a name
plus a session is a dispatched run, a name with no session is a server lane, and
NULL on both is a person. docs/page-provenance.md holds the full table.

These tests pin the three things that make the pair trustworthy:
1. the write cannot land without a writer (no empty-string path, no NULL row);
2. both readers — the `page_edits` history and the page's own last-edit pair —
   name the same writer, and keep naming it after that editor is gone;
3. the operator's probe (`where session_id like 'agent-curate-%'`) counts exactly
   the curator writes and says out loud which writes it leaves out.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import files_tree_service
from backend.services.page_actor import PageActor, install, page_actor, uninstall

from .test_curator import _auth, _register

CURATOR_RUN = "agent-curate-11111111-1111-1111-1111-111111111111-20261006120000"
SCOPED_RUN = "agent-curate-22222222-2222-2222-2222-222222222222-202610061200"
SCHEDULED_AGENT_RUN = "agent-sched-33333333-3333-3333-3333-333333333333-20261006120000"


@pytest.fixture
def no_actor():
    """Install absence — the state a lane that declares no actor runs in.

    The suite's autouse default is a human, so a test that means to prove the
    choke point refuses a writer-less write has to switch that courtesy off first.
    """
    token = install(None)
    try:
        yield
    finally:
        uninstall(token)


async def _folder(client: AsyncClient, key: str, name: str) -> str:
    r = await client.post("/api/v1/me/folders", json={"name": name}, headers=_auth(key))
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _page(client: AsyncClient, key: str, folder_id: str, name: str) -> dict:
    r = await client.post(
        "/api/v1/me/pages/new",
        json={"name": name, "folder_id": folder_id, "content": "v1"},
        headers=_auth(key),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _edit(client: AsyncClient, key: str, page: dict, content: str) -> dict:
    r = await client.patch(
        f"/api/v1/me/pages/{page['id']}",
        json={"content": content, "expected_content_hash": page["content_hash"]},
        headers=_auth(key),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _rows(pool, page_id: str | UUID) -> list[dict]:
    rows = await pool.fetch(
        "SELECT op, agent_name, edited_by, session_id FROM page_edits "
        "WHERE page_id = $1 ORDER BY created_at",
        page_id,
    )
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_a_write_with_no_writer_raises_and_lands_nothing(
    client: AsyncClient, _db_pool, no_actor
):
    """The old failure mode was silence: `edit_agent_name or user_name` let an
    empty attribution through as a row that looked like a person's, so a curator
    pass and a human click were indistinguishable AFTER the fact. There is no
    such row any more — a writer-less write dies inside the write's own
    transaction, which is why the page is gone too and not merely uncredited."""
    key, uid = await _register(client)
    folder_id = UUID(await _folder(client, key, "lane-probe"))

    with pytest.raises(RuntimeError, match="no page-write actor"):
        await files_tree_service.create_page(uid, "orphan.md", uid, folder_id, content="x")

    assert await _db_pool.fetchval("SELECT count(*) FROM pages") == 0
    assert await _db_pool.fetchval("SELECT count(*) FROM page_edits") == 0


@pytest.mark.asyncio
async def test_a_human_write_names_no_agent_and_still_records_its_writer(
    client: AsyncClient, _db_pool
):
    """A person's click is `agent_name IS NULL` with `edited_by` = the owner — the
    pair the operator filters on, and the reason `edited_by` alone is never the
    probe: for a curator write the owner is the same id it is here."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "notes")
    page = await _page(client, key, folder_id, "roadmap.md")
    edited = await _edit(client, key, page, "v2")

    assert [
        (r["op"], r["agent_name"], r["session_id"], r["edited_by"])
        for r in await _rows(_db_pool, page["id"])
    ] == [
        ("create", None, None, uid),
        # The route's content save logs 'update'; 'edit' is the surgical
        # agent-side path (`edit_page`), a different op on the same history.
        ("update", None, None, uid),
    ]
    # The other reader agrees: a page read carries no writer either.
    assert (edited["last_edit_agent_name"], edited["last_edit_session_id"]) == (None, None)


@pytest.mark.asyncio
async def test_a_turns_actor_is_the_run_while_the_owner_stays_the_owner(
    client: AsyncClient, _db_pool
):
    """The half the whole card exists for: the turn's credential resolves to the
    owner, so the ONLY thing that can say "a run did this" is the actor the turn
    installed. It names the run; `edited_by` keeps naming the owner."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "wiki")
    page = await _page(client, key, folder_id, "entities.md")

    async with page_actor(PageActor.agent("Wiki Curator", CURATOR_RUN)):
        await files_tree_service.update_page(
            UUID(page["id"]), uid, uid, content="written by the run"
        )

    create, curated = await _rows(_db_pool, page["id"])
    assert (create["agent_name"], create["session_id"]) == (None, None)
    assert (curated["op"], curated["agent_name"], curated["session_id"]) == (
        "update",
        "Wiki Curator",
        CURATOR_RUN,
    )
    assert curated["edited_by"] == uid, "the run's session names the run; the id names the owner"
    fresh = await files_tree_service.get_page(UUID(page["id"]), uid)
    assert fresh["last_edit_agent_name"] == "Wiki Curator"
    assert fresh["last_edit_session_id"] == CURATOR_RUN


@pytest.mark.asyncio
async def test_both_readers_name_the_same_writer_however_the_write_arrived(
    client: AsyncClient, _db_pool
):
    """Provenance is one read and it does not expire. Three writes by three kinds
    of writer — a person over HTTP, a run through its turn's actor, and a row
    whose page columns were set by raw SQL — must answer identically from the
    history table and from the page itself, because neither reader joins
    `users` and neither enforces a foreign key: `last_edit_agent_name` survives
    its editor being deleted, which is the point.

    The third page has no `page_edits` row at all, so its page-side answer is the
    only record there is: revert the page-read half of `update_page` and that
    assertion is what goes red."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "wiki")

    human_page = await _page(client, key, folder_id, "human.md")
    await _edit(client, key, human_page, "edited by a person")

    run_page = await _page(client, key, folder_id, "by-run.md")
    async with page_actor(PageActor.agent("Wiki Curator", CURATOR_RUN)):
        await files_tree_service.update_page(UUID(run_page["id"]), uid, uid, content="by a run")

    sql_page = await _page(client, key, folder_id, "by-sql.md")
    await _db_pool.execute(
        "UPDATE pages SET last_edit_agent_name = $2, last_edit_session_id = $3 WHERE id = $1",
        sql_page["id"],
        "Wiki Curator",
        CURATOR_RUN,
    )

    by_id = {
        "human": human_page["id"],
        "run": run_page["id"],
        "sql": sql_page["id"],
    }
    page_side = {}
    for label, page_id in by_id.items():
        page = await files_tree_service.get_page(UUID(page_id), uid)
        page_side[label] = (page["last_edit_agent_name"], page["last_edit_session_id"])

    assert page_side == {
        "human": (None, None),
        "run": ("Wiki Curator", CURATOR_RUN),
        # The bare UPDATE wrote the page columns only — and the page still tells
        # the story, which no `users` join and no history row could replace.
        "sql": ("Wiki Curator", CURATOR_RUN),
    }

    run_history = [r for r in await _rows(_db_pool, run_page["id"]) if r["op"] == "update"]
    assert [(r["agent_name"], r["session_id"]) for r in run_history] == [
        ("Wiki Curator", CURATOR_RUN)
    ]
    assert run_history[0]["edited_by"] == uid


@pytest.mark.asyncio
async def test_the_curator_probe_counts_curator_writes_and_names_its_blind_spots(
    client: AsyncClient, _db_pool
):
    """`select count(*) from page_edits where session_id like 'agent-curate-%'` is
    the probe the operator runs, so it must count every curator lane and no other
    kind of write.

    Measured against the id minters in this build (`scheduled_session_prefix`,
    `scoped_curation_service.run`): every curator lane — personal, scoped, and a
    two-phase run's `-digest` session — shares the `agent-curate-` prefix, so none
    is missed. What the probe cannot see is a writer with no session at all: a
    person (NULL), a server lane (a name, NULL session), and a scheduled agent
    that is not a curator (`agent-sched-`). Those are stated here rather than
    hidden, because the query answers "how many curator page writes" and nothing
    else."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "wiki")

    await _page(client, key, folder_id, "human.md")
    personal_run = await _page(client, key, folder_id, "personal-run.md")
    scoped_run = await _page(client, key, folder_id, "scoped-run.md")
    digest_run = await _page(client, key, folder_id, "digest-run.md")
    scheduled_agent = await _page(client, key, folder_id, "scheduled-agent.md")
    server_lane = await _page(client, key, folder_id, "server-lane.md")

    for page, session in (
        (personal_run, CURATOR_RUN),
        (scoped_run, SCOPED_RUN),
        (digest_run, f"{CURATOR_RUN}-digest"),
        (scheduled_agent, SCHEDULED_AGENT_RUN),
    ):
        async with page_actor(PageActor.agent("Wiki Curator", session)):
            await files_tree_service.update_page(UUID(page["id"]), uid, uid, content="run wrote it")
    async with page_actor(PageActor.system("Clip import")):
        await files_tree_service.update_page(
            UUID(server_lane["id"]), uid, uid, content="worker wrote it"
        )

    counted = await _db_pool.fetchval(
        "SELECT count(*) FROM page_edits WHERE session_id LIKE 'agent-curate-%'"
    )
    assert counted == 3, "personal, scoped and digest curator sessions; nothing else"

    excluded = await _db_pool.fetch(
        "SELECT op, agent_name, session_id FROM page_edits "
        "WHERE session_id IS NULL OR session_id NOT LIKE 'agent-curate-%' "
        "ORDER BY created_at"
    )
    assert [(r["agent_name"], r["session_id"]) for r in excluded] == [
        # The six creates: a request with no agent header is a person.
        (None, None),
        (None, None),
        (None, None),
        (None, None),
        (None, None),
        (None, None),
        # A scheduled agent that is not a curator mints `agent-sched-`, so the
        # probe cannot see its writes even though they are machine writes.
        ("Wiki Curator", SCHEDULED_AGENT_RUN),
        # A server lane names itself and has no run session to name.
        ("Clip import", None),
    ]
