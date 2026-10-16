"""The skill/sharing CLI commands are thin wrappers over client methods.
These lock in the wiring: the right client call with the right arguments, so
the per-person sharing model, the anyone-with-link level, plain folder creation,
skill members/invites, session folders, and source snapshots all reach the
server correctly."""

import json

import httpx
import pytest

from cli import main
from cli.client import StashClient, StashError

# STAS-298 captured this handle off the wire for a folder whose link was granted
# (`bundle-step2-handles.txt`; the same string `cli/tests/test_shares_ls_handle.py`
# replays). The server mints it from PUBLIC_URL, so the CLI must echo it
# byte-identical or say nothing — never build one from parts.
_HANDLE = "http://127.0.0.1:4199/api/v1/mcp/folders/0VmWDNWgYHsIHKRJr6_EWAus3mt0I1CY6aBv5-stsWs"

# The envelope `GET /api/v1/share` answers with once a link is granted.
_LINK_GRANTED = {"shares": [], "general_access": "read", "mcp_url": _HANDLE}

# And the answer once it is revoked: the level drops and the handle is gone.
_LINK_REVOKED = {"shares": [], "general_access": "none", "mcp_url": None}


class _FakeClient:
    def __init__(self, calls: list, link_state: dict | None = None):
        self._calls = calls
        # The server owns the link level and the handle it mints from that level,
        # so this fake moves the two together. A read after a write then answers
        # with what the write actually stored, which is the only way a test can
        # tell a CLI that reports the server apart from one that reports the
        # value it was handed.
        self._link = dict(link_state or _LINK_GRANTED)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    # Session handles resolve server-side; no titled sessions here, so every
    # handle passes through as the id it already is.
    def resolve_session(self, ref, trashed=False):
        self._calls.append(("resolve", ref, trashed))
        return {"matched": False, "session_id": ref, "id": ref, "name": None}

    # object sharing
    def share_object(self, object_type, object_id, email, permission="read", expires_at=None):
        self._calls.append(("share", object_type, object_id, email, permission))
        return {"ok": True, "email": email}

    def unshare_object(self, object_type, object_id, principal_type, principal_id):
        self._calls.append(("unshare", object_type, object_id, principal_type, principal_id))

    def get_share_state(self, object_type, object_id):
        self._calls.append(("share_state", object_type, object_id))
        return dict(self._link)

    def set_general_access(self, object_type, object_id, public_permission):
        self._calls.append(("general_access", object_type, object_id, public_permission))
        self._link["general_access"] = public_permission
        self._link["mcp_url"] = None if public_permission == "none" else _HANDLE
        return {"public_permission": public_permission}

    # plain folders
    def create_folder(self, name, parent_folder_id=None):
        self._calls.append(("create_folder", name, parent_folder_id))
        return {"id": "fold-1", "name": name}

    # skill members + invites + snapshot
    def add_skill_member(self, skill_id, user_id, permission="read"):
        self._calls.append(("add_member", skill_id, user_id, permission))
        return {"user_id": user_id, "permission": permission}

    def list_skill_members(self, skill_id):
        self._calls.append(("members", skill_id))
        return [{"display_name": "Sam", "permission": "admin", "user_id": "u1"}]

    def list_skill_invites(self):
        self._calls.append(("invites",))
        return [
            {
                "skill_title": "Specs",
                "invited_by_display_name": "Sam",
                "permission": "read",
                "id": "inv1",
            }
        ]

    def snapshot_source_into_skill(self, skill_id, source_id, path):
        self._calls.append(("snapshot", skill_id, source_id, path))
        return {"id": "page-1"}


def _wire(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(main, "_require_auth", lambda: None)
    monkeypatch.setattr(main, "_client", lambda: _FakeClient(calls))
    return calls


def _wire_link(monkeypatch, link_state: dict | None = None) -> tuple[list, _FakeClient]:
    """`_wire` for the share-state tests: the caller also gets the fake back, so
    a test can read the state still standing on the server side after the command
    has run."""
    calls: list = []
    client = _FakeClient(calls, link_state)
    monkeypatch.setattr(main, "_require_auth", lambda: None)
    monkeypatch.setattr(main, "_client", lambda: client)
    return calls, client


def test_shares_add_and_remove(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    main.shares_add("folder", "fold-1", "a@b.com", permission="write", as_json=True)
    main.shares_rm("folder", "fold-1", "u9", principal_type="user")
    assert ("share", "folder", "fold-1", "a@b.com", "write") in calls
    assert ("unshare", "folder", "fold-1", "user", "u9") in calls


def test_skill_snapshot_source(monkeypatch) -> None:
    calls = _wire(monkeypatch)
    main.skills_snapshot_source("cart-1", source="src-1", path="specs/auth.md", as_json=True)
    assert ("snapshot", "cart-1", "src-1", "specs/auth.md") in calls


# --- `stash shares link`: the anyone-with-link level, without raw REST -------
#
# STAS-298 defect D-5: no `stash shares` verb could read, grant, or revoke the
# "anyone with the link" level. Its negative control — switch the folder link
# off, prove the MCP handle stops being advertised, switch it back on — was two
# hand-typed REST calls (`step2-negative-revoke-patch.txt` and
# `step2-restore-patch.txt` in `bundle-step2-handles.txt`). Every test below is
# that same chain driven through the product.


def _run_cli(monkeypatch, capsys, argv) -> tuple[int, str, str]:
    """Invoke the real ``main()`` as ``stash <argv>`` (as
    ``cli/tests/test_usage_hints.py`` does) and return (exit_code, stdout,
    stderr). The verb's reachability is part of what is under test: calling the
    command function directly would prove too little, because a verb nobody can
    name on the command line is exactly the defect being closed."""
    monkeypatch.setattr("sys.argv", ["stash", *argv])
    monkeypatch.setattr(main, "_JSON_MODE", False)
    try:
        main.main()
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_shares_link_without_a_permission_only_reads(monkeypatch, capsys) -> None:
    """A read must not write. Someone checking a folder's link level has no
    intent to change it, and a stray write would silently re-publish a share."""
    calls, _ = _wire_link(monkeypatch)
    main.shares_link("folder", "fold-1", permission=None, as_json=True)
    assert json.loads(capsys.readouterr().out) == {
        "object_type": "folder",
        "object_id": "fold-1",
        "general_access": "read",
        "mcp_url": _HANDLE,
    }
    assert [call[0] for call in calls] == ["share_state"]


@pytest.mark.parametrize("level", ["read", "comment", "write", "none"])
def test_shares_link_forwards_the_level_it_was_given(monkeypatch, capsys, level) -> None:
    """The server owns the accepted levels (`share_service._PUBLIC_PERMISSIONS`),
    so the CLI passes the word through instead of keeping a second list that can
    disagree with it."""
    calls, server = _wire_link(monkeypatch)
    main.shares_link("folder", "fold-1", permission=level, as_json=True)
    assert ("general_access", "folder", "fold-1", level) in calls
    assert server._link["general_access"] == level


def test_shares_link_reads_after_writing(monkeypatch, capsys) -> None:
    """The output is the server's answer rather than the requested value: one
    write then one read, in that order, is what makes the printed state worth
    believing."""
    calls, _ = _wire_link(monkeypatch)
    main.shares_link("folder", "fold-1", permission="write", as_json=True)
    assert [call[0] for call in calls] == ["general_access", "share_state"]
    assert json.loads(capsys.readouterr().out)["general_access"] == "write"


def test_shares_link_revoke_reports_the_handle_gone(monkeypatch, capsys) -> None:
    """The rehearsal's revoke, now product-driven. Revoking to "none" is what
    kills the MCP handle, and the report has to show the handle gone and not the
    level alone — otherwise "is the link really off?" still needs a raw read to
    answer."""
    calls, _ = _wire_link(monkeypatch)
    main.shares_link("folder", "fold-1", permission="none", as_json=True)
    assert ("general_access", "folder", "fold-1", "none") in calls
    assert json.loads(capsys.readouterr().out) == {
        "object_type": "folder",
        "object_id": "fold-1",
        "general_access": "none",
        "mcp_url": None,
    }


def test_shares_link_grant_revoke_restore_stays_on_the_cli(monkeypatch, capsys) -> None:
    """STAS-298 step 2 end to end: grant, revoke, prove the handle really is gone,
    restore it. Four CLI calls, no hand-typed HTTP, and the handle is only ever the
    string the server sent — including the step that matters most, a read after the
    revoke, which must not resurrect an address the server stopped serving."""
    calls, _ = _wire_link(monkeypatch, dict(_LINK_REVOKED))
    main.shares_link("folder", "fold-1", permission="read", as_json=True)
    granted = json.loads(capsys.readouterr().out)
    main.shares_link("folder", "fold-1", permission="none", as_json=True)
    revoked = json.loads(capsys.readouterr().out)
    main.shares_link("folder", "fold-1", permission=None, as_json=True)
    still_off = json.loads(capsys.readouterr().out)
    main.shares_link("folder", "fold-1", permission="read", as_json=True)
    restored = json.loads(capsys.readouterr().out)
    assert granted["mcp_url"] == _HANDLE
    assert revoked["mcp_url"] is None
    assert still_off == {
        "object_type": "folder",
        "object_id": "fold-1",
        "general_access": "none",
        "mcp_url": None,
    }
    assert restored["mcp_url"] == _HANDLE
    assert [call[0] for call in calls] == [
        "general_access",
        "share_state",
        "general_access",
        "share_state",
        "share_state",
        "general_access",
        "share_state",
    ]


def test_shares_link_text_output_shows_a_handle_only_when_there_is_one(monkeypatch, capsys) -> None:
    """Human mode has to stay pasteable while never showing an address that does
    not exist: granted prints the server's URL, revoked prints the level and no
    URL-shaped text at all."""
    _wire_link(monkeypatch)
    main.shares_link("folder", "fold-1", permission=None, as_json=False)
    assert _HANDLE in capsys.readouterr().out

    _wire_link(monkeypatch, dict(_LINK_REVOKED))
    main.shares_link("folder", "fold-1", permission=None, as_json=False)
    quiet = capsys.readouterr().out
    assert "none" in quiet
    assert "mcp/folders" not in quiet


def test_shares_link_reachable_from_the_command_line(monkeypatch, capsys) -> None:
    """The D-5 symptom as its finder hit it: `stash shares link` was not a
    command, so the verb's very existence is what this asserts — through Click's
    parser rather than around it."""
    _wire_link(monkeypatch)
    code, out, err = _run_cli(monkeypatch, capsys, ["shares", "link", "folder", "fold-1", "--json"])
    assert "No such command" not in err
    assert code == 0, err
    assert json.loads(out)["mcp_url"] == _HANDLE


def test_shares_link_revoke_from_the_command_line(monkeypatch, capsys) -> None:
    """The hand-typed PATCH the rehearsal was forced into — set general-access to
    "none" — is now an argv invocation that exits 0."""
    calls, _ = _wire_link(monkeypatch)
    code, out, err = _run_cli(
        monkeypatch,
        capsys,
        ["shares", "link", "folder", "fold-1", "--permission", "none", "--json"],
    )
    assert code == 0, err
    assert ("general_access", "folder", "fold-1", "none") in calls
    assert json.loads(out)["general_access"] == "none"


def test_shares_link_help_offers_only_the_types_that_can_carry_a_link(monkeypatch, capsys) -> None:
    """`shares add` reaches a session or a source; `shares link` does not — the
    server's general-access table covers folder, page, file and table alone. The verb
    therefore must not inherit the sharing family's type list: help naming a session
    or a source promises a link the server then refuses.
    """
    _wire_link(monkeypatch)
    code, out, _err = _run_cli(monkeypatch, capsys, ["shares", "link", "--help"])
    assert code == 0
    advertised = " ".join(out.split())
    assert "folder | page | file | table" in advertised
    assert "session" not in advertised
    assert "source" not in advertised


def test_shares_link_leaves_which_types_have_a_link_to_the_server(monkeypatch, capsys) -> None:
    """Only page, file, folder and table get a link; the CLI keeps no copy of that
    list, so both halves of the rule have to arrive from the server: a link-less type
    reads back as `none` rather than as an error the CLI invented, and a write to one
    exits 1 carrying the server's own refusal."""

    class _NoLink(_FakeClient):
        """Shaped like the server for a type outside its general-access table."""

        def get_share_state(self, object_type, object_id):
            self._calls.append(("share_state", object_type, object_id))
            return {"shares": [], "general_access": "none", "mcp_url": None}

        def set_general_access(self, object_type, object_id, public_permission):
            self._calls.append(("general_access", object_type, object_id, public_permission))
            raise StashError(400, "session has no general access link")

    client = _NoLink([])
    monkeypatch.setattr(main, "_require_auth", lambda: None)
    monkeypatch.setattr(main, "_client", lambda: client)

    main.shares_link("session", "sess-1", permission=None, as_json=True)
    assert json.loads(capsys.readouterr().out)["general_access"] == "none"

    code, out, err = _run_cli(
        monkeypatch, capsys, ["shares", "link", "session", "sess-1", "--permission", "read"]
    )
    assert code == 1
    assert "has no general access link" in err
    assert out == ""
    assert ("general_access", "session", "sess-1", "read") in client._calls


# --- plain folder creation (STAS-298 defect D-6) -----------------------------


def test_create_folder_reaches_the_client_as_a_plain_folder(monkeypatch, capsys) -> None:
    """D-6 was filed as "no command creates a plain folder", and the rehearsal
    worked around it with a hand-typed folder POST. The bound command already
    existed; this pins that it is still there and still creates a plain empty
    folder — no parent, no skill flag — so the claim cannot quietly become true
    again."""
    calls = _wire(monkeypatch)
    main.files_create_folder("Docs", parent=None, as_json=True)
    assert ("create_folder", "Docs", None) in calls
    assert json.loads(capsys.readouterr().out)["id"] == "fold-1"


# --- the wire itself: the requests the rehearsal typed by hand ---------------


def _wire_real_client(monkeypatch, answer) -> list[httpx.Request]:
    """`_wire` with the real ``StashClient``, standing down only its transport.
    The fakes above prove which method was called; this proves what leaves the
    machine, so the requests can be compared with the ones STAS-298's operator
    entered by hand."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer(request))

    client = StashClient("https://example.test", api_key="k")
    client._http = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handle)
    )
    monkeypatch.setattr(main, "_require_auth", lambda: None)
    monkeypatch.setattr(main, "_client", lambda: client)
    return seen


def test_shares_link_sends_the_requests_the_rehearsal_typed_by_hand(monkeypatch, capsys) -> None:
    """The whole D-5 claim, checked at the socket boundary: one write of the level,
    one read of the state, to the same endpoint and with the same parameters as the
    hand-typed REST calls — and the handle that comes back is the server's string
    unchanged, not one the CLI built from its base URL."""
    seen = _wire_real_client(
        monkeypatch,
        lambda request: (
            _LINK_GRANTED if request.method == "GET" else {"public_permission": "write"}
        ),
    )
    main.shares_link("folder", "fold-1", permission="write", as_json=True)

    patch, read = seen
    assert (patch.method, patch.url.path) == ("PATCH", "/api/v1/share/general-access")
    assert json.loads(patch.content) == {
        "object_type": "folder",
        "object_id": "fold-1",
        "public_permission": "write",
    }
    assert (read.method, read.url.path) == ("GET", "/api/v1/share")
    assert dict(read.url.params) == {"object_type": "folder", "object_id": "fold-1"}
    assert json.loads(capsys.readouterr().out)["mcp_url"] == _HANDLE
