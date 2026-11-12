# Stash Plugin for Pi

Streams Pi coding sessions to Stash through a Pi 1.x **extension**.

## Prerequisites

- `stash` CLI installed and logged in
- Pi 1.x, which loads TypeScript extensions from its agent dir. Pi's older
  `~/.pi/hooks/` directory is dead: pi warns that hooks were renamed to
  extensions and never runs what is in there.

Streaming is gated globally: it is on whenever you are signed in and have not
stopped recording (`stash stop`). There is no per-repo manifest — the handlers
call into the installed `stashai` package, so the interpreter they need comes
with the CLI rather than being something you install beside it.

## Install

`stash setup --record --agents pi` (or the interactive `stash connect` /
`stash settings` flow) copies `extensions/stash.ts` from the shipped assets under
`stashai/plugin/assets/pi/` into pi's agent dir — `$PI_CODING_AGENT_DIR` or
`~/.pi/agent`, call it `<agentDir>` — as `<agentDir>/extensions/stash.ts`, and
writes the Stash block into `~/.pi/AGENTS.md`. That single file is the whole
install: pi picks up every `*.ts` in `<agentDir>/extensions/` at startup, with no
registration in `settings.json` and no restart ritual beyond starting pi again. The
installer copies bytes and never symlinks, so it keeps working if the stash checkout
or pipx env moves, and it never depends on this repo's location.

Manual install, when you cannot run the CLI installer:

```bash
cd path/to/stash/plugins/pi-plugin
mkdir -p ~/.pi/agent/extensions
cp extensions/stash.ts ~/.pi/agent/extensions/stash.ts

# Agent context — tells Pi it has the stash CLI available
cat AGENTS.md >> ~/.pi/AGENTS.md
```

## How it works

`extensions/stash.ts` is a transport and nothing else. On each pi event it writes
one JSON object to the stdin of `stash hook run pi <event>`, which runs the
matching `on_*.py` handler shipped inside the installed `stashai` package. Every
Stash decision — what to record, how to name pi's fields, when to upload — lives in
that Python, so a package upgrade reaches a machine on its next event and the
extension itself changes almost never.

The three values pi puts on its context object and never on an event — session id,
working directory, transcript path — are read in the extension and sent with the
payload. A payload missing one of its required keys raises in `scripts/adapt.py`;
the adapter never invents a value.

## When it fails

A failure is never silent. The extension has no try/catch: a child process that
cannot start, or that exits non-zero, is announced on stderr: a line beginning
'stash pi hook failed', then the event and the reason. The handler it was talking to records the
same crash in `~/.stash/plugins/pi/upload_status.json` under the `hook_run`
operation — error text, timestamp, and a growing `consecutive_failures` count. That
file is what `stash status` reads, so Pi's row turns `failing` and the traceback is
printed rather than swallowed.

stdout is deliberately dropped: an extension's child process writes to a terminal pi
is also drawing on, and pi reads nothing from it. The upload-health notices the
handlers print on stdout therefore reach a manual `python on_stop.py` run, not a live
pi session — `stash status` is the channel for a live one.

## Commands

Everything is a plain `stash` CLI subcommand — no slash commands or skills:

| Command | Description |
|---------|-------------|
| `stash connect` | Interactive setup (auth + store) |
| `stash settings` | Interactive settings page (streaming, scope, endpoint, …) |
| `stash disconnect` | Pause event streaming across every installed plugin |

## What streams

| Pi 1.x event | Stash event | Notes |
|---|---|---|
| `session_start` | — (warms cache) | Session record created, skills synced |
| `before_agent_start` (user-authored only) | `prompt` | An extension-injected `custom` message is not a prompt |
| `tool_result` | `tool_use` | Finished tool call streamed (bash, read, write, edit, grep, find, …) |
| `agent_settled` | `stop` + transcript upload | The settled turn's content travels in the transcript, uploaded in the background with a 60s cooldown |
| `session_shutdown` | `session_end` | Session finalized, state cleared |

## Known gaps

1. **Pi extension coverage depends on Pi's runtime.** The five events above are
   the ones the extension forwards; pi fires more than it needs to and its
   vocabulary can move between releases. Check pi's own documentation for which
   events fire reliably and whether all tool types are covered.
2. **macOS / Linux only.** Pi runs on macOS and Linux. No Windows support.
   (Consistent with all existing Stash streaming plugins.)

## Retrieval

Pi has shell access. For reads mid-conversation, have the agent invoke
the `stash` CLI. Use `stash vfs` for filesystem-style browsing without an OS mount:

```
stash vfs "find /me -maxdepth 3 -type f"
stash vfs "rg \"database migration\" /me"
stash vfs "cat '/me/README.md'"
stash vfs "cat '/me/sessions/_index.jsonl'"
stash search "<query>"
stash whoami --json
```
