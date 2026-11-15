---
"stash": patch
---

Every page write now names who wrote it, and a write that cannot say is refused.

**The problem this closes.** `page_edits.edited_by` is the scope owner, because a
dispatched curator's credential IS the owner's key. So a curator's write and that
person's own click shared one answer, and the columns that could tell them apart —
`agent_name`, `session_id` — were filled by two optional parameters whose only
caller ever supplied them was the in-process agent runtime. Every other lane wrote
NULL into both. `select count(*) from page_edits where session_id like
'agent-curate-%'` was therefore not a count of curator writes at all, and no
operator could tell whether that was true. It was not true: the URL-import worker,
the demo seed, the dev seed script, the Discover import (script and admin route)
and the anonymous `/api/v1/demo` publisher — five lanes — all wrote pages with an
empty attribution, and the empty string used to pass, because the old code was
`edit_agent_name or user_name`.

**What changed.** A page write now has a writer, or it does not happen.
`backend/services/page_actor.py` holds a `PageActor` (human / agent / system) in a
context variable; `_log_page_edit` reads it, and a missing actor raises inside the
write's own transaction — so the page is not created either, rather than being
created uncredited. `create_page`, `update_page` and `edit_page` lost
`edit_session_id` / `edit_agent_name`, and `agent_runtime` lost its two per-turn
context variables and their two readers: one provenance channel instead of three,
so a later lane can no longer supply the one parameter that silently goes NULL.
`update_page`'s `last_edit_*` pair and the realtime `page.updated` payload read the
same actor, which is what makes the page row and the history row tell one story —
and both are surviving names, not foreign keys, so the answer outlives the session
row and the agent row behind it.

**The identity now crosses the box.** A run's own session id and agent name are
injected into the harness environment (`STASH_AGENT_SESSION`,
`STASH_AGENT_NAME`), the CLI sends them back as `X-Stash-Agent-Session` +
`X-Stash-Agent-Name`, and `backend/auth.py` resolves them where the credential is
resolved. No pair means a human. One header alone is a 400 naming the missing one.
A pair must name a run this account actually had — one indexed `history_events`
lookup — or the request is a 400 whose message states the table, both header names
and the values that failed, because the likeliest future cause of a legitimate
refusal is a history-pruning job eating the rows the proof lived in. **The claim is
provenance, never permission**: page authorization is still entirely
`_check_content_access` plus the owner-scoped `WHERE` clauses, and the claim is
owner-scoped, so its worst abuse is a wrong provenance row inside the liar's own
scope.

**What was removed:** `files_tree_service.create_page` / `update_page` /
`edit_page` no longer accept `edit_session_id` or `edit_agent_name`, and
`agent_runtime._session_ctx` / `_agent_name_ctx` (with `_current_session()` and
`_current_agent_name()`) are gone with no alias. No call site kept either: the
runtime's three tool handlers that passed them now inherit the turn's actor, and
the one test that passed `edit_agent_name=` moved to the new seam (zero hits for
either parameter name outside this card's own prose).

**What did not change:** the schema (no migration — the columns already existed and
`idx_history_events_agent_session` was already there), `_check_content_access` and
every other authorization path, `scoped_curation_service` and
`shared_wiki_cleanup` (both already named themselves correctly and were left
untouched), the frontend (it already read `last_edit_agent_name`, and it now
receives it more often rather than less), the CLI's visible output, and any
released-version constant, so `pyproject.toml` is untouched.

**Three files outside the card's own limit list, said out loud.** `backend/routers/demo.py`
is the fifth anonymous lane Step 0's census found: `/api/v1/demo/*` carries no
`get_current_user` at all, so it has no request actor to inherit and now declares
`Demo publish` at its own handlers. `backend/routers/admin.py` is a sixth, found
while wiring Step 1: `POST /admin/discover-skills/import` is gated by
`X-Admin-Token`, not by `get_current_user`, and reached `github_skill_import` with
no actor installed — it declares the same writer the script does.
`backend/services/sprite_agent_service.py` is where the two environment variables
enter the harness, which is the other half of a channel whose endpoints are the
auth dependency and the CLI. Every lane declaration sits at the outermost edge with
no request behind it, and none sits inside a service an HTTP route also reaches —
`clip_service`, `shared_skill_service`, `github_skill_import` and `batch_service`
stay actor-free so a real request's credit is never overwritten by a lane default.
