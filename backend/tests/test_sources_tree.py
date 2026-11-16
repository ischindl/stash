"""The sources tree is the agent's answer to "what do you have access to"
(`stash ls`). Two properties matter beyond plain rendering:

1. EVERY visible source appears — even empty or still-syncing ones.
   A missing source reads as "Stash can't see it", which is the exact
   impression the feature exists to kill.
2. Truncation is marked, never silent. A capped directory must say how much it
   hid, or the tree lies about the company's footprint.
"""

import asyncio
import time
from uuid import uuid4

import pytest

from backend.config import settings
from backend.services import source_service
from backend.services.source_service import build_entry_tree
from cli.client import REQUEST_TIMEOUT_SECONDS


def test_build_entry_tree_nests_paths_into_folders():
    entries = [
        {"path": "docs/api.md", "name": "api.md", "kind": "file"},
        {"path": "docs/guide.md", "name": "guide.md", "kind": "file"},
        {"path": "README.md", "name": "README.md", "kind": "file"},
    ]
    tree = build_entry_tree(entries, depth=3, per_dir=50)
    assert [n["name"] for n in tree] == ["README.md", "docs"]
    docs = tree[1]
    assert docs["kind"] == "folder"
    assert [n["name"] for n in docs["children"]] == ["api.md", "guide.md"]


def test_build_entry_tree_keeps_entry_kind_and_path_on_leaves():
    entries = [{"path": "#eng/2026-06-01/1717.ts", "name": "1717.ts", "kind": "message"}]
    tree = build_entry_tree(entries, depth=3, per_dir=50)
    leaf = tree[0]["children"][0]["children"][0]
    assert leaf["kind"] == "message"
    assert leaf["path"] == "#eng/2026-06-01/1717.ts"


def test_build_entry_tree_leaves_display_document_name_not_path_segment():
    # Gmail-style: the path is an opaque message id; the name is the subject.
    entries = [{"path": "19eb2b5141a3bbed", "name": "Q3 board deck", "kind": "message"}]
    tree = build_entry_tree(entries, depth=3, per_dir=50)
    assert tree[0]["name"] == "Q3 board deck"
    assert tree[0]["path"] == "19eb2b5141a3bbed"


def test_build_entry_tree_trims_to_depth():
    entries = [{"path": "a/b/c/d.md", "name": "d.md", "kind": "file"}]
    tree = build_entry_tree(entries, depth=2, per_dir=50)
    b = tree[0]["children"][0]
    assert b["name"] == "b"
    assert "children" not in b


def test_build_entry_tree_marks_truncation_instead_of_silently_dropping():
    entries = [{"path": f"f{i}.md", "name": f"f{i}.md", "kind": "file"} for i in range(5)]
    tree = build_entry_tree(entries, depth=1, per_dir=3)
    assert len(tree) == 4
    assert tree[-1] == {"name": "", "kind": "truncated", "hidden": 2}


async def test_sources_tree_includes_every_visible_source(monkeypatch):
    from backend.services import files_tree_service, memory_service

    github = {
        "id": "11111111-1111-1111-1111-111111111111",
        "source_type": "github_repo",
        "display_name": "stash",
        "capability": "navigable",
        "sync_status": "idle",
        "last_synced_at": None,
    }

    async def fake_pages(owner_user_id, user_id):
        return [{"id": "p1", "name": "Welcome"}]

    async def fake_sessions(owner_user_id, user_id):
        return [
            {
                "session_id": "s1",
                "agent_name": "claude",
                "title_source": "  Fix the onboarding\nwizard  ",
            },
            {"session_id": "s2", "agent_name": "claude", "title_source": None},
        ]

    async def fake_connected(user_id):
        return [github]

    async def fake_documents(source, prefix="", limit=200):
        # The registry row itself must pass through — list_documents applies
        # the per-source security filters (Slack channel / Gong account
        # allowlists) from it.
        assert source is github
        return [{"path": "docs/api.md", "name": "api.md", "kind": "file"}]

    audits = []

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(files_tree_service, "list_scope_pages", fake_pages)
    monkeypatch.setattr(memory_service, "list_scope_sessions", fake_sessions)
    monkeypatch.setattr(source_service, "list_connected_sources", fake_connected)
    monkeypatch.setattr(source_service, "list_documents", fake_documents)
    monkeypatch.setattr(source_service, "_audit_source_read", fake_audit)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    # Connected sources nest under their provider folder. A sole connection
    # collapses — its documents sit directly in the provider folder.
    assert [s["display_name"] for s in sources] == [
        "Files",
        "Session transcripts",
        "github",
    ]
    session_names = [n["name"] for n in sources[1]["tree"]]
    assert session_names == ["Fix the onboarding wizard", "claude"]
    assert sources[2]["type"] == "provider"
    assert sources[2]["tree"][0]["name"] == "docs"
    assert audits[0]["action"] == "source.tree_listed"


async def test_sources_tree_nests_multiple_connections_under_one_provider(monkeypatch):
    from backend.services import files_tree_service, memory_service

    repos = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "source_type": "github_repo",
            "display_name": "stash",
            "capability": "navigable",
            "sync_status": "idle",
            "last_synced_at": None,
        },
        {
            "id": "22222222-2222-2222-2222-222222222222",
            "source_type": "github_repo",
            "display_name": "plugin",
            "capability": "navigable",
            "sync_status": "idle",
            "last_synced_at": None,
        },
    ]

    async def fake_empty(owner_user_id, user_id):
        return []

    async def fake_connected(user_id):
        return repos

    async def fake_documents(source, prefix="", limit=200):
        return [{"path": "README.md", "name": "README.md", "kind": "file"}]

    async def fake_audit(**kwargs):
        pass

    monkeypatch.setattr(files_tree_service, "list_scope_pages", fake_empty)
    monkeypatch.setattr(memory_service, "list_scope_sessions", fake_empty)
    monkeypatch.setattr(source_service, "list_connected_sources", fake_connected)
    monkeypatch.setattr(source_service, "list_documents", fake_documents)
    monkeypatch.setattr(source_service, "_audit_source_read", fake_audit)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    github = next(s for s in sources if s["display_name"] == "github")
    # Two repos → each is a subfolder under /sources/github, not a sibling of it.
    assert [n["name"] for n in github["tree"]] == ["plugin", "stash"]
    assert github["tree"][0]["source"] == "22222222-2222-2222-2222-222222222222"
    assert github["tree"][0]["children"][0]["name"] == "README.md"
    assert {m["handle"] for m in github["members"]} == {repo["id"] for repo in repos}


# --- The route bounds its own work and names what did not answer -------------
#
# `stash ls` holds the server to 30 s (cli/client.py's shared client) and this
# route used to cost the SUM of every connection it was asked about. Three
# properties keep that from coming back, and each is tested where it is decided:
# the work happens at once, a connection that misses its budget is NAMED rather
# than awaited or silently dropped, and one straggler never fails the listing.


def _source(handle: str, name: str, source_type: str = "github_repo") -> dict:
    return {
        "id": handle,
        "source_type": source_type,
        "display_name": name,
        "capability": "navigable",
        "sync_status": "idle",
        "last_synced_at": None,
    }


def _mount(monkeypatch, connected: list[dict], documents) -> list[dict]:
    """Install the fakes a tree build needs; return the audit-row collector."""
    from backend.services import files_tree_service, memory_service

    async def empty(owner_user_id, user_id):
        return []

    async def fake_connected(user_id):
        return connected

    async def fake_audit(**kwargs):
        audits.append(kwargs)

    audits: list[dict] = []
    monkeypatch.setattr(files_tree_service, "list_scope_pages", empty)
    monkeypatch.setattr(memory_service, "list_scope_sessions", empty)
    monkeypatch.setattr(source_service, "list_connected_sources", fake_connected)
    monkeypatch.setattr(source_service, "list_documents", documents)
    monkeypatch.setattr(source_service, "_audit_source_read", fake_audit)
    return audits


async def test_sources_tree_lists_every_connection_at_once_not_in_a_sum(monkeypatch):
    """Six connections across three providers answer together.

    A serial route answers them in turn, which is how one `ls` reached 48 s on a
    42-connection stack and died past the caller's 30 s. Peak overlap is the
    proof: any serialized tier caps it at 1."""
    in_flight = 0
    peak = 0

    async def slow(source, prefix="", limit=200):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.15)
        in_flight -= 1
        return [{"path": "README.md", "name": "README.md", "kind": "file"}]

    connected = (
        [_source(f"00000000-0000-0000-0000-00000000000{i}", f"repo-{i}") for i in range(2)]
        + [
            _source(f"10000000-0000-0000-0000-00000000000{i}", f"folder-{i}", "google_drive")
            for i in range(2)
        ]
        + [
            _source(f"20000000-0000-0000-0000-00000000000{i}", f"call-{i}", "granola")
            for i in range(2)
        ]
    )
    _mount(monkeypatch, connected, slow)

    started = time.perf_counter()
    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)
    elapsed = time.perf_counter() - started

    assert peak == 6, f"only {peak} of 6 listings were in flight at once"
    assert elapsed < 0.5, f"the route serialized: {elapsed:.2f} s for 6 x 0.15 s"
    # A listing that answered has no gap to report on any provider.
    assert not [s for s in sources if "listing_error" in s]


async def test_the_fan_out_stays_capped_below_the_pool(monkeypatch):
    """Concurrency is a fan-out with a ceiling, not a flood: the reads it starts
    together are capped so the tree cannot eat the pool the rest of the API
    reads from."""
    in_flight = 0
    peak = 0

    async def slow(source, prefix="", limit=200):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return []

    connected = [
        _source(f"30000000-0000-0000-0000-0000000000{i:02d}", f"repo-{i}") for i in range(20)
    ]
    _mount(monkeypatch, connected, slow)

    await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    assert 1 < peak <= source_service.TREE_READ_CONCURRENCY, f"peak reads: {peak}"


async def test_a_connection_past_its_budget_is_named_and_the_rest_still_answers(
    monkeypatch,
):
    monkeypatch.setattr(source_service, "TREE_MEMBER_BUDGET_SECONDS", 0.2)

    async def one_slow(source, prefix="", limit=200):
        if source["display_name"] == "slow-repo":
            await asyncio.sleep(5)
        return [{"path": "README.md", "name": "README.md", "kind": "file"}]

    connected = [
        _source("40000000-0000-0000-0000-000000000001", "slow-repo"),
        _source("40000000-0000-0000-0000-000000000002", "quick-repo"),
    ]
    audits = _mount(monkeypatch, connected, one_slow)

    started = time.perf_counter()
    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0, f"the straggler was awaited to its own end: {elapsed:.1f} s"
    github = next(s for s in sources if s["display_name"] == "github")
    gap = github.get("listing_error")
    assert gap and "slow-repo" in gap and "did not answer" in gap, gap
    slow_folder = next(n for n in github["tree"] if n["name"] == "slow-repo")
    assert slow_folder.get("children") in (None, []), "a straggler must not claim documents"
    assert "slow-repo" in slow_folder["listing_error"]
    quick_folder = next(n for n in github["tree"] if n["name"] == "quick-repo")
    assert [c["name"] for c in quick_folder["children"]] == ["README.md"]
    assert audits[-1]["metadata"]["degraded_providers"] == ["github"]


async def test_a_raising_connection_degrades_one_provider_and_does_not_escape(monkeypatch):
    async def raises(source, prefix="", limit=200):
        if source["source_type"] == "github_repo":
            raise RuntimeError("connection reset by peer")
        return [{"path": "call-1", "name": "call-1", "kind": "file"}]

    connected = [
        _source("50000000-0000-0000-0000-000000000001", "stash"),
        _source("50000000-0000-0000-0000-000000000002", "Q3 planning", "granola"),
    ]
    _mount(monkeypatch, connected, raises)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    github = next(s for s in sources if s["display_name"] == "github")
    # A provider that could not be listed is a gap, not a missing integration:
    # everything that identifies it and lets a caller retry it stays present.
    assert github["type"] == "provider" and github["provider"] == "github"
    assert github["display_name"] == "github"
    assert [m["handle"] for m in github["members"]] == ["50000000-0000-0000-0000-000000000001"]
    assert github["tree"] == []
    assert "stash" in github["listing_error"] and "failed" in github["listing_error"]
    granola = next(s for s in sources if s["display_name"] == "granola")
    assert "listing_error" not in granola
    assert granola["tree"][0]["name"] == "call-1"


async def test_a_budget_that_is_already_gone_names_the_connection_without_awaiting_it(
    monkeypatch,
):
    """The route's deadline is checked before the read, not after: a listing that
    cannot even start is named, so the answer arrives on time instead of late."""
    monkeypatch.setattr(source_service, "TREE_ROUTE_BUDGET_SECONDS", -1.0)
    awaited = 0

    async def never(source, prefix="", limit=200):
        nonlocal awaited
        awaited += 1
        return []

    _mount(monkeypatch, [_source("60000000-0000-0000-0000-000000000001", "stash")], never)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    github = next(s for s in sources if s["display_name"] == "github")
    assert awaited == 0, "the exhausted budget was ignored and the read ran anyway"
    assert "never started" in github["listing_error"]


async def test_more_than_three_missed_connections_are_counted_not_named(monkeypatch):
    monkeypatch.setattr(source_service, "TREE_MEMBER_BUDGET_SECONDS", 0.05)

    async def all_slow(source, prefix="", limit=200):
        await asyncio.sleep(1)
        return []

    connected = [
        _source(f"70000000-0000-0000-0000-00000000000{i}", f"private-repo-{i}") for i in range(5)
    ]
    _mount(monkeypatch, connected, all_slow)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    gap = next(s for s in sources if s["display_name"] == "github")["listing_error"]
    assert "5 of 5 connections" in gap, gap
    assert "private-repo-0" not in gap, "a count that spills into names is not a count"


async def test_the_gap_names_the_budget_at_the_size_it_was_set(monkeypatch):
    """A budget below one second is still a budget the caller was given.

    Rounding it down to `0 s` in the sentence would tell an operator their
    listing was handed no time at all, which is a different diagnosis than the
    one they are reading this to make.
    """
    monkeypatch.setattr(source_service, "TREE_MEMBER_BUDGET_SECONDS", 0.05)

    async def slow(source, prefix="", limit=200):
        await asyncio.sleep(1)
        return []

    _mount(monkeypatch, [_source("71000000-0000-0000-0000-000000000001", "acme/repo")], slow)

    sources = await source_service.sources_tree(uuid4(), uuid4(), depth=3)

    gap = next(s for s in sources if s["display_name"] == "github")["listing_error"]
    assert "0.05 s listing budget" in gap, gap


async def test_a_cancelled_route_still_cancels(monkeypatch):
    """The degraded leg bounds waiting, not aborting.

    When the caller hangs up on the route, the cancellation has to reach them.
    Swallowing it here would turn an abort into a confident short answer that
    happens to be missing a provider.
    """

    async def never(source, prefix="", limit=200):
        await asyncio.sleep(30)
        return []

    _mount(monkeypatch, [_source("80000000-0000-0000-0000-000000000001", "stash")], never)

    route = asyncio.create_task(source_service.sources_tree(uuid4(), uuid4(), depth=3))
    await asyncio.sleep(0.2)  # let it reach the listing it will never finish
    route.cancel()

    with pytest.raises(asyncio.CancelledError):
        await route


def test_the_budgets_sit_under_the_budget_the_caller_already_has():
    """The route's ceiling is only a promise while it stays under the client's:
    cli/client.py gives every request `REQUEST_TIMEOUT_SECONDS`, and this card
    does not raise it."""
    assert (
        source_service.TREE_MEMBER_BUDGET_SECONDS
        < source_service.TREE_ROUTE_BUDGET_SECONDS
        < REQUEST_TIMEOUT_SECONDS
    )
    assert source_service.TREE_READ_CONCURRENCY <= settings.DB_POOL_MAX


def test_the_client_request_timeout_the_ladder_is_measured_against_has_not_moved():
    """The ladder above is a ceiling only while the client's budget is the one measured.

    The two server budgets were cut against this wall clock and the release note
    names it, so both directions of a change carry prose consequences: raising it
    makes the ceilings slack, lowering it makes the ladder a lie. This assertion is
    what forces that decision into a diff instead of a silent edit.
    """
    assert REQUEST_TIMEOUT_SECONDS == 30
