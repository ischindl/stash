---
"stash": patch
---

An agent whose name is not pure ASCII could not make one attributed CLI call, and the wire now carries an id so it never has to.

**What broke.** The dispatched-run identity channel put a display string on an
ASCII-only wire. The harness environment carried `STASH_AGENT_SESSION` +
`STASH_AGENT_NAME`, `cli/client.py` echoed them as `X-Stash-Agent-Session` +
`X-Stash-Agent-Name`, and HTTP header field values must be ASCII-encodable. Any
agent whose `agents.name` held a character outside ASCII — the founder stack's
own four curator lanes carried a U+2014 em dash in their names — died inside the
client's own httpx encoder (`UnicodeEncodeError: 'ascii' codec can't encode
character`) before the request left the machine. Not one attributed write was
possible; the lanes spent their whole wall rediscovering the broken channel, and
the only way they ever wrote a page was by giving up attribution.

**What changed.** The identity half of the channel is now the `agents` row's id,
which is ASCII by construction. The harness environment carries `STASH_AGENT_SESSION`
+ `STASH_AGENT_ID`; the CLI sends `X-Stash-Agent-Session` + `X-Stash-Agent-Id`;
`backend/auth.py` resolves the pair where the credential is resolved: one
primary-key read `SELECT name FROM agents WHERE id = $1 AND user_id = $2` fetches
the display name from the database, and the run proof still keys
`(agent_name, session_id, owner_user_id)` against `history_events`, so
`idx_history_events_agent_session` still serves it. `PageActor.agent` is installed
with the name the query returned, so `page_edits.agent_name` and
`pages.last_edit_agent_name` stay byte-identical to the `agents` row — em dashes,
Slovak diacritics and all — while every value either header can carry is ASCII.
The injector (`sprite_agent_service._attribution_env`) validates the id as a uuid
and refuses an unusable one at the injection site, and every lane that starts a
turn — chat, `/run`, the scheduled personal lane, the digest phase, Slack and
Telegram via the channel branch — now supplies the `agents` row's id it already
held; `run_chat` refuses a lane that names no agent at all.

**What was removed, with no alias:** `X-Stash-Agent-Name` and `STASH_AGENT_NAME`.
No branch reads either; there is no deprecated constant, no dual acceptance, no
"use the name when the id is absent" fallback. Percent-encoding or base64 of the
name were rejected on purpose: they keep two spellings of one identity in the
world.

**What did not change:** the schema (no migration — `agents.id` was already the
uuid primary key, `page_edits` keeps its display-name column), the meaning of
`page_edits` and its surviving-names rule, the frontend, `_check_content_access`
and every other authorization path (the claim is provenance, never permission),
`tool_loop`'s in-process actor install for ask-the-stash (it never crosses the
wire), and every lane that declares a system actor.

**The release is two legs, in this order.** Publish `stashai` 0.1.492 to PyPI and
converge the CLI on every box that keeps one, *then* deploy the server. A
provisioned box keeps the `stash` CLI it has (`sprite_service` installs only when
`command -v stash` fails, and the package's self-upgrade hook needs `uv`), so the
ordering decides what an un-upgraded box experiences: a name-pair-era CLI reading
the new environment finds no `STASH_AGENT_NAME`, trips its own half-pair guard,
and exits 2 on its first call — reads included — without ever reaching the
server, so its lane makes zero attributed calls. A harness dispatched before the
server deploy is the transient exception: it still carries the old environment,
so its request arrives as a session header without an id and is refused with a
400 naming `X-Stash-Agent-Id`. `X-Stash-Cli-Latest` on every response names the
lag on both paths.
