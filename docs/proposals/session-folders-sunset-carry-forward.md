# The session_folders sunset must not erase per-project wiki routing

Status: proposal (2026-08-31) — decision record for STAS-155. Landing status, migration numbering,
and feed/emission claims were re-derived per line on 2026-09-02 — read **Landing-status update** at
the end of this file before the prose below.
Owner: Backend Engineer (agent-ff03cfcc), for operator review
Trigger: `backend/migrations/versions/0201_keyed_folders_become_end_users.py` docstring (lines ~14–19) — the future sunset migration's FIRST statement must re-run 0201's session UPDATE sweep, then DROP `session_folders`, `sessions.session_folder_id`, and the legacy read aliases. Two features now read exactly those columns. The sunset itself is unscheduled and externally gated (0190's veto).
Commit target: `docs/proposals/session-folders-sunset-carry-forward.md` (one docs-only commit; this task document is the machine-readable copy).

## Current behavior (verified in code, 2026-08-31)

- **0201 sweep + conversion** (`0201_keyed_folders_become_end_users.py`): each keyed folder (`session_folders.external_key IS NOT NULL`) becomes an `end_users` row carrying the same external id (1:1 per owner — 0143's unique partial index); sessions are stamped `end_user_id`. The sunset's sweep re-run is idempotent (`end_user_id IS NULL` guard); `test_legacy_lane_coexistence.py` pins its properties. **Unkeyed folders are "UI grouping and are left alone" — until the sunset drops them.**
- **0190** (no-op): the drop was vetoed because Heavi's backend and every installed CLI/plugin/extension still write through the folder lane; the cutover must be coordinated.
- **Privacy floor**: `end_users.share_wiki BOOLEAN NOT NULL DEFAULT true` (0189), PATCHable (`end_user_service.update_end_user`, `routers/developer.py:360`), GUI toggle shipped (`frontend/src/components/developer/WikiToggle.tsx`).
- **Curator feed — per line, re-measured 2026-09-02** (`curation_service._feed_events`): it emits
  `user` / `user_share_wiki` via `sessions.end_user_id → end_users` on **both** lines. On the **PR
  line** (`origin/main`) it emits neither `session_folder` nor `session_folder_share_wiki`, so the
  personal curator prompt (`prompts.py:203–209`) documents a per-event `folder` curation signal that
  is **dead text on the PR line**. On the **integration line** (`main`) STAS-128 (`80d592f7`) extended
  that same function: the `_project_share_wiki` helper emits `session_folder_share_wiki` per event and
  `_feed_events` synthesizes a `session_folder` summary item, so a folder signal does exist there — but
  note the key mismatch, recorded in the sub-fact below: the prompt names `folder`, the feed emits
  `session_folder`, on **both** lines. The
  file and helper names to re-derive either claim are in Pin 3 below. STAS-127 (the feed-signal task)
  is archived at step 2/5; its join (`sf.name AS session_folder, … LEFT JOIN`, pr/1088) is not an
  ancestor of either line — the integration line's folder events come from STAS-128, not from STAS-127.
- **STAS-128 — landed on the integration line, still absent from the PR line (re-measured 2026-09-02)**:
  the design revision was `63b5af98` on `fold/assembled-2026-08-29`, adding `session_folders.share_wiki
  BOOLEAN NOT NULL DEFAULT FALSE` via migration **0204** (`0204_session_folders_share_wiki.py`). What
  actually reached local `main` is `80d592f7`, carrying the same migration **renumbered to 0203**
  (`0203_session_folders_share_wiki.py`), and it is not an ancestor of `origin/main`. (The note this
  bullet carried on 2026-08-31 — that `local_models_json` already occupied 0203 — was branch-count
  drift: no such migration file exists on either line. See Pin 2.) Semantics as shipped in its prompt text: an event informs the shared wiki only when BOTH `user_share_wiki` and `project_share_wiki` are true; project opt-in never overrides user opt-out (hard floor); **events with no user follow the project gate only**; unfiled/Default sessions stay eligible (D4 row 1 of 5). Console: GET/PATCH `/developer/projects` — opt-IN is creator-gated (`can_manage_scope`), opt-OUT never blocked for a write member; newly created projects start dark.
- **Other folder-lane surfaces the drop touches**: `session_folder_service.py` (full), `session_service.upsert_session` legacy filing parameter (`session_service.py:25,43`), `routers/session_folders.py` (both routers), `routers/sessions.py:160` (`sf.name AS session_folder_name` join), `routers/transcripts.py` (`session_folder_id` form field), `routers/memory.py` + `memory_service.py` (folder id through ingestion), and `permission_service.py:30` — `session_folder` is a **shareable object type** whose live share rows must be zeroed or migrated before the table disappears.

## The two signals at risk

1. **Project routing bit** — the founder's per-project "feeds shared wiki" decision (`session_folders.share_wiki`, STAS-128).
2. **Project attribution hint** — `session_folder` in the curator change feed plus the prompt guidance (STAS-127 / pr/1088 / `prompts.py:203–209`), including the "Global — approved for learning" trust convention.

## Decision

### Signal 1 lives on the row the sweep already creates: `end_users.share_wiki` (no new column, no new table)

For every session that the project gate can ever touch on the legacy lane, 0201's conversion already makes folder and end user **the same entity** (1:1 via `external_key` + owner). Post-sunset the two-gate model therefore collapses to the gate that already exists on trunk: `end_users.share_wiki`. The founder's routing decision is carried by ANDing it into the floor bit once, before the drop.

**Scored comparison** (higher = better; 0–3):

| Candidate | Schema added | Migration risk | Privacy floor | Console continuity | Total |
|---|---|---|---|---|---|
| **A. `end_users.share_wiki` (chosen)** | 3 (zero) | 3 (one UPDATE + one assert) | 3 (same bit = floor by construction) | 3 (WikiToggle + PATCH already shipped) | **12** |
| B. dedicated `projects` table + `sessions.project_id` | 0 | 0 (new table, new column, new API, second conversion) | 2 | 1 | 3 |
| C. page/folder placement | 1 | 1 (routing inferred from filing behavior) | 0 (a privacy bit must not depend on an agent's filing) | 1 | 3 |
| D. retire the signal, no carry | 3 | 1 (silent state loss for opted-out projects) | 2 (floor survives; founder opt-outs vanish) | 2 | 8 |

- **B rejected**: re-invents the lane 0190/0201 deliberately retired — a new session grouping column immediately after the migration whose whole point is deleting one; contradicts the settled direction (session identity on the dev platform is `end_user_id`).
- **C rejected for routing** (kept for half of signal 2, below): workspace-scoped privacy state cannot live in the owner's personal page tree, and an opt-out must never depend on curator filing behavior succeeding.
- **D rejected**: A is D plus one idempotent UPDATE and one guard rail — strictly better for a few lines of migration.

### The one-time carry, inside the sunset migration (exact order)

1. **Re-run 0201's session UPDATE sweep** — contract-mandated FIRST statement, idempotent.
2. **Widen guard (fail loud)**: abort unless zero —
   ```sql
   SELECT count(*) FROM sessions s
   JOIN session_folders f ON f.id = s.session_folder_id
   WHERE s.end_user_id IS NULL AND f.share_wiki = FALSE
     AND NOT f.is_default;
   ```
   Userless events follow the project gate only (63b5af98's rule; D4 row 1 makes unfiled/Default eligible). The Default exemption keys on `is_default`, never on the name: folder names are not unique (`get_or_create_folder`'s own docstring) and the DB-enforced Default is the partial unique index `session_folders_one_default … WHERE is_default`, so a real dark project folder that happens to be *named* "Default" would otherwise slip out of the guard and widen silently. Sessions whose only gate was a **dark unkeyed folder** have no end-user row to carry into — after the drop they would become unfiled and therefore **eligible**, silently widening exactly what the founder opted out. The invariant "a project opt-in may only ever widen opted-in material" forbids shipping the sunset with this set non-empty: the operator must move those sessions (console "Move to project" / assign endpoint) or delete the dark folders before the migration runs. Do not auto-create end users for personal unkeyed folders — they are UI grouping, not developer-platform identities (category error).
3. **False-wins AND** (the carry):
   ```sql
   UPDATE end_users eu SET share_wiki = FALSE
   FROM session_folders sf
   JOIN workspaces w ON w.scope_user_id = sf.owner_user_id
   WHERE eu.workspace_id = w.id
     AND sf.external_key = eu.external_id
     AND sf.external_key IS NOT NULL
     AND sf.share_wiki = FALSE;
   ```
   Idempotent (re-running is a no-op once applied); touches only keyed folders (the 1:1 population); false-wins matches the AND semantics exactly and can never widen; keyed display-name collisions (two Heavi folders share a name) are safe because the join is on `external_key`, matching 0201's own loop discipline.
4. **Drop** `session_folders`, `sessions.session_folder_id`, the legacy read aliases (`routers/sessions.py:160` join, `transcripts.py` form field, `session_service.upsert_session` filing parameter), and retire the `session_folder` share object type (`permission_service.py:30`) — with a pre-drop assertion that no live `share` rows of that type remain (mirror of step 2's posture).

**Defaults asymmetry**: do not port "new folders dark" to end-user creation in the sunset. New end users keep today's birth default (`share_wiki DEFAULT true`, 0189); the dark-new-projects policy is a pre-sunset console concern and re-cutting it inside a destructive migration would smuggle a product change into a schema drop. The console's opt-IN gating (`can_manage_scope`) and free opt-out move to the end-user PATCH surface as a sunset-time follow-on, out of schema scope.

### Signal 2: attribution survives for free; the trust convention retires and gets a future home

- **Attribution hint**: post-sunset the feed's `user` field **is** the former project name (conversion preserves the folder name onto the end-user row — verified: 0201's loop copies `sf.name`), so per-event project attribution already flows on trunk machinery. The sunset removes the `sf.*` join from `_feed_events` wherever one exists — on the **PR line** there is
no such join to remove, on the **integration line** it ships with STAS-128 — and the external prompt's
two-gate bullets (63b5af98's block) collapse to the single-gate text that existed before STAS-128. The
attribution gap is the same on both lines: the feed's `user` field is joined off `end_user_id` alone, so
a session whose `end_user_id` is null keeps no project attribution on either line.
- **`prompts.py:203–209` (personal curator "folder" bullet)**: dead text on the **PR line**; on the
  **integration line** the *substance* is live (`80d592f7` made the feed emit a folder signal) but the
  *key is still wrong there* — the bullet tells the curator to read `folder`, while the feed emits
  `session_folder`, on **either** line. So the bullet names a field no line emits. Either way it dies at
  the sunset — remove or rewrite it in the sunset PR, otherwise the prompt instructs the curator to read
  a field the feed can no longer emit.
- **"Global — approved for learning" trust convention**: the one genuinely orphaned fragment. Its honest home is candidate C done right later: a wiki-placement annotation in the owner's own folder tree (placement of curated knowledge, not routing of private sessions) — explicitly out of scope for the sunset; recorded so the sunset PR does not quietly keep the prompt bullet alive to preserve it.

## Privacy floor, stated as an invariant

> For every session event e: `shared_wiki(e) ⇒ user_gate(e) ∧ project_gate(e)`. The user gate is the floor; project opt-in only ever widens opted-in users' material; no migration may newly-widen any event.

Collapse-preserving proof sketch: pre-sunset effective value per end user = `eu.share_wiki ∧ folder.share_wiki`; the false-wins AND writes exactly that onto the single surviving bit, and the step-2 guard closes the only population (userless events in dark unkeyed folders) where the drop would otherwise raise effective sharing. Post-sunset the floor and the row are the same column, so a widened event would require flipping the floor bit itself — i.e. an explicit console/API act, which is the intended surface (`developer.py:360`, WikiToggle).

## Trigger condition that makes the sunset schedulable at all

All must hold before the sunset can even be scheduled (0190's veto is external, not technical):
1. Heavi's backend rolled onto the `user_id` lane (coordinated cutover, per 0190's docstring).
2. The whole installed CLI/plugin/extension fleet stopped sending `session_folder` on upload (`transcripts.py` form field): the sweep predicate (`session_folder_id IS NOT NULL AND end_user_id IS NULL`) counts **zero** over a settled window in prod — then the sweep is provably a no-op on the day.
3. Zero live `object_type='session_folder'` share rows (or migrated).
4. The step-2 widen guard passes (dark unkeyed folder sessions moved).
5. Migration number: **next free at run time** — re-measured 2026-09-02, the tip is `0203_session_folders_share_wiki` on the **integration line** (`main`) and `0202_llm_wiki_items` on the **PR line** (`origin/main`), so the next free number differs by line; `0204_session_folders_share_wiki` exists only on `fold/assembled-2026-08-29`, and no `local_models_json` migration file exists on either line (Pin 2); do not write a fixed number into the sunset plan.
6. **Settled on the integration line** (re-measured 2026-09-02): item 4's premise and the surface it blocks on both landed there in `80d592f7` — the null-handling text for a null `project_share_wiki`, and an operator-facing remediation surface (the developer console's per-project toggle). Neither is met on the PR line, and the CLI stays refusal-only on both lines. Evidence in Pin 4 below; until this item holds on the line you are deploying to, item 4 is unmeetable there.

## Hand-off

- STAS-128 PR link line (paste-ready; PR creation stays operator-gated): *"Per-project wiki routing survives the future `session_folders` sunset: the founder's decision ANDs into `end_users.share_wiki` before the drop and the console toggle keeps working — see `docs/proposals/session-folders-sunset-carry-forward.md` (STAS-155)."*
- STAS-128's own design is unchanged by this note (its `session_folders.share_wiki` choice is correct today; the sunset is unscheduled).
- Corrections recorded (per line, re-measured 2026-09-02): task text said migration "0203" → the branch
  artifact was **0204** on `fold/assembled-2026-08-29`, and what landed on the **integration line** is
  that migration **renumbered to 0203**; the **PR line** still has neither. STAS-127 archived at step
  2/5, its feed join on neither line (the integration line's folder events come from STAS-128's
  `80d592f7`). The `folder` prompt bullet is dead text on the PR line and live on the integration line.

## Executor verification addendum (2026-08-31, read from base `b55183be` = `origin/main` `631ff161`)

Every claim above was re-read from this base in the executor session rather than quoted from
the plan. It holds, with the corrections below. The sections above remain the decision of
record; nothing here changes it.

**Numbering census, measured rather than estimated** (`git for-each-ref` over all 1418 refs,
testing `git ls-tree` for each file): trunk's tip is `0202`; `0203_local_models_json` exists
on **17** refs; `0204_session_folders_share_wiki` on **2** (`fold/assembled-2026-08-29`,
`dogfood/internal-email-domains`). Neither 0203 nor 0204 is an ancestor of `origin/main`, so
trigger condition 5's "next free at run time" is load-bearing, not cautionary.
[Census scope, re-measured 2026-09-02: the paragraph above is kept as the record of what was measured
from base `b55183be`. Per line today: the **integration line** (`main`) tip is `0203_session_folders_share_wiki`
and the **PR line** (`origin/main`) tip is still `0202_llm_wiki_items`; `0204_session_folders_share_wiki`
remains branch-only on `fold/assembled-2026-08-29`; and the `0203_local_models_json` file counted on 17
refs never landed on either line — it exists only as D4's expected *table*. The guardrail's conclusion is
unchanged. See Pin 2.]

**One material addition — STAS-128 as written leaves the unfiled case undefined, and the way
it gets resolved decides whether this note's widen guard is even needed.** On
`fold/assembled-2026-08-29`, the feed emits the gate through a LEFT JOIN
(`curation_service.py:269-270` `sf.name AS session_folder, sf.share_wiki AS
project_share_wiki`; `:275` `LEFT JOIN session_folders`), so an unfiled session yields
`project_share_wiki: null` — and the shipped routing rule is unconditional ("an event may
inform the shared wiki only when BOTH its `user_share_wiki` and its `project_share_wiki` are
true", that branch's `prompts.py:378-384`) with **no** text anywhere telling the curator how
to read a null. Two readings follow, and they differ in opposite directions: null-as-true
keeps D4 rows 1/5 (unfiled/Default stay eligible) and is the premise step 2's guard is written
against; null-as-false silently **narrows** every unfiled session in every developer workspace
— which happens to make the guard's population harmless, at the cost of a far larger behavior
change nobody specified. STAS-128 must land the null-handling text before either this note's
guard or D4's own table can be assumed true.
[Settled per line, 2026-09-02: that text landed on the **integration line** inside `80d592f7` — the
personal curator's routing rules now read a null `project_share_wiki` as if the account had exactly one
project, i.e. the null-as-true reading, which is this note's guard's premise. On the **PR line** the text
is still absent and the null-as-false narrowing risk remains live there. See Pin 4.]

**The remediation path step 2's guard points at does not exist on trunk.** The guard says the
operator must move the affected sessions or delete the dark folders; server-side that is
`POST /api/v1/me/session-folders/assign` (`routers/session_folders.py:163`) and
`/get-or-create`, but the CLI explicitly refuses the operation ("Sessions can't be moved —
session folders were removed with the developer platform work", `cli/main.py:3801-3802`), and
the console's project endpoints exist only on the unlanded branch. So the guard can only ever
pass after STAS-128 lands: the sunset's ordering dependency is not just "the sweep ran", but
"a remediation surface exists for the population the guard blocks on".
[Status pointer, 2026-09-02: this paragraph stays exactly as written — it is dated analysis of base
`b55183be`. The dependency it names is **settled on the integration line**: the console's project
endpoints landed there in `80d592f7` (`backend/routers/developer.py`,
`frontend/src/components/developer/ProjectWikiToggle.tsx`), so the surface the guard blocks on exists
on `main`. It is **still unmet on the PR line**, where those endpoints exist only on a branch, and the
CLI refusal quoted above holds on both lines. See Pin 4 and trigger condition 6.]

**Drop-surface the decision's list omits** (all present on this base; the list above is
accurate but not exhaustive): `session_folder_service.py` is twelve public functions plus
helpers, and `list_folders:183`'s visibility predicate reads `shares` rows at `:190-192` —
that predicate is what step 4's zero-shares assertion protects. `migrations/0082_shares.py:26`
enumerates `session_folder` in the `object_type` column comment (a stale comment post-drop).
Frontend: `frontend/src/app/(app)/sessions/page.tsx:499,598,609` (column and its conditional),
`frontend/src/lib/api.ts:1459`, `frontend/src/lib/sessionGrouping.test.ts:20`. Tests:
`test_session_folder_get_or_create.py` dies with the endpoint, while
`test_legacy_lane_coexistence.py:155-160` is the **specification** for step 1 — it must be
rewritten to assert the sweep ran, not deleted with the rest.
`migrations/0092/0109/0118/0120` also name the table (historical; no action). Retiring the
shareable type is a customer-visible API break and needs a `.changeset/` removal note.

**Corrected at commit time: step 3's carry SQL could not have run.** It joined
`sf.owner_user_id = eu.owner_user_id`, but `end_users` has no `owner_user_id` — `0189` created the
table keyed on `workspace_id` and no later migration adds the column, so the statement fails at
parse time on the day it runs, aborting the sunset. The join is now through `workspaces`, exactly
as the pinned sweep expresses it (`test_legacy_lane_coexistence.py:157-159`:
`JOIN workspaces w ON w.scope_user_id = sf.owner_user_id` then
`JOIN end_users eu ON eu.workspace_id = w.id AND eu.external_id = sf.external_key`), which also
keeps the 1:1 keyed guarantee the surrounding prose relies on. The guard (step 2) and the drop
(step 4) do not touch `end_users` and are unaffected.

Both statements were then **executed**, not just eyeballed, against a scratch Postgres 16
reproducing the real column sets (throwaway container, removed afterwards). The recorded join
fails exactly as predicted — `ERROR: column eu.owner_user_id does not exist`. With the corrected
join, on a fixture of one opted-in keyed folder, one dark keyed folder, and one dark unkeyed
folder each holding a session: step 1 attributes 2 sessions; step 2's guard returns 1 violation —
the dark-unkeyed session, i.e. it fires on precisely the population it was written for; step 3
leaves the opted-in project `true` and flips only the dark one to `false` (narrows, never widens),
and leaves the resulting state unchanged when run again. So steps 1–3 are verified behavior, not
intended behavior.

**Corrected at review: the widen guard keyed its Default exemption off the wrong column.** Step 2
first read `f.name <> 'Default'`, but folder names are not unique (`get_or_create_folder`'s own
docstring says so, and its unkeyed lookup tolerates duplicates via `ORDER BY created_at LIMIT 1`),
while the DB-enforced Default is the partial unique index `session_folders_one_default … WHERE
is_default` (column from 0092, re-keyed to `owner_user_id` by 0119) — the same predicate
`ensure_default_folder` uses. Re-executed against the same scratch shape with one fixture added,
a dark **unkeyed** project folder literally *named* "Default" (`is_default = false`) alongside the
genuine one: the name-based predicate returned 1 violation and passed that project through, so its
sessions would land unfiled and therefore **eligible** after the drop — the silent widening the
invariant forbids — where `NOT f.is_default` returns 2 and still exempts the genuine Default
folder. Step 1, step 3, its idempotent re-run, and the pre-correction join's
`ERROR: column eu.owner_user_id does not exist` all reproduced unchanged.

**A verified asymmetry that strengthens candidate A over B.** `0119:69` dropped
`workspace_id` from `session_folders`, so the legacy lane has never needed a `workspaces` row,
while `end_users.workspace_id` is `NOT NULL` (`0189:47`) and `workspaces` rows come only from
developer activation or `0201` (`user_scope_service.seed_user_scope:67` provisions a curator,
not a workspace). A personal account therefore has no `end_users` row at all, so step 3's
carry is a definitionally empty no-op there — which is correct, because the shared wiki exists
only per workspace (`end_user_service.external_curator_prompt:218`) and there is no
cross-user surface to widen into. Candidate B would have manufactured a grouping entity for
exactly the accounts that have nothing to gate.

**Independent concurrence, and the condition that would overturn it.** Candidate B (a dedicated
`projects` row plus a filing column) is what this session initially favored, because it is the
only candidate able to express a per-project gate for *unkeyed* folders. It was set aside for
the recorded reasons: it re-adds a filing column in the very migration whose purpose is
deleting one, and step 2's guard makes A's single blind spot loud instead of silent, which is
the posture this repo prefers. That concurrence flips on one measurable fact — if unkeyed
filing is heavily used, "move those sessions or delete the dark folders" stops being a guard
and becomes an unexecutable migration, and the project row becomes the cheapest honest home.

**Measurement still owed (prod Neon, read-only; not reachable from this session).** Sessions
per `owner_user_id` with a non-null `session_folder_id`, excluding `Default`, split keyed vs
unkeyed and by folder `share_wiki`; plus a count of live `object_type='session_folder'` share
rows. The first decides whether step 2's guard is passable in practice, the second whether step
4 needs a user-facing note beyond the changeset. `docs/architecture.html` and `docs/testing.md`
were checked and contain no session-folder statements, so no other doc contradicts this note.
[Superseded on the documentation point only — see "Pin 5" in the Landing-status update below.]

## Landing-status update (2026-09-02)

Nothing above is decided differently. What changed is **where things live**, so every factual claim
about landing, migration numbering, and feed emission was re-derived from git and is corrected here
and inline, each tagged with the line it is true on. Read this section before the prose above.

**Vocabulary — and why a sentence in this file can be true and false at the same time.**

- the **integration line** = local `main` (`2d83feb4`), the branch merge commits land on here;
- the **PR line** = `origin/main` (`c56f81c9`), GitHub's `main` — the branch CI gates on and, per
  the repo's deployment notes, the branch hosted prod builds from. Hosted prod therefore still runs
  PR-line code, which is what the trigger list's *prod* measurement is measuring.
- The relation is measured, not assumed: `git rev-list --left-right --count origin/main...main` →
  `29 0`, and `git merge-base --is-ancestor origin/main main` → true. The PR line is a strict
  ancestor of the integration line, which is its **strict descendant** — 29 commits ahead, 0 behind.
  That asymmetry is the entire reason this section exists: a landing is true on the integration line
  and still false on the PR line, so a bare "landed" / "not landed" is now ambiguous. Where the
  sections above say "trunk" without qualification, they meant the PR line, which is what `origin/main`
  was when they were written — and they were accurate then.

**Pin 1 — STAS-128 landed on the integration line; it is still absent from the PR line.** The
descendant commit is `80d592f7` ("STAS-128: add per-project shared-wiki routing to the developer
console"), and it arrived carrying the folder-adjacent migration **renumbered**: the merge ships
`backend/migrations/versions/0203_session_folders_share_wiki.py`, not the `0204_…` file that the
executor addendum saw on `fold/assembled-2026-08-29`. Re-derive: `git merge-base --is-ancestor
80d592f7 main && echo LANDED-on-integration-line`; `git merge-base --is-ancestor 80d592f7
origin/main || echo ABSENT-from-PR-line`; `git show --stat --format=%s 80d592f7`.

**Pin 2 — the migration census needs a per-line tip, and one of its supporting facts never
happened.** Tip per line: `git ls-tree --name-only main:backend/migrations/versions | sort | tail
-3` → tip `0203_session_folders_share_wiki.py`; `git ls-tree --name-only
origin/main:backend/migrations/versions | sort | tail -3` → tip `0202_llm_wiki_items`. The landed
chain is `0201_keyed_folders_become_end_users` → `0202_llm_wiki_items` →
`0203_session_folders_share_wiki` (`git show
main:backend/migrations/versions/0203_session_folders_share_wiki.py | grep -i down_revision`).
**Correction to the census paragraph above:** there is no `local_models_json` **migration file** on
either line — `git ls-tree -r --name-only main | grep -i local_model` and the same over
`origin/main` both return nothing. `local_models_json` exists only as the *table* D4's collision
table expects in the same fold batch (and as a column on `enterprise_organizations`), so the
census's "`0203_local_models_json` exists on **17** refs" counted branch copies that never reached
either line. That is precisely the drift the census's own conclusion warns about, and the
guardrail is vindicated rather than relaxed: the sunset still takes the **next free number at run
time** and no fixed number goes into the plan.

**Pin 3 — the curator feed's folder events are line-conditional, not absent.** The file is
`backend/services/curation_service.py` on both lines. On the **PR line** `_feed_events` emits
`user` / `user_share_wiki` only: no `session_folder` event and no `session_folder_share_wiki`
event, so both prompt consumers reading those fields are dead text **there**. On the **integration
line** `80d592f7` extends the same function: the new `_project_share_wiki` helper contributes a
`session_folder_share_wiki` item per event, and `_feed_events` synthesizes a `session_folder`
summary item (with its wiki `target`) from the session's `session_folder_id`. One naming mismatch
survives on **both** lines: the personal curator bullet tells the curator to read a per-event
`folder`, while the emitted key is `session_folder` — so the bullet's literal key matches nothing on
either line. Re-derive: `git show origin/main:backend/services/curation_service.py | grep -nE "session_folder|_project_share_wiki"`
(no output) vs the same command against `main:` (the helper and both emissions). One consequence
holds on **both** lines and is the reason Signal 2 is still not solved: the feed's `user` field is
joined off `e.get("end_user_id")` alone — never `session_user_id` — so a session with a null
`end_user_id` still carries no attribution whichever line you deploy, and resolving the old
"if pr/1088 landed it" conditional changes nothing about that gap.

**Pin 4 — the two settled gates settled on the integration line only.** The null-handling text the
trigger list waits on is landed on `main`, inside `80d592f7`: the personal curator's routing rules
in `backend/services/prompts.py` now instruct the curator to treat a null `project_share_wiki` as
if the account had exactly one project — the **null-as-true** reading, i.e. the premise step 2's
widen guard is written against, so the guard is still needed and still meaningful. Re-derive:
`git show main:backend/services/prompts.py | grep -n "routes as if there"` and `git log --oneline
main -S "routes as if there" -- backend/services/prompts.py` (→ `80d592f7`). The remediation
surface the guard blocks on also landed in that commit — the developer console's per-project
`GET/PATCH` in `backend/routers/developer.py` and `frontend/src/components/developer/ProjectWikiToggle.tsx`.
Neither gate is met on the PR line, and the CLI is not a remediation surface on **either** line:
it still refuses session moves (`cli/main.py` "Sessions can't be moved — session folders were
removed with the developer platform work") and offers no per-project wiki command. Re-derive:
`git show main:backend/routers/developer.py | grep -n share_wiki` (→ hits) vs the same on
`origin/main:` (→ none).

**Pin 5 — the doc check in "Measurement still owed" needs one correction.** `docs/testing.md` is no
longer clean: on `main` it carries a session-folder row at line 38 (`test_session_folder_share_wiki.py`
— "Per-project shared-wiki opt-in: starts off, only the switch flips it"), added by the same
`80d592f7`; it is still absent from `origin/main`. Re-derive: `git show main:docs/testing.md |
grep -n session_folder` (→ 1 row) vs `git show origin/main:docs/testing.md | grep -c session_folder`
(→ `0`). That row is a **test-coverage inventory entry for the new feature**, not a statement about
the sunset, so the measurement paragraph's conclusion — no other doc contradicts this note — stands;
`docs/architecture.html` remains clean on both lines (`git grep -licE 'session.?folder' main --
docs/architecture.html` → no output).

**Scope of this update.** Landing status, migration numbering, feed/event emission, the settled-gate
evidence, and the `docs/testing.md` record. No decision, mechanism, candidate score, invariant,
trigger requirement, or numbering guardrail above is altered; the corrections were applied inside
this file only — `docs/testing.md` and `docs/architecture.html` are reported here, not edited.
