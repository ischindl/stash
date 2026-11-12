---
"stash": patch
---

`origin/main` is an ancestor of our trunk again. The absorb is one merge commit — this file's
own commit is it — with our trunk `1e3ddcfdd` as parent 1 and `refs/remotes/origin/main`
(`3691d14c4`) as parent 2, and `refs/heads/main` was advanced to it by compare-and-swap: not a
rebase, not a squash, and nothing was pushed anywhere. This is the first absorb since STAS-374
that moved no product line, and saying so plainly is the point of this file: its payload is
ancestry, not bytes.

**The measurement that frames every sentence below.** Upstream changed 816 files since the merge
base `94feb43cf` and our trunk changed 1530 — and `comm -23` over those two lists is **empty**:
there is not one file upstream touched that our trunk had not also touched since the same base.
After the 28 conflicts were resolved, `git diff HEAD^1 HEAD` names three files, all of them
written by this card and none of them touched by upstream's side. Every one of the 816 merged to
content our trunk already carried, so the 248 upstream commits brought history rather than code.
Against upstream our tree is still 776 files, +103260 / −2465.

**What users get:** nothing new to use, and one thing to rely on. Render builds `origin/main`;
our trunk now sits on top of it instead of beside it, so the branch we ship from can be compared
against what the product actually runs. The anti-fork predicate
`git merge-base --is-ancestor origin/main refs/heads/main` — the one STAS-373 wrote and which
refused every day until now — is TRUE, and the daily absorb job can reach its no-op branch.

**What upstream now owns:** no surface changed hands, because no content moved. Upstream's
curator, wiki, evals and reward-model surfaces arrived as ancestry only; the code behind them is
the same code STAS-374 recorded as theirs.

**What was removed — and which upstream commit replaced it:** nothing. No `modify/delete`
conflict appeared anywhere, so there was no retirement to follow. That includes the one family
durable memory expected to leave with the code it policed: `www/test/cli-docs-coverage.test.ts`,
`www/test/cli-docs-registry.test.ts`, `www/test/docs-home-commands.test.ts` and
`www/test/lib/cli-registry.ts` are absent at the merge base `94feb43cf` and absent at
`3691d14c4`, but present in our tree — relative to this base they are additive-ours, not ours-
and-retired-upstream, so this merge had no authority to delete them and deleted nothing. That
test surface still reads a CLI-docs page upstream retired; its disposition stays open and is
reported here rather than repaired.

**What survived deletion:** the rulebook. Root `CLAUDE.md` (`100644`, blob `c164f89ce`) and its
tracked `AGENTS.md` symlink (`120000`, blob `681311eb9`) are byte-identical to the trunk's, and
they were never at risk in this merge because upstream carries neither at the root and the two
paths did not conflict.

**What did not change:** everything else. Diffing a naive auto-merge of the same two parents
(`git merge-tree --write-tree HEAD^1 HEAD^2` = `9385a2aa8`) against the committed tree isolates
32 deviating files: the 28 conflicted paths the lane resolved, one per row in the STAS-427 task
document `merge-conflict-ledger`; two silent auto-merge artifacts that were a syntax error and a
redefinition, deduplicated rather than side-picked (`backend/models.py` carried a second
`from .product_checkpoints import ProductCheckpoint`, `backend/tasks/agent_schedules.py` a second
`else:` on one `try` block); and the three files this card wrote on top. That last set is the
only content this commit adds over the trunk it merged.

**Alembic needed no surgery, and the ceiling is a measurement, not a number from a card.** In
the merged tree upstream's 250 revision files reach highest id `0256`; STAS-371 had already moved
our local-only revisions above that and STAS-423 added `0271`, so our tail
(`0257_session_folders_share_wiki` .. `0271_curator_turn_ceiling`) sits strictly above the ceiling
and stays unbooked. The independent collision probe over both merge parents — slug diff of
`git ls-tree` basenames, numerically sorted before the join — prints **zero** lines, which is the
only green answer, because two colliding ids are two different paths and merge with no git
conflict at all; the file counts either side (265 ours, 250 upstream) say nothing about it.
`python -m backend.scripts.upstream_gate bookings` confirms the booking map rather than changing
it: `bookings current: 250 ids on refs/remotes/origin/main, highest 0256`.
`python backend/migrations/check_heads.py` closes with `migration graph ok: single head 0271`,
and the whole chain re-applies base-to-head on a database created empty, stamping `0271` over 144
base tables. The second alembic environment, `backend/managed/migrations/versions` (`m0001…`), is
not guarded by that script, so it got its own basename probe: zero collisions there too.

**One test-mechanics repair, and it is not this merge's.** `backend/tests/test_folder_curators.py`
patched the worker's ceilings with `monkeypatch.setattr(celery.conf, …)`. `celery.conf` is a
ChainMap, so its keys live in a mapping and never in the instance dict; pytest 9's `setattr`
teardown therefore records no old value and answers with `delattr` on a key that was never an
attribute, and the backend suite exited 1 on a run in which every test passed (2941 passed, 1
error). `setitem` is the same temporary override with a teardown that restores, and the assertion
is untouched. The test arrived with `1e3ddcfdd` and `backend/requirements-dev.txt` asks only for
`pytest>=8.0.0`, which now resolves to 9.2.0 — so this is a trunk condition that this card's green
set happened to be the first to walk end to end, not a merge consequence: the merged tree carries
our trunk's bytes for every file upstream touched.

**Enforcement, stated precisely.** This card makes the gate *activatable*; it does not make it
*active*. `.githooks/reference-transaction.sh` is still not installed on this machine —
`.git/hooks/` holds no `reference-transaction` and `git config core.hooksPath` still exits 1 —
and `backend/scripts/install_hooks.py` was not run, because hook installation is the operator's
leg (K3) and it writes outside every file path this card may touch.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased bullet
is the authoritative release note. This file is the removal record the absorption owes. The
per-file ledger behind every side-pick, with the class and the side named per path, lives in the
STAS-427 task document `merge-conflict-ledger`.
