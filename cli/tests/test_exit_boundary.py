"""Top-level entry boundary for the stash CLI (`main`).

Commands that route through `_err` classify transport failures to exit 2 (see
`test_axi_exit_codes.py`). But the re-raise call sites in share/page-create
intentionally re-raise a non-409 StashError, and any raw httpx.TransportError
that escapes the request layer — neither of which `_err` (a `StashError`-only
router) sees. The `main()` entry boundary catches these escaped errors when it
invokes the compiled Typer command with `standalone_mode=False` and routes
them through the same classification and stderr emission, so *any* API-backed
failure surfaces as the classified exit code with stderr (never stdout) and no
raw traceback.
"""

from __future__ import annotations

import io
from pathlib import Path

import click
import httpx
import pytest
import typer

from cli import main
from cli.client import StashError
from cli.exit_codes import (
    EXIT_INTERNAL_ERROR,
    EXIT_SUCCESS,
    EXIT_USER_ERROR,
    TRANSPORT_ERROR_STATUS,
)


def _making_app(exc: Exception) -> typer.Typer:
    """Stand-in for the Typer app `main` compiles: a single command that
    raises the escaped error `main` must catch — exactly what a command body
    that re-raises a StashError produces when the error escapes the entry."""
    t = typer.Typer()

    @t.command()
    def boom() -> None:
        raise exc

    return t


def _run_main(monkeypatch, capsys, argv, app_override=None, client=None) -> tuple:
    """Invoke `main()` with the given argv and capture (exit_code, out, err).

    Keeps the boundary test deterministic: the app is either the real `main.app`
    (pass a `client`) or a stand-in Typer app whose single command raises the
    escaped error directly. A `main()` return without SystemExit is the
    success path (exit 0); a SystemExit carries the exit code.
    """
    monkeypatch.setattr(main, "_require_auth", lambda: None)
    if client is not None:
        monkeypatch.setattr(main, "_client", lambda: client)
    if app_override is not None:
        monkeypatch.setattr(main, "app", app_override)
    monkeypatch.setattr("sys.argv", list(argv))
    try:
        main.main()
        code = EXIT_SUCCESS
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else EXIT_SUCCESS
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- Escaped-error routing through the boundary (deterministic) --------------


def test_escaped_transport_stasherror_surfaces_exit_2(monkeypatch, capsys) -> None:
    """A re-raised transport StashError escaping a command body exits 2."""
    app_override = _making_app(StashError(TRANSPORT_ERROR_STATUS, "connection refused"))
    code, out, err = _run_main(monkeypatch, capsys, ["stash"], app_override=app_override)

    assert code == EXIT_INTERNAL_ERROR
    assert "connection refused" in err
    assert out == ""


def test_escaped_4xx_stasherror_still_exits_1(monkeypatch, capsys) -> None:
    """An HTTP 4xx StashError escaping to the boundary still exits 1."""
    app_override = _making_app(StashError(404, "not found"))
    code, out, err = _run_main(monkeypatch, capsys, ["stash"], app_override=app_override)

    assert code == EXIT_USER_ERROR
    assert "not found" in err
    assert out == ""


def test_escaped_raw_transport_exits_internal(monkeypatch, capsys) -> None:
    """A raw httpx.TransportError escaping to the boundary exits 2."""
    app_override = _making_app(
        httpx.ConnectError("refused", request=httpx.Request("GET", "http://x"))
    )
    code, out, err = _run_main(monkeypatch, capsys, ["stash"], app_override=app_override)

    assert code == EXIT_INTERNAL_ERROR
    assert "refused" in err
    assert out == ""


def test_non_stash_or_transport_error_reraises(monkeypatch, capsys) -> None:
    """Genuine bugs (a non-classified exception) re-raise so a traceback shows."""

    class Boom(Exception):
        pass

    monkeypatch.setattr(main, "app", _making_app(Boom("splat")))
    monkeypatch.setattr("sys.argv", ["stash"])
    with pytest.raises(Boom):
        main.main()


# --- Real command routing through the boundary -------------------------------


class _FakeSearchClient:
    def __init__(self, search_sources):
        self._search_sources = search_sources

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def search_sources(self, *args, **kwargs):
        if isinstance(self._search_sources, Exception):
            raise self._search_sources
        return self._search_sources


def test_real_search_transport_through_entry_exits_internal(monkeypatch, capsys) -> None:
    """The real compiled-command invocation + `_err` path closes at the same
    exit code: a wrapped transport failure on `stash search` exits 2 via the
    entry function, with clean stdout."""
    client = _FakeSearchClient(StashError(TRANSPORT_ERROR_STATUS, "connection refused"))
    code, out, err = _run_main(monkeypatch, capsys, ["stash", "search", "test"], client=client)

    assert code == EXIT_INTERNAL_ERROR
    assert "connection refused" in err
    assert out == ""


def test_real_search_success_through_entry_exits_zero(monkeypatch, capsys) -> None:
    client = _FakeSearchClient(
        {"results": [{"source": "files", "ref": "p1", "name": "Runbook"}], "has_more": False}
    )
    code, out, err = _run_main(monkeypatch, capsys, ["stash", "search", "test"], client=client)

    assert code == EXIT_SUCCESS
    assert "Runbook" in out


def test_missing_argument_usage_error_stays_clean(monkeypatch, capsys) -> None:
    """A missing required argument exits with Click's clean usage error and
    the contextual hint, not a raw traceback.

    ``stash search`` requires a positional ``query``. With ``main()``'s
    ``standalone_mode=False`` invocation, Click raises a ``MissingParameter``
    (a ``UsageError`` subclass) during parsing — before any command body or
    client call runs — instead of rendering the error itself. The boundary
    catch must route that through typer's Rich error panel plus the
    ``Hint:`` line (both stderr) and exit 2, the same exit a dependencyless
    success invocation would, so a missing arg never surfaces as an unhandled
    Click traceback.
    """
    code, out, err = _run_main(monkeypatch, capsys, ["stash", "search"])

    assert code == EXIT_INTERNAL_ERROR
    assert "Missing argument" in err
    assert "Hint:" in err
    assert out == ""


class _FakeShareClient:
    """Drives `stash share` to the upload_transcript re-raise site (the real
    production path): folder/page creation succeed, then upload_transcript
    raises, which share's `except StashError: if status != 409: raise` block
    re-raises for the boundary to catch."""

    def __init__(self, upload_exc):
        self._upload_exc = upload_exc

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def resolve_session(self, ref, trashed=False):
        return {"matched": False, "session_id": ref, "id": ref, "name": None}

    def create_folder(self, name, parent_folder_id=None):
        return {"id": "f1", "name": name}

    def create_page(self, name, content="", folder_id=None, content_type=None):
        return {"id": "p1"}

    def upload_transcript(self, *args, **kwargs):
        if isinstance(self._upload_exc, Exception):
            raise self._upload_exc
        return {"imported": 1}

    def publish_skill_folder(self, folder_id, **kwargs):
        return {"id": "s1", "slug": "x", "title": "x"}


def _wire_share(monkeypatch, tmp_path, jsonl_text) -> Path:
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(jsonl_text)
    monkeypatch.setattr(main, "_find_session_jsonl", lambda sid: jsonl)
    return jsonl


def test_share_reraises_transport_through_entry_exits_internal(
    monkeypatch, capsys, tmp_path
) -> None:
    """The share re-raise site (the original escape path) now exits 2 via the
    boundary, with the failure on stderr and off stdout."""
    jsonl_text = (
        '{"sessionId":"s1","type":"assistant","message":{"role":"assistant","content":"answer"}}\n'
    )
    _wire_share(monkeypatch, tmp_path, jsonl_text)
    client = _FakeShareClient(StashError(TRANSPORT_ERROR_STATUS, "network down"))

    code, out, err = _run_main(
        monkeypatch, capsys, ["stash", "share", "--session", "s1"], client=client
    )

    assert code == EXIT_INTERNAL_ERROR
    assert "network down" in err
    assert "network down" not in out


def test_share_reraises_4xx_through_entry_exits_user_error(monkeypatch, capsys, tmp_path) -> None:
    """A 4xx StashError re-raised by the share site still exits 1 at the
    boundary — no regression in the re-raise classification."""
    jsonl_text = (
        '{"sessionId":"s1","type":"assistant","message":{"role":"assistant","content":"answer"}}\n'
    )
    _wire_share(monkeypatch, tmp_path, jsonl_text)
    client = _FakeShareClient(StashError(404, "not found"))

    code, out, err = _run_main(
        monkeypatch, capsys, ["stash", "share", "--session", "s1"], client=client
    )

    assert code == EXIT_USER_ERROR
    assert "not found" in err
    assert "not found" not in out


# --- User cancellation at the boundary (STAS-153) ----------------------------
#
# `standalone_mode=False` switches off Click's own cancellation handling, and
# `click.exceptions.Abort` subclasses `RuntimeError` — not `UsageError` — so it
# is covered by none of the three error clauses above. Uncaught, a cancellation
# the user asked for re-raises and Python prints a traceback, which is the
# regression against trunk this boundary owns. The invariant: cancel intent ->
# one `Aborted.` line on stderr, nothing added to stdout, exit 1, no traceback.


class _NeverOpenedClient:
    """Client stub that fails loudly if a cancelled command opens a connection.

    `tables delete` confirms *before* `with _client()`, so a decline that is
    genuinely terminal must never build a client. Returning from the abort path
    instead of exiting would fall through to this stub and raise.
    """

    def __enter__(self):
        raise AssertionError("client opened after the user cancelled")

    def __exit__(self, *_args):
        return None


def _run_cancel_command(monkeypatch, capsys, *, stdin_text=None, interrupt_prompt=False) -> tuple:
    """Drive the real `stash tables delete` into its confirmation prompt.

    `stdin_text` replaces stdin with the typed answer (or EOF when empty);
    `interrupt_prompt` makes the prompt's own input function raise
    `KeyboardInterrupt`, which is how Click receives a typed Ctrl-C. Both shapes
    end as `click.exceptions.Abort`.
    """
    if stdin_text is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    if interrupt_prompt:

        def _ctrl_c(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("click.termui.visible_prompt_func", _ctrl_c)
    return _run_main(
        monkeypatch,
        capsys,
        ["stash", "tables", "delete", "tbl-1"],
        client=_NeverOpenedClient(),
    )


def _assert_clean_abort(code: int, out: str, err: str) -> None:
    """The one cancel contract: exit 1, a single `Aborted.` on stderr, no traceback.

    `err` is compared after stripping and counted rather than compared bare:
    Click's prompt teardown can prepend its own blank line to stderr, and that
    blank line is not part of the contract. The prompt itself is written to
    stdout by Click, so stdout is asserted to carry no abort text or traceback
    rather than to be empty.
    """
    assert code == EXIT_USER_ERROR
    assert err.strip() == "Aborted."
    assert err.count("Aborted.") == 1
    assert "Traceback" not in err
    assert "click.exceptions" not in err
    assert "Aborted." not in out
    assert "Traceback" not in out


def test_raw_abort_at_boundary_exits_1(monkeypatch, capsys) -> None:
    """A raw `Abort` reaching the boundary is cancel intent, not a bug.

    Before this catch existed the exception re-raised and the user watched a
    `click.exceptions.Abort` traceback for a cancellation they triggered.
    """
    app_override = _making_app(click.exceptions.Abort())
    code, out, err = _run_main(monkeypatch, capsys, ["stash"], app_override=app_override)

    assert code == EXIT_USER_ERROR
    assert err == "Aborted.\n"
    assert out == ""


def test_declined_confirmation_exits_1_without_traceback(monkeypatch, capsys) -> None:
    """The reported symptom: answering `n` to `stash tables delete` printed a raw
    `click.exceptions.Abort` traceback where trunk printed one abort line.

    Drives the real compiled command, so it covers the production chain —
    `typer.confirm(abort=True)` -> `click.Abort` -> boundary — and proves the
    cancel is terminal: the client stub raises if a delete is attempted.
    """
    code, out, err = _run_cancel_command(monkeypatch, capsys, stdin_text="n\n")

    _assert_clean_abort(code, out, err)


def test_ctrl_c_at_prompt_exits_1_without_traceback(monkeypatch, capsys) -> None:
    """Ctrl-C typed at a confirmation prompt aborts the same clean way.

    Click converts the interrupt at the prompt into `Abort` (click.termui
    `confirm`), so this is the same boundary arrival as a declined answer and
    must not diverge in wording or exit code.
    """
    code, out, err = _run_cancel_command(monkeypatch, capsys, interrupt_prompt=True)

    _assert_clean_abort(code, out, err)


def test_keyboard_interrupt_mid_command_exits_1(monkeypatch, capsys) -> None:
    """Ctrl-C while a command is working exits 1 on the same abort line.

    Typer turns a `KeyboardInterrupt` raised inside a command body into
    `click.exceptions.Exit(130)` (typer/core.py:202-203), which a
    non-standalone invocation *returns* instead of raising — so without
    normalizing the returned code the user got a silent exit 130 with no line.
    """
    app_override = _making_app(KeyboardInterrupt())
    code, out, err = _run_main(monkeypatch, capsys, ["stash"], app_override=app_override)

    _assert_clean_abort(code, out, err)


def test_keyboard_interrupt_during_startup_exits_1(monkeypatch, capsys) -> None:
    """Ctrl-C while the Typer app is being compiled aborts like any other.

    Typer's interrupt conversion lives inside command invocation, so an
    interrupt raised before a command body exists has no converter and escapes
    raw. The boundary's catch is what keeps startup cancellations off the
    traceback path.
    """

    def _interrupt_during_compile(_app):
        raise KeyboardInterrupt

    monkeypatch.setattr("typer.main.get_command", _interrupt_during_compile)
    code, out, err = _run_main(monkeypatch, capsys, ["stash"])

    _assert_clean_abort(code, out, err)
