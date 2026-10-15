---
"stash": patch
---

Claude hooks now install by merging into `~/.claude/settings.json`, the same
shape `~/.cursor/hooks.json` gets — the marketplace plugin is no longer the
hook delivery path, because it silently wasn't.

**The failure this fixes:** `~/.claude/settings.json` received no stash hooks
at all on installed machines (the marketplace plugin path never merged them),
and the plugin's hook scripts hard-required `CLAUDE_PLUGIN_DATA` — env only
that plugin sets — so `stash hook run claude on_session_end`, the very command
the hooks carry, died with `KeyError` for anything else invoking it. Net
effect: Claude never emitted `session_end`, and every layer (install output,
status files) looked healthy.

**What users get:** `stash install`/`signin`/`setup` merges the five events
(SessionStart, UserPromptSubmit, PostToolUse async, Stop, SessionEnd) into
`~/.claude/settings.json` from `plugins/claude-plugin/settings.snippet.json`,
preserving every user key and unrelated hook entry, sweeping stale
stash-owned entries by command marker, and idempotent on rerun. An existing
`stash@stash-plugins` marketplace install is retired with one
`claude plugin uninstall` best-effort call — a failing or missing binary is
reported as a note, never an install failure. The hook scripts are now
harness-independent: they read `~/.stash/config.json` through `agent_config`
(data dir `~/.stash/plugins/claude`), so `stash hook run claude <event>`
works identically from Claude Code, the CLI, or a shell.

**What was removed:** `_install_claude_plugin` and
`_enable_marketplace_autoupdate` from the installer, the marketplace
`hooks/hooks.json` and `scripts/ensure_cli.sh` artifacts (the CLI floor check
now belongs to the install entry point), and the `CLAUDE_PLUGIN_DATA` /
`CLAUDE_PLUGIN_USER_CONFIG_*` env surface. No shim reads the old env: one
config format, one codepath. Machines that already trusted the plugin get the
same behavior from settings.json; running `stash signin` (or any install
path) once re-points them and uninstalls the plugin.

**Note:** this repo has no tooling that consumes `.changeset/`;
`CHANGELOG.md`'s Unreleased bullet is the authoritative release note. This
file exists because the task framework mandates a removal record for a
net-negative change — the founder may delete it.
