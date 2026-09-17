# Per-folder curators with model selection

Status: draft for founder review · 2026-09-04 · verified against `origin/main` (38f3fd1f lineage)

## Why

Curation is one owner-wide timeline with exactly two agents. A single heavy
project (Rozvrh: ~15–20k events) cannot be curated until the owner-wide backlog
(~95k events, ~3 days at current throughput) drains, and compute cannot be
targeted per project. The founder also wants each curator to pick its LLM —
Claude or one of several connected open models.

Goal: **a curator is a configurable object — scope it to a folder, give it a
provider and model, schedule it independently.** Rozvrh-scoped wiki lands in
~12–16 h instead of 3 days.

## What already exists (no invention needed)

- `agents.model_provider` exists; `agent_auth.resolve(user_id, prefer_provider)`
  already selects a connected credential per agent (`backend/routers/developer.py:307`
  calls it with `curator["model_provider"]`).
- Connecting a local endpoint probes `GET {base_url}/models` and **stores the
  model list** (`user_agent_credentials.models_json_enc`,
  `agent_auth.probe_local_endpoint`). That list is the dropdown source.
- `_feed_events` already annotates every event with its session's folder
  (`session_folder` name + id) — the join needed for scoping exists.
- The delta watermark (`agents.curated_through`) and run bookkeeping are
  already **per agent row**, so a third curator gets its own watermark for free.
- Developer-console curator page exists (`frontend/src/app/(app)/developer/curator`)
  with `LocalModelSection.tsx` for endpoint credentials.
- Beat enumerates curator agents generically (`backend/tasks/agent_schedules.py`),
  so extra curator rows are dispatched without scheduler changes.

Constraint to remove: partial unique index `one_curator_per_user_per_wiki ON
agents (user_id, curator_wiki) WHERE is_curator` (migration 0193 lineage).

## Design rulings (proposed)

1. **Folder scoping applies to the external (project-wiki) curator only.**
   The internal/memory curator writes one shared page set (Log, Wiki Index);
   two writers would corrupt it. Internal stays exactly one per workspace.
2. Scoped curators are full `agents` rows with `is_curator = true` — no new
   table. Beat, watermarks, run history, allowance metering come for free.
3. No backwards compatibility: the index swap and new columns are one
   migration; existing two curator rows are untouched (folder `NULL` =
   workspace scope).

## Schema (one forward migration)

- `agents.curator_folder_id uuid NULL REFERENCES session_folders(id)`
- `agents.model_id text NULL` — model override within the provider's
  connected credential (local: picked from `models_json`; other providers:
  ignored unless they grow multi-model credentials)
- Drop `one_curator_per_user_per_wiki`; create
  `CREATE UNIQUE INDEX one_curator_per_scope ON agents
   (user_id, curator_wiki, COALESCE(curator_folder_id, uuid_nil()))
   WHERE is_curator` — one workspace-level + one per folder per wiki kind.
- Folder delete cascades the curator row (decision point D below).

## Feed scoping

- `changes_since(...)` and `_feed_events(...)` gain `folder_id: UUID | None`:
  events filtered through the existing sessions→session_folders join; the
  pages/files lists filtered by the same folder subtree; connected-source
  pointers unchanged in v1.
- `complete_through` (the watermark's "complete through" computation) takes the
  same `folder_id` — a scoped curator must never advance past events its feed
  never saw.
- **Build on top of #1092** ("one feed, two shapes"): the folder filter is a
  further narrowing inside the end-user shape; implementing before that PR
  lands means rewriting the same call sites twice.
- The curator runs `stash changes …` from its server-rendered prompt: add
  `--folder <id>` to the CLI (client bump ships alongside, the #1092 pattern),
  emitted by the scoped prompt.

## Prompt scoping

`render_external_curator_prompt` gains an optional scope: when set, the
end-user roster renders as the single scoped entry and a header states the
feed is already restricted — the agent must not ask for broader data.

## Model selection

- `resolve()` grows `model_id: str | None`. For the local provider, the
  credential secret is `{base_url, model, api_key}`; `model_id` overrides
  `model` for that run. Unknown id → fail loud (NeedsAuth-style), never fall
  back.
- One endpoint row is one **box** (shipped, migration 0209): a user may hold
  many `local` rows, each with its own `id` and a `name` (defaulted to the
  base_url hostname). The one-row-per-provider rule survives only as a partial
  unique index over the key providers (`WHERE provider <> 'local'`), whose
  connect keeps its overwrite semantics; connecting a local endpoint APPENDS.
  `agents.credential_id` is the only endpoint selector on the row: a pin dials
  exactly that box, NULL resolves to the oldest connected local endpoint — the
  row a single-endpoint user already has, so his behaviour is byte-preserved.
  One pin covers every turn of a run: the digest turn and the writer turn dial
  the same `base_url`. A pin must be one of the user's own local endpoint rows
  (validated at write time, not at the next turn).
- A folder curator created with no model selection stores NULL provider/model/
  credential and resolves byte-identically to the workspace curator; PATCH with
  explicit null clears a pin back to inherit.
- An inherited curator that opted into a digest is asymmetric by design: the
  digest turn must be `local`, so it dials the oldest LOCAL endpoint, while its
  writer turn resolves exactly like the workspace curator — the oldest connected
  credential of ANY provider. The two turns may ride different providers; that
  is not a broken pin. The asymmetry is about the digest's **provider** only.
  A model pick is the local provider's shape on **both** halves of a curator, and
  both are therefore refused at the door rather than at the next turn:
  `_validate_pin` refuses `model_id` on a non-local provider, and
  `update_curator`'s digest block refuses `digest_model_id` against a non-local
  `digest_provider` with the run's own wording (400, `digest_model_id only
  applies to the local provider`) — while `digest_provider` alone on any
  configured provider stays saveable. Both checks read the MERGED row, so moving
  a curator that carries a model pick onto a key provider must shed that pick in
  the same write, and a row an older deploy saved can be shed but never
  re-tuned. Those legacy rows still fail loud on the digest turn —
  `resolve`/`_byo_auth` raise the identical wording — because that is the
  accepted end state for a row this door never saw: no migration rewrites them
  and nothing silently drops the pick.
- The `models_json` override rides the default endpoint row (the oldest local
  row a NULL pin resolves to). The save names that row by id — a `provider =
  'local'` predicate would stamp one box's models.json onto every box — so the
  override is a per-row shape, not a merged registry.
- UI: model dropdown from `models_json` of the connected credential, with a
  "probe again" affordance.

## API + UI

Developer router (console-gated, founder's own stack):

- `GET  /me/developer/curators` — list (scope, wiki kind, provider, model,
  watermark, last outcome/error)
- `POST /me/developer/curators` — `{folder_id?, curator_wiki, model_provider?,
  model_id?}`; rejects: scoping an internal curator; duplicate scope
- `PATCH /me/developer/curators/{id}` — provider/model/cron edits
- `DELETE /me/developer/curators/{id}` — refused while `started`; refused if
  it is the last workspace-level internal curator

Console curator page: curator table + "Add curator" form (folder picker over
`session_folders`, wiki-kind radio, provider select from *connected*
credentials, model select from that credential's probe list).

## Scheduling & concurrency

Beat needs no change (generic curator enumeration). The binding limits are:

- heavy-worker celery concurrency (now 2) must be ≥ intended simultaneous
  curator runs → make it a compose/`.env` knob, not a code change;
- all open-model curators share the founder's vLLM GPU — batching gives real
  but sublinear gains (expect ~1.5–2× from parallelism, not 3×). Claude- and
  OpenRouter-backed curators scale independently.

## Tests

- scoped feed excludes another folder's events/pages at SQL level (mirrors
  #1092's isolation test);
- `one_curator_per_scope`: second scoped curator on the same folder rejects,
  a different folder accepts; internal-scoped create rejects;
- `resolve(model_id)` selects the overridden model; unknown model fails loud;
- scoped `complete_through` never advances past filtered-out events (watermark
  independence regression guard);
- beat enumerates a scoped curator (mirror `test_first_day_curator`);
- CRUD endpoints incl. the two refusal guards.

## Rollout

1. Rebase onto #1092 once it lands (its feed split is the substrate).
2. Ship as one PR (migration + service + prompts + CLI flag + console UI +
   tests); dogfood on the founder's stack immediately after.
3. Interim (no code): the running parallel dual-curator chain already covers
   the Rozvrh backlog in ~3 days.

Expected effect: Rozvrh wiki from ~3 days to ~12–16 h (scoped backlog
~15–20k events at 1000/run ≈ 45 min).

## Open decisions

- **A. Model-selection depth** — RESOLVED, both depths shipped (phase 2,
  migration 0209): the model pick works within an endpoint (`model_id`) and the
  endpoint pick works across boxes (`credential_id`, oldest-connected fallback,
  409-with-list delete guard when an agent still references the endpoint).
- **B. Internal curator scoping** — proposed never (shared page set). Confirm.
- **C. Allowance metering for scoped curators** — do they share the free
  monthly curator pool or get their own? (recommend: share; they run on the
  same workspace credits).
- **D. Folder delete** — cascade-delete the scoped curator (recommended) vs
  park it disabled.
- **E. First-day tick** — should new-folder activity also wake a folder's
  scoped curator debounced, or nightly-only (recommended: nightly)?

## Addendum — two-phase runs (shipped as 444f123c / migration 0208)

A curator run spent its tokens rereading transcripts: the expensive model ate
the whole capped feed to change three pages. A two-phase run splits the work.

- `agents.digest_provider` / `digest_model_id` name the read half. NULL keeps
  the single-phase run exactly as before.
- With a digest model set, `run_scheduled` first runs a digest turn: a fast
  model runs the very feed command (`stash changes [--folder X] --since Y
  --json`) and reports ≤700-word extracts with per-session citations. Its
  session carries the run's `agent-curate-` prefix, so the digest transcript
  stays out of the feed.
- The curator's own model then runs the unchanged wiki prompt with the report
  substituted for the feed command — it never reruns `stash changes`.
- An empty digest report fails the run (no writer on emptiness, watermark
  untouched).
- The external curator refuses a digest model (its feed is end-user material:
  a second model would be a second processor of customer data). Workspace
  internal curators may use any configured provider for the digest — provider
  yes, model pick no: `digest_model_id` with a key provider is refused at the
  door, see “Model selection”; folder curators keep the
  self-hosted-endpoint rule for both models.
- Watermark semantics are unchanged: `complete_through` advances through the
  feed the digest read — the two phases are one delivery.

## Shipped surface (STAS-201 / 202 / 203)

The curator controls landed in the **product Settings page**, not the developer
console, and on the owner-facing routes rather than the developer router sketched
under "API + UI" above.

Routes (`backend/routers/files_tree.py:340-438`, dual-mounted so the canonical
`/api/v1/curators` works too):

- `GET    /api/v1/me/curators` — rows carry `curator_folder_id`, `curator_wiki`,
  both models, `credential_id`, `schedule_cron`, watermark, last outcome, and
  `event_backlog` (unread depth).
- `POST   /api/v1/me/curators` — `{folder_id}` scopes it to that project.
- `PATCH  /api/v1/me/curators/{agent_id}`
- `DELETE /api/v1/me/curators/{agent_id}`

UI: `frontend/src/components/settings/CuratorsSection.tsx`, mounted in
`frontend/src/app/settings/page.tsx` under the model section. Typed helpers are
`listCurators` / `createCurator` / `patchCurator` / `deleteCurator` in
`frontend/src/lib/api.ts`.

Where the shipped UI departs from the design above, on purpose:

- **No wiki-kind radio.** A row's scope comes from the server
  (`curator_folder_id` / `curator_wiki`); a person adding a curator picks a
  project, never a wiki.
- **One grouped model picker per row, not provider-then-model.** Its empty option
  writes an explicit-null patch (a field omitted from a PATCH is unchanged), so
  clearing a model actually clears it. A row with no pin resolves to the oldest
  connected endpoint and the picker shows it that way; choosing a model writes the
  endpoint id explicitly.
- **Digest on folder rows only**, fed by the box the row already pins. There is no
  `digest_credential_id` column, so a local digest inherits the row's pin — which
  is also why the digest list cannot offer models from a different box.
- **Idle switch on folder rows only**; workspace rows refuse a null schedule.
- **Folder picker excludes `is_default` and already-curated projects.** The
  workspace curator already reads the Default catch-all's material, so a curator
  scoped to it would duplicate it.
- **Delete is confirm-gated**, matching the repo's other destructive controls.

The endpoints the pickers read come from `GET /api/v1/me/agent-credentials`
(`listModelEndpoints`), whose `local` doc now lists every connected endpoint; a
row unreachable to the last probe arrives with `probe_error` and no model list.
Connect and probe are `POST /api/v1/me/agent-credentials` and
`POST .../local/test`; one endpoint is removed by
`DELETE .../endpoints/{credential_id}`, and a 409 there names the agents still
pinning it.
