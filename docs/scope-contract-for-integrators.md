# Scope contract for integrators (`X-Stash-Scope`)

Who this is for: a machine caller that holds an API key and no browser — a
product backend, a CI job, or another Fusion-class integrator — that needs to
keep N isolated stores of memory behind one operator's account. This document
is the contract. The executable version of the sequence below is
`backend/tests/test_scope_integrator_contract.py`; if that file is green, every
promise here is true.

## 1. One tenancy boundary: the `X-Stash-Scope` header

A **scope** is a workspace: a login-less user row whose `user_id` is the owner
of all content written under that scope. The scope's id is the workspace's
`scope_user_id` (a UUID).

- **Value.** Send `X-Stash-Scope: <scope_user_id>` — the UUID of a workspace
  you are a member of — on any content route (sessions, events, event search,
  semantic search, transcripts, files, pages, artifacts). The scope is resolved
  once, in the shared `get_scope` FastAPI dependency (`backend/auth.py`), so
  every content route honors it identically.
- **Absent means personal.** Without the header you act as your own user
  (or, for a machine key minted on a workspace, as that workspace — see §3).
  A missing header can only ever reach the caller's *own* scope. There is
  never a silent fallback into someone else's scope, and never one out of it:
  a header naming a workspace the caller is not a member of is a hard 403, not
  a downgrade to personal.
- **Error contract.**
  - `401` — missing or invalid `Authorization: Bearer <key>` (checked first).
  - `400` — the header value is not a UUID (`"X-Stash-Scope must be a UUID"`).
  - `403` — the header names a workspace the authenticated user is not a
    member of (`"Not a member of that workspace"`).
  - A header equal to the caller's own `user_id` is accepted immediately with
    no membership lookup — the path a workspace-minted machine key takes.
- **Isolation is server-side.** Content routes own/list/create under the
  resolved scope: event routes pin `owner_user_id = <scope>` in SQL
  (`query_scope_events`, `search_scope_events`, `search_scope_events_vector` in
  `backend/services/memory_service.py`) and gate reads through membership. A
  scoped read returns rows of that scope and nothing else; no client-side
  filtering is needed.

This header is the sanctioned boundary for machine callers, not a UI feature:
the Stash CLI, plugin, and MCP server already send it, and the frontend's
scope switcher is just one more client of it.

## 2. Provisioning a scope per project, without an admin

One project, one workspace, one machine key. Run this once per project:

1. `POST /api/v1/me/developer/activate`
   with `{"name": "<project label>"}` and **no `workspace_id`**.
   Returns a fresh one-man, invite-only workspace row
   (`id`, `name`, `domain: null`, `scope_user_id`, `created_by`,
   `external_wiki_folder_id`, `end_user_wikis_folder_id`, `created_at`).
   **Persist `scope_user_id`** (and `id`). This call is *not* idempotent:
   without `workspace_id` it always creates a new workspace, so call it once
   per project and never to recover — see step 4.
2. `POST /api/v1/me/developer/keys`
   with header `X-Stash-Scope: <scope_user_id>` from step 1 and body
   `{"name": "yourapp-<projectId>", "access": "full"}`.
   Returns `{workspace_id, api_key, access, expires_at}`.
   **`access` must be `"full"`.** A `"read"` key may push events
   (`POST /api/v1/me/sessions/events` and `/events/batch` are in the read-key
   allowlist) but is refused (`403`) on
   `POST /api/v1/me/session-folders/get-or-create`, so a `read` key cannot
   resolve project folders. Measured, not guessed.
3. Use the returned `api_key` for every call of that project. It is minted
   *on* the workspace's scope user, so it authenticates **as** that scope:
   `get_scope` short-circuits on its own id and the header is optional for it.
   Sending the header with such a key is harmless; omitting it cannot leak.
4. Recovery and duplicate detection: `GET /api/v1/me/workspaces` lists the
   caller's workspaces (`workspaces[]` with `id`, `name`, `domain`,
   `scope_user_id`, `external_wiki_folder_id`). If you lost a `scope_user_id`,
   find it here; if a project label appears twice, you provisioned twice.

Key shape: **N per-project machine keys, not one shared key.** A leaked
per-project key exposes one project; one shared personal key plus a per-request
header puts every project behind a single secret.

Operational note: keep a per-project workspace single-member for now. Today
the keys route gates on workspace *membership*, not ownership, so any member
can mint a `full` machine key on the scope (measured 2026-11-21). Single-member
workspaces make that moot while the platform posture is decided.

## 3. Membership: who must be in the workspace

- The identity that is tested is **the authenticated one** — the `user_id`
  behind your `Authorization` key. `get_scope` accepts a header equal to that
  id without a lookup, and otherwise requires
  `permission_service.is_workspace_member(scope_user_id, current_user_id)`;
  membership has exactly one definition (`workspace_member_condition`: an
  explicit `workspace_members` row, or a verified email whose domain equals the
  workspace's `domain`).
- A workspace-minted machine key **is** the scope user, so it needs no
  membership row and can never receive a membership 403. A human's personal
  key needs a membership row — and members are not silently starved: the read
  filter (`readable_content_condition` / `readable_session_event_condition`)
  carries an explicit workspace branch, so a member's scoped read returns the
  rows the machine key pushed.
- **No cliff on the path in §2.** A domainless workspace created by
  `developer/activate` adds its creator as an explicit member in the same
  transaction (`workspace_service.create_workspace`), so the provisioning user
  can never be locked out of a scope they just created.
- **The cliff is the admin path.** `POST /api/v1/admin/workspaces` requires a
  domain and records no `created_by`: membership is derived *only* from a
  verified email on that domain, so (a) a user whose email is unverified or
  off-domain is not a member, and (b) there is no human creator who manages
  the workspace. There is no client-repairable remedy —
  `POST /api/v1/admin/workspaces/{id}/members` is admin-token only.
- **Diagnostic:** `GET /api/v1/me/workspaces`. Your scope missing from that
  list means you are not a member; `pending_domain_workspaces` names
  domain-derived workspaces that stay locked until the email is verified.
  Treat a `403` or a missing row as a **provisioning bug, not a retry**:
  nothing a client can call will repair it.

## 4. Rows written before the scope existed

**No — there is no supported move or re-ownership path.** Nothing in the
product changes `history_events.owner_user_id` after insert (the only UPDATEs
touch `embedding`, `content_hash`, `embed_stale`, `created_at`), reads pin that
column to the scope, and `push_event` is an unconditional insert: re-pushing an
identical `(session_id, event_type, created_at)` row into the new scope creates
a second row rather than moving the old one (measured). The moment you start
sending the header, your pre-scope history is invisible to scoped reads — by
design, because that same mechanism is what isolates tenants.

Sanctioned answer: **no history moves; record from now on.** If old rows
matter to you, the honest client-side options are: re-push the rows you still
hold and accept the duplicate, or keep reading the personal scope for the old
window and the workspace scope for the new one.

## 5. Folder filtering is not a boundary

`POST /api/v1/me/session-folders/get-or-create` with an `external_key` remains
a fine way to *organize* a scope's sessions. It is not isolation, and the read
routes will not gain a `session_folder_id` (or `folder_id`, or `scope`)
parameter that carries tenancy: on `/me/sessions/events`, `/events/search` and
`/events/semantic-search` the parameter is today simply ignored (the event read
queries take no folder argument, and `history_events` has no
`session_folder_id` column), and it will stay ignored.

The deciding rule: **a filter the caller can omit is fail-open** — forgetting
it returns every project in the scope at once, which is the exact leak tenancy
is supposed to close. The header's failure mode is strictly narrower: absence
lands in the caller's own scope and never in another tenant's. Folders make it
worse as a tenant edge: they are shared objects (public links, discoverable
serving) and rows move between them (`POST /api/v1/me/session-folders/assign`).
Folder/session filters therefore remain a narrowing convenience, and
**isolation must never be built on a query parameter**.

## What Stash will not do

- Turn folder or session query parameters into a tenancy boundary (§5).
- Build a re-ownership or backfill API for `history_events.owner_user_id` (§4).
- Support one shared credential across projects; §2 mandates per-project keys.

Executable contract: `backend/tests/test_scope_integrator_contract.py`.
Security invariant: see the scope bullet in `docs/security-readiness.md`.
