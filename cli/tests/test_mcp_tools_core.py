"""Command-level wiring tests for eight core agent-facing MCP tools.

What these lock in is the hand-off between a tool function and the
``StashClient`` method it is supposed to call: which method, with which
arguments, in which shape (positional or keyword), and whether a client error
still propagates. A tool that gets rewired to the wrong client method, drops an
argument, or swallows an exception into a friendly envelope fails here instead
of surfacing as an agent that mysteriously does nothing.

Harness rules (follow them when extending this file):

* The fake client is **explicit** — one method per real ``StashClient`` method,
  with a signature copied from ``inspect.signature(StashClient.<method>)``, not
  invented. A signature mismatch with the real client is a defect in the test.
  A ``__getattr__`` catch-all fake is deliberately *not* used here: it silently
  accepts a tool rewired to a client method that does not exist, which is one of
  the two failures this task exists to catch (measured: renaming the call to
  ``list_agents_names`` leaves all 259 pre-existing CLI tests green). The only
  place a catch-all is correct is the error sweep below, where the method name
  is irrelevant because the fake raises on the first touch.
* Every contract test asserts the **exact single recorded call**. "The right
  call happened" is weaker than "this call happened, once, and nothing else" —
  the extra-call and missing-call cases are real regressions.
* Every contract test also asserts the returned JSON equals the canned payload
  verbatim, which locks the ``_json`` passthrough and forbids wrapper keys.

Both client-call shapes in ``cli/mcp_server.py`` are covered by one seam: most
tools chain ``_client().method(...)`` while a few bind ``client = _client()``
first (``stash_restore``, ``stash_purge``, ``stash_delete_page``, ...). Patching
``mcp_server._client`` intercepts both, because both go through it.

Ledger: 10 of 69 agent-facing tools have command-level coverage — 2 by
``test_mcp_server.py`` (``stash_search``, ``stash_vfs``; STAS-152) and 8 by this
file. The other 59 are future increments; until they land, the registration
guard in ``test_mcp_registry.py`` is what protects all 69 at the decorator seam.

Recipe to add the next tool:
1. ``inspect.signature(StashClient.<method>)`` and add that method verbatim to
   ``_FakeClient``, recording ``(name, args, kwargs)`` and returning a payload
   that looks unlike every other payload in the file.
2. Add a contract test asserting the exact recorded call plus the verbatim JSON,
   and add the tool to the error-sweep table with valid minimal arguments.
3. Extend the ledger line above, and keep the covered set out of
   ``test_mcp_server.py`` so the two files never cover the same tool twice.
"""

import json

import httpx
import pytest

from cli import mcp_server


def _wire(monkeypatch) -> list:
    """Replace the single ``_client`` seam with a recording fake; return its log."""
    calls: list = []
    monkeypatch.setattr(mcp_server, "_client", lambda: _FakeClient(calls))
    return calls


class _FakeClient:
    """Recording stand-in for ``StashClient``, mirroring its real signatures.

    Each method logs the positional/keyword split it was called with, so
    positional-vs-keyword drift and blank-string-to-``None`` mapping are both
    observable. Call payloads are deliberately dissimilar: a tool rewired to a
    sibling method is caught by the recorded call *and* by the returned payload.
    """

    def __init__(self, calls: list):
        self._calls = calls

    def whoami(self) -> dict:
        self._calls.append(("whoami", (), {}))
        return {"id": "u-1", "email": "me@example.com", "plan": "pro"}

    def list_agent_names(self) -> list:
        self._calls.append(("list_agent_names", (), {}))
        return ["fusion", "Memory curator"]

    def list_sources(self) -> list:
        self._calls.append(("list_sources", (), {}))
        return [{"source": "files", "name": "Files"}, {"source": "s-9", "name": "Gmail"}]

    def get_page(self, page_id: str) -> dict:
        self._calls.append(("get_page", (page_id,), {}))
        return {"id": page_id, "content": "body text", "content_hash": "sha256:aaa"}

    def update_page(self, page_id: str, **kwargs) -> dict:
        self._calls.append(("update_page", (page_id,), kwargs))
        return {"id": page_id, "content_hash": "sha256:bbb"}

    def create_page(
        self,
        name: str,
        content: str = "",
        folder_id: str | None = None,
        content_type: str = "markdown",
        content_html: str = "",
        html_layout: str | None = None,
    ) -> dict:
        self._calls.append(
            (
                "create_page",
                (),
                {
                    "name": name,
                    "content": content,
                    "folder_id": folder_id,
                    "content_type": content_type,
                    "content_html": content_html,
                    "html_layout": html_layout,
                },
            )
        )
        return {"id": "p-new", "name": name, "app_url": "https://example/p-new"}

    def share_object(
        self,
        object_type: str,
        object_id: str,
        email: str,
        permission: str = "read",
        expires_at: str | None = None,
    ) -> dict:
        self._calls.append(
            (
                "share_object",
                (object_type, object_id, email),
                {"permission": permission, "expires_at": expires_at},
            )
        )
        return {"shared": True, "email": email, "permission": permission}

    def upload_file(self, file_path: str, folder_id: str | None = None) -> dict:
        self._calls.append(("upload_file", (file_path,), {"folder_id": folder_id}))
        return {"kind": "file", "id": "f-1", "app_url": "https://example/f-1"}


def test_whoami_calls_the_client_with_no_arguments(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_whoami())

    assert calls == [("whoami", (), {})]
    assert result == {"id": "u-1", "email": "me@example.com", "plan": "pro"}


def test_list_agents_calls_list_agent_names_not_a_like_named_method(monkeypatch) -> None:
    """The tool name and the client method name differ here, so this is the one
    contract that cannot be satisfied by forwarding a tool's own name."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_list_agents())

    assert calls == [("list_agent_names", (), {})]
    assert result == ["fusion", "Memory curator"]


def test_list_sources_returns_the_envelope_verbatim(monkeypatch) -> None:
    """The discovery entry point must not add wrapper keys around the list."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_list_sources())

    assert calls == [("list_sources", (), {})]
    assert result == [{"source": "files", "name": "Files"}, {"source": "s-9", "name": "Gmail"}]


def test_read_page_passes_the_page_id_positionally(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_read_page("p1"))

    assert calls == [("get_page", ("p1",), {})]
    assert result == {"id": "p1", "content": "body text", "content_hash": "sha256:aaa"}


def test_create_page_turns_a_blank_folder_id_into_none(monkeypatch) -> None:
    """The tool's public default is ``""`` (an empty string is friendlier to an
    MCP client than an omitted optional), while the client wants ``None`` for
    "root". The ``folder_id or None`` mapping is the contract."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_create_page("n"))

    assert calls == [
        (
            "create_page",
            (),
            {
                "name": "n",
                "content": "",
                "folder_id": None,
                "content_type": "markdown",
                "content_html": "",
                "html_layout": None,
            },
        )
    ]
    assert result == {"id": "p-new", "name": "n", "app_url": "https://example/p-new"}


def test_create_page_forwards_a_set_folder_id_untouched(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    mcp_server.stash_create_page("n", content="body", folder_id="fold-7")

    assert calls == [
        (
            "create_page",
            (),
            {
                "name": "n",
                "content": "body",
                "folder_id": "fold-7",
                "content_type": "markdown",
                "content_html": "",
                "html_layout": None,
            },
        )
    ]


def test_edit_page_always_sends_the_cas_hash_and_drops_a_blank_name(monkeypatch) -> None:
    """``expected_content_hash`` is what makes a lost update fail as a 409
    instead of silently clobbering a human's edit, so it must survive even when
    the optional rename is absent — and an empty ``name`` must not be sent as a
    rename-to-nothing."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_edit_page("p1", "new body", "sha256:aaa"))

    assert calls == [
        ("update_page", ("p1",), {"content": "new body", "expected_content_hash": "sha256:aaa"})
    ]
    assert "name" not in calls[0][2]
    assert result == {"id": "p1", "content_hash": "sha256:bbb"}


def test_edit_page_forwards_the_name_when_it_is_set(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    mcp_server.stash_edit_page("p1", "new body", "sha256:aaa", name="Renamed")

    assert calls == [
        (
            "update_page",
            ("p1",),
            {"content": "new body", "expected_content_hash": "sha256:aaa", "name": "Renamed"},
        )
    ]


def test_upload_file_passes_the_path_positionally(monkeypatch) -> None:
    """The operator guidance injected into every agent context tells them to run
    ``stash upload <path>``; this is the MCP equivalent of that hand-off."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_upload_file("/tmp/x.txt"))

    assert calls == [("upload_file", ("/tmp/x.txt",), {"folder_id": None})]
    assert result == {"kind": "file", "id": "f-1", "app_url": "https://example/f-1"}


def test_share_object_mixes_positionals_and_turns_blank_expiry_into_none(monkeypatch) -> None:
    """The client signature mixes positional ids with keyword permission/expiry;
    swapping the positionals or re-keying them is a silent mis-share."""
    calls = _wire(monkeypatch)
    result = json.loads(mcp_server.stash_share_object("page", "p1", "a@b.c"))

    assert calls == [
        ("share_object", ("page", "p1", "a@b.c"), {"permission": "read", "expires_at": None})
    ]
    assert result == {"shared": True, "email": "a@b.c", "permission": "read"}


def test_share_object_forwards_permission_and_expiry(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    mcp_server.stash_share_object(
        "table", "t-2", "a@b.c", permission="write", expires_at="2030-01-01T00:00:00Z"
    )

    assert calls == [
        (
            "share_object",
            ("table", "t-2", "a@b.c"),
            {"permission": "write", "expires_at": "2030-01-01T00:00:00Z"},
        )
    ]


# Valid minimal arguments per covered tool for the fail-loud sweep.
_ERROR_SWEEP = [
    ("stash_whoami", ()),
    ("stash_list_agents", ()),
    ("stash_list_sources", ()),
    ("stash_read_page", ("p1",)),
    ("stash_create_page", ("n",)),
    ("stash_edit_page", ("p1", "new body", "sha256:aaa")),
    ("stash_upload_file", ("/tmp/x.txt",)),
    ("stash_share_object", ("page", "p1", "a@b.c")),
]


@pytest.mark.parametrize(("tool_name", "args"), _ERROR_SWEEP)
def test_client_error_propagates_out_of_every_covered_tool(monkeypatch, tool_name, args) -> None:
    """No tool in ``cli/mcp_server.py`` has a try/except, so a client failure
    must reach the MCP client instead of being reshaped into a friendly envelope.

    Measured negative control: the recording ``_FakeClient`` above never raises,
    so a sweep built on it stays green under a swallowed-error mutation — it has
    no failure to be swallowed. A fake that cannot fail cannot detect a swallowed
    error, which is why this one raises on the first attribute touch.
    """

    class _RaisingClient:
        def __getattr__(self, name):
            def _raise(*_args, **_kwargs):
                raise httpx.HTTPError(f"{name} failed")

            return _raise

    monkeypatch.setattr(mcp_server, "_client", lambda: _RaisingClient())

    with pytest.raises(httpx.HTTPError):
        getattr(mcp_server, tool_name)(*args)
