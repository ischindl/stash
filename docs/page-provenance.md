# Page provenance: who wrote this page

Every write to a page records who made it, in `page_edits`, and the page itself
carries the newest answer in `pages.last_edit_agent_name` /
`pages.last_edit_session_id`. This document is the table you read that data with.

It exists because one column is not the writer. `page_edits.edited_by` is the
**scope owner** — the account whose credential made the call. A dispatched
curator runs on its owner's own key, so its writes and that person's clicks share
one `edited_by`. The writer is the `(agent_name, session_id)` pair.

## The table

| `agent_name` | `session_id` | Who it is |
|---|---|---|
| NULL | NULL | A person, over an HTTP request with no agent headers. |
| a name | a session id | A dispatched run. `session_id` names the run. |
| a name | NULL | A server lane with no run session: a worker, a seed, an import. |

What each column means, and what it must never be asked:

| Column | Meaning |
|---|---|
| `page_id` | The page written. |
| `owner_user_id` | The scope the page lives in. |
| `edited_by` | The scope **owner** — the identity the credential resolved to. Never the writer. |
| `agent_name` | The writer's name, or NULL for a person. |
| `session_id` | The run that wrote it, or NULL. |
| `op` | `create`, `update` (a content save), or `edit` (a surgical agent edit). |
| `created_at` | When. |

## How to ask

The count of curator page writes, which is the question that started this:

```sql
select count(*) from page_edits where session_id like 'agent-curate-%';
```

`agent-curate-` is the prefix every curator run mints its session under — the
personal lane, the workspace-scoped lane, and a two-phase run's `-digest` session
all share it (see `scheduled_session_prefix` and `scoped_curation_service.run`),
so this probe misses no curator write that ran as a session — the one curator write
that does not is named in the list below.

What the probe cannot see is a writer with no session:

- a person (`agent_name IS NULL`, `session_id IS NULL`);
- a server lane — the clip worker, the demo seed, a discover import, or the drain part
  writing its own report page — which names itself but has no run session to name;
- a scheduled agent that is **not** a curator, whose sessions start `agent-sched-`
  and which is correctly excluded from a curator count but is still a machine.

So the query answers "how many curator page writes" and nothing else. For a whole
census of machine writes, drop the pattern and keep the name:
`where agent_name is not null`.

Never ask `edited_by` who wrote a page. It answers with the owner, always.

## The same answer from the other side

A page read carries the newest writer without a join: `last_edit_agent_name` and
`last_edit_session_id` on the page row, written by the same code path that writes
the history row. They are the columns the UI and the API show.

Both are **surviving names**, not foreign keys. `session_id` has no foreign key at
all — a run's session row can be deleted while its writes remain on screen — and
`last_edit_agent_name` stays readable after the agent row behind it is gone. That
is deliberate: provenance that disappears when a bookkeeping row is cleaned up is
not provenance. Nothing here joins `users` or `sessions` to render the answer.

## Where the actor comes from

`backend/services/page_actor.py` holds a `PageActor` (human / agent / system) in a
ContextVar. `files_tree_service._log_page_edit` reads it via `current_actor()`,
which **raises** when nothing is installed. There is no default, no fallback, and
no path that writes an empty attribution: a write with no writer fails inside the
write's own transaction, so the page itself is not created either.

A lane installs its actor at the outermost edge that has no request behind it:

| Lane | Installed by | Names itself |
|---|---|---|
| HTTP request | `backend/auth.py` (`_set_page_actor`, beside `_set_request_via`) | no writer → `PageActor.human()` |
| Agent turn | `tool_loop.stream_tool_loop`, from the turn's `session_id` + `agent_name` | yes |
| URL-import worker | `backend/tasks/clips.py` | `Clip import` |
| Demo seed | `backend/services/demo_service.py` | `Demo seed` |
| Demo publish router (anonymous, no auth dependency) | `backend/routers/demo.py` | `Demo publish` |
| Dev seed script | `scripts/seed_dev_data.py` | `Dev seed` |
| Discover import script | `scripts/import_github_skills.py` | `GitHub skill import` |
| Discover import over the admin route (X-Admin-Token, no `get_current_user`) | `backend/routers/admin.py` | `GitHub skill import` |
| Scoped curator | `backend/services/scoped_curation_service.py` | `Scoped curator` + its run session |
| Shared-wiki privacy cleanup | `backend/services/shared_wiki_cleanup.py` | `Privacy cleanup` |
| Drain part's report page | `backend/tasks/agent_schedules.py` (`_write_part_report_page`) | `curator-drain-part` |

Declare it at that edge and **not** inside a service an HTTP route also reaches.
`clip_service`, `shared_skill_service`, `github_skill_import` and `batch_service`
are all reachable from routes that already have a real request actor; a name
declared inside them would overwrite a credit that was correctly earned, and the
service would stop answering to whoever actually called it.

**A new lane must declare its own actor.** It cannot forget quietly — the first
write it attempts raises `RuntimeError: page_actor: no page-write actor in context`
with the fix in the message. That hardness is the design, not a side effect: the
bug this replaced was silence. `edit_agent_name or user_name` let an empty
attribution through as a row that looked like a person's, which is how a curator
pass and a human click became indistinguishable after the fact.

## The header pair, and what it is not

A run's identity reaches the server through two headers, `X-Stash-Agent-Session`
and `X-Stash-Agent-Id`, sent by the CLI that runs inside the harness:

```
sprite_agent_service injects STASH_AGENT_SESSION + STASH_AGENT_ID into the
harness environment  ->  cli/client.py sends the pair  ->  backend/auth.py resolves it
```

Both values are ASCII by construction — a session id is minted server-side from
ASCII parts, and the agent half is a uuid string. The agent half is an id and not
the display name because a header field value must be encodable on the wire, and
a display name is not: an agent named with an em dash or a Slovak diacritic used
to crash in the CLI's own httpx encoder (`UnicodeEncodeError`, `'ascii' codec`)
before a single byte left the machine, so such an agent could not make one
attributed call at all. The name is what the provenance columns want, so the
server reads it out of the `agents` row itself — the name reaches `page_edits` byte-exact
without ever travelling.

`backend/auth.py` resolves the pair where the credential is resolved — one
primary-key read of `agents` scoped to the calling account, then one run proof on
`history_events` keyed by the name that read returned — and:

- no pair — the request is a human;
- a credential-less request claiming a pair — 400: nothing names the account
  that ran it;
- one header alone — 400. A half pair is a broken injection, and a write credited
  to a session no id can explain is worse than a refused request. The 400 names
  the missing header and the present one;
- an id that is not a uuid — 400 naming `X-Stash-Agent-Id` and quoting the value:
  the injector guarantees a uuid, so anything else is fabricated or stale;
- an id naming no `agents` row of this account — 400 naming the `agents` table
  and the id the caller supplied (never whose account the lookup keyed on);
- a pair that names no `(agent_name, session_id, owner_user_id)` row in
  `history_events` — 400, with the failing check, the table, and both values in
  the message. That check is one indexed lookup (`idx_history_events_agent_session`).

The 400s are self-diagnosing on purpose. The likeliest ways a legitimate claim
starts getting refused: the agent was renamed while its run was in flight (the
proof keys on the name as the `agents` row holds it now, while the run's
`history_events` rows carry the name as of dispatch — one run, until its next
turn), and the day a history-pruning job starts deleting the `history_events`
rows a run's proof lived in. A field 400 nobody can read is how those would
announce themselves. A stale CLI is deliberately not on this list: it never gets
this far, which is the next section's subject.

**The claim is provenance, never permission.** Page authorization stays entirely
in `_check_content_access` and the owner-scoped `WHERE` clauses, unchanged. The
claim is owner-scoped, so the worst a liar achieves is a wrong provenance row
inside their own scope — and in practice a pair stolen from another account is
refused outright, because it names no run of theirs.

**An installed CLI older than this pair fails by generation.** The CLI ships
standalone (`stashai` on PyPI) and a provisioned box installs it once:
`backend/services/sprite_service.py` runs
`command -v stash > /dev/null || python3 -m pip install --user --break-system-packages stashai`,
so a box that already has a `stash` keeps the one it has. The package's own
self-upgrade hook runs only under `uv` — `stashai/plugin/session_upload.py`
returns immediately when `shutil.which("uv")` is empty — so a pip-provisioned box
keeps the build it was handed until a human upgrades it. Which stale generation
reaches which failure follows from where its environment comes from:

- A CLI older than the *pair itself* (older than STAS-459) reads neither
  variable, so it sends nothing. No pair is the human answer, not an error: its
  writes land as `agent_name IS NULL` and look exactly like a person in this
  table. The way to tell is that the page has a `history_events` run behind it
  and no writer.
- A CLI from the **name-pair era** — the generation that sent the pair's second
  half as the agent's display name, before STAS-498 moved it to the agent id —
  fails before the network. The deployed server injects `STASH_AGENT_SESSION` +
  `STASH_AGENT_ID`; that CLI reads the session half, looks for the name half that
  is no longer injected, finds it absent, and its own half-pair guard in
  `cli/client.py` raises on the first call of the turn, reads included. It exits
  `EXIT_INTERNAL_ERROR` (2) with a message naming the two variables its own build
  expects side by side, and no request is ever sent — the lane makes zero
  attributed calls rather than one refused call.
- The server's half-pair 400 naming `X-Stash-Agent-Id` is the narrower case: a
  harness dispatched *before* the server deploy still holds the old environment,
  so its CLI sends `X-Stash-Agent-Session` plus a name header the server no
  longer reads, and the request arrives as a session without an id. It ends with
  that in-flight run.

The cure is the same in each case and it lives on the box: upgrade `stash`.
`X-Stash-Cli-Latest` on every response is the field instrument that names the
lag — the CLI compares its own version to it and prints the difference. Hence the
release is two legs in a fixed order: publish `stashai` to PyPI, converge the CLI
on every box that keeps one, and only then deploy the server. Deploying the
server first puts not-yet-upgraded boxes into the second bullet (see
`docs/trunk-publish-and-deploy.md` for the publishing procedure).

## Tests

- `backend/tests/test_page_provenance.py` — the table, the probe and its blind
  spots, both readers agreeing, and that a write with no writer lands nothing.
- `backend/tests/test_page_write_actors.py` — every lane above proved at its own
  edge, with a guard that a lane declaring nothing writes nothing.
- `backend/tests/test_agent_attribution.py` — the header pair through the real
  auth dependency, every refusal (no credential, half pair, non-uuid id, id
  naming no agent of this account, pair naming no run) each with its own message,
  a U+2014-named agent credited byte-exact while every wire value stays ASCII,
  and the environment a harness child is exec'd with (key provider and a pinned
  self-hosted box alike), asserting the id is injected and the old name variable
  is gone.
- `cli/tests/test_agent_attribution_headers.py` — the CLI half, a drift guard on
  the four names (which are spelled independently at each end) plus the negative
  guard that neither removed spelling reappears, and an encoder-level test that a
  display name cannot cross the wire while an id can.
