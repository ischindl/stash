---
"stash": patch
---

`origin/main` (`419efb4599fdafb648c036a5f31aeee220d363a7`) is inside our ancestry on the STAS-439
lane, and it is **not** inside `refs/heads/main` yet. As measured fetch-free on the lane tip,
against the recorded absorbed SHA rather than a re-fetched ref:
`git merge-base --is-ancestor 419efb459 refs/heads/fusion/stas-439` exits **0** and
`git rev-list --left-right --count 419efb459...refs/heads/fusion/stas-439` prints **0** upstream
commits left over (548 of ours on top). The same predicate on `refs/heads/main` is FALSE: the trunk
sits at `8674d5500` with merge base `41b217aa0` and `ahead/behind=544/2`. The debt is cleared on
the lane and still open on the trunk; the trunk write is an operator leg, named under *Landing*.

**Shape: one two-parent merge plus five normal commits on top of it.** No rebase, no squash, no
cherry-pick series. Four of the five are named in the *Post-merge repairs* paragraph below and the
fifth is this record.

| merge | parents | committed tree vs a naive `merge-tree` of the same two parents | hand-resolved |
|---|---|---|---|
| `82b3b7770` | `8674d5500` (trunk) + `419efb459` (upstream) | `e905adf2d` vs `05e5a51b0` — deviates in **13** files | 12 conflicts + 1 forced deletion |

The thirteenth deviating file is `frontend/src/components/workspace/reward-rail.test.tsx`: it is
ours-only, so git never flagged it, and it is deleted because its subject — `reward-rail.tsx`, a
modify/delete conflict — is retired upstream. Keeping it would have kept a test that cannot import.

**What users get.** Over `refs/heads/main` the lane moves **647 files (+66 / −93,585)**, and the
removed mass is upstream's own `1eca3516c` "Remove the evals product from the open-source repo
(#1295)": traces and their annotation UI, intuitions, reward functions, prompt optimization, the
Evals UI, and the training workers behind them. The MCP surface the CLI ships shrinks from 102
tools to **69**, because 33 of them (`stash_intuition_*`, `stash_optimization_*`,
`stash_reward_function_*`, `stash_trace`, `stash_traces`, `stash_import_traces`,
`stash_annotate_trace`, `stash_train_reward_model`) served that product.

**What survived deletion: the whole migration set.** Upstream retired **zero** migration files,
which is the fact that makes this absorption non-destructive, and it is a measured one: the merged
tree carries 267 files in `backend/migrations/versions/` — the trunk's own count — of which all
**250** upstream ids are present with **zero** slug mismatches on the shared ids, numerically sorted
before the join, stderr untouched. Our local tail `0257`..`0273` (17 ids) stays strictly above
upstream's ceiling `0256` and unbooked. `python backend/migrations/check_heads.py` closes
`migration graph ok: single head 0273`, `upstream_gate bookings` reports `250 ids on
refs/remotes/origin/main, highest 0256` both before and after `--live`, so
`backend/migrations/shipped_revisions.json` was **not rewritten**, and the whole chain re-applies
base-to-head on a database created empty (`python -m alembic upgrade head`, exit 0).
Two upstream-owned survivors are named because a blanket "delete what upstream deleted" would have
deleted them wrongly: `backend/models.py` keeps `reward_models_enabled` and `product_checkpoint`
(upstream keeps those columns itself, and migration `0210`/`0222` are still on the chain, so the
DB columns exist), and `backend/product_checkpoints.py` is byte-identical to upstream's blob,
`has_workbench()` dead in both trees. Root `CLAUDE.md` and its tracked `AGENTS.md` symlink survive
untouched. So do all eight agent trees under `stashai/plugin/assets/`.

**The twelve conflicts, with the side named per path.** The full ledger lives in the STAS-439 task
document `merge-conflict-ledger` (and its CSV artifact); in short: `.env.example` keeps our
`ALERT_SLACK` block and takes upstream's deletion of the evals blocks; `backend/main.py` imports and
includes only routers whose modules exist and its lifespan runs `share_mcp.session_runtime()` alone
(`intuition_mcp` is gone); `backend/tests/test_security_headers.py` keeps our stderr-sink mechanism
and loses the patched-out module's scaffolding; `cli/main.py` keeps our import refactor and drops
the import of the deleted `test_cmd`; the four `on_session_start.py` copies keep our no-fallback
shape and take upstream's removal of `released_instruction_context`, with the two shipped asset
copies left byte-identical to their plugin twins; and three paths take upstream's deletion outright
(`test_reward_models_flag.py`, `product-checkpoint.test.tsx`, `reward-rail.tsx`) because their
subject is the retired product — `rail.tsx` carries the byte-same Files/folders matcher the reward
rail held, so the `/folders/` navigation itself does not depend on them.

**The one thing this merge could not preserve, reported rather than averaged.** Our fork pinned
"nobody reintroduces the `/developer` lock for new signups", and the pin rode on
`reward_models_enabled`: the cohort upstream's #1128 created carried both flags, and the reward flag
was what exempted it. With the flag retired, the merged `workspace-shell.tsx` measures the opposite
of the guard — `redirectToPlatform` is true for a platform-only account on any route that is not a
platform resource, and the file browser is not one — and the same test file's own first group already
asserts that redirect. There is no fork-side carve-out left to test, so
`frontend/src/components/developer/platform-access.test.tsx` takes upstream's blob byte-for-byte.
Whether the fork wants a replacement gate for that intent is a product decision this record refuses
to make silently. What does survive is measurable: `/folders/{id}` and the other platform resources
still render in developer chrome for that account.

**Post-merge repairs this card owns** (a reference left dangling by the retirement, never a
workaround): `backend/tests/test_startup_logging.py` was the fourth `intuition_mcp` site, invisible
to the intake census because our trunk added that scaffolding and upstream never touched the file,
so the merge took our blob whole; `backend/tests/conftest.py` loses the `rm_title_generator` fixture
that imported `backend.services.rm`; `backend/celery_app.py`'s visibility-timeout comment named the
15 evals decorators and their 3900 s ceiling — none survive, so it states that the registry maximum
is the global 1800 s, and the pin test still reads the maximum from the registry; two frontend test
fixtures kept declaring `reward_models_enabled` on a `User` whose type no longer has it;
`cli/tests/test_mcp_registry.py`'s pinned surface and `docs/stash-mcp-integration.md` drop the 33
retired tools (both files are fork-side, so no upstream blob was at risk and no assertion was
weakened); `uv.lock` is re-resolved with `uv lock` for pyproject `0.1.491`, never hand-edited; and
`docker-compose.founder.yml` and `docker-compose.dogfood.yml` lose their `reward-worker` block,
because upstream's removal deleted that service from the self-host base while both fork-side
overlays went on declaring it, and compose CREATES a service named by an override alone, with
neither an image nor a build context, so `docker compose up` would reject the founder stack whole
rather than warn. Three guards measured it and a fourth said the founder pin no longer named
exactly the base's backend lanes; the queue that lane consumed is gone from `celery_app.py` too,
so there is nothing left for it to read. The three documents that recited the four-lane set now
recite three.

**Known reds this card does not own, reported not repaired.** (a) `npm ci` still fails in both web
projects with `Missing: sharp@ from lock file` — the pair is upstream's, the override
`"sharp": "<0.0.0-0"` and the sharp-free lock come from upstream's own releases, and STAS-427 already
records the disposition; it is the reason three frontend CI jobs (`frontend-lint`, `frontend-test`,
`frontend-build`) cannot reach a verdict on GitHub. (b) `frontend` ESLint is red — 11 errors and 10
warnings across 7 files — and every one of those 7 files is **byte-identical to `refs/heads/main`**,
so the verdict is trunk's, not this merge's; it is now visible because the unlocked install sidesteps
(a). Re-measured with the lockfile-pinned `eslint-plugin-react-hooks@7.0.1` the verdict is identical
(11 errors, 10 warnings), so it is not an artifact of the sidestep either. (c) `www`'s vitest suite is
red in the same three files STAS-427 recorded (3 files, 28 tests passing), and their subject page
`www/app/docs/cli/page.tsx` is absent at trunk too. Both web suites were still run for a verdict
under `npm install --no-save --package-lock=false`, which leaves manifest and lock byte-identical
(`git status --porcelain` empty for all four files afterwards) and therefore resolves a graph CI does
not.

**The green set, as measured on this box.** `ruff check backend/ cli/` and
`ruff format --check backend/ cli/` at the pinned `ruff==0.15.6`; `cli/tests` **712 passed**;
`plugins/tests` **429 passed**; `pytest backend/tests --collect-only` **2192 tests collected, zero
errors**; the whole backend suite against `pgvector/pgvector:pg16` on a probed port, which came home
**2188 passed, 4 failed** in 33 min, and those four are the founder and dogfood overlay guards named
above, not a fourth unknown: fixed in the same card and green on re-run, 13 passed across the three
compose and release-contract files; `frontend` vitest
**468 passed (78 files)**, `next build` with its type-check pass, and `www` `next build`. Two
lessons are worth keeping: an earlier draft of this record's green set was polluted by its own
`compileall` run, whose `__pycache__` directory inside `stashai/plugin/assets/` reddened two
`plugins/tests` guards that enumerate that directory — the artifacts were build residue, removed,
and both guards are green at 429; and the same compileall taught nothing to the record that the
measurements above do not already say.

**Landing — the open leg.** `refs/heads/main` is held by a checkout
(`git worktree list --porcelain` prints `/home/schindler/git/stash` on `branch refs/heads/main`), so
`AGENTS.md` decides the case: the ref must not be advanced from a linked worktree while a worktree
holds it. The handover is the deliverable, and it is a pure formality because forwardness holds —
`git merge-base --is-ancestor refs/heads/main refs/heads/fusion/stas-439` exits **0**. The operator
command, with its expected-old value, is:

```
git update-ref refs/heads/main "$(git rev-parse refs/heads/fusion/stas-439)" 8674d55008a8b786abf5d037ea24949935647384
```

The expected-new value is read from the lane ref rather than transcribed, because this record is
committed into the lane it names; the lane tip's own SHA is posted in the STAS-439 task log at the
final commit, re-read from the ref at that moment.

Re-read `git rev-parse refs/heads/main` before and after; those two SHAs are the proof. It is
refused if `refs/heads/main` is no longer `8674d5500`, which is the CAS doing its job.

**Enforcement, stated precisely.** Unlike the STAS-427 record, the gate on this machine **is**
active: `.git/hooks/reference-transaction` is a symlink to `.githooks/reference-transaction.sh`
(installed 2026-11-13T15:21), and `git config --get core.hooksPath` exits 1. That is why
`python -m backend.scripts.upstream_gate absorb` refused this absorb on the predicate as expected and
printed the 12 conflicting paths, why the K3 frozen lanes are unfrozen only once the trunk carries
`origin/main`, and why the trunk write above must satisfy the predicate — which it does, since the
lane tip contains `419efb459`.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased bullet is
the authoritative release note. This file is the removal and survival record the absorption owes.
STAS-438 is the lane that was frozen by the gate this absorb clears; once the trunk write above runs,
its base carries `origin/main` and it can land.
