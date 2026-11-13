---
"stash": patch
---

`origin/main` is inside our ancestry on the STAS-427 lane, and it is **not** inside
`refs/heads/main` yet. That distinction is the honest headline of this absorb, so it is
first and it is measured, not narrated. On the lane tip
`git merge-base --is-ancestor refs/remotes/origin/main refs/heads/fusion/stas-427` exits
**0** and `git rev-list --left-right --count refs/remotes/origin/main...refs/heads/fusion/stas-427`
prints **0** upstream commits left over; the same two probes on `refs/heads/main` exit **1**
and print **252** upstream commits left over
(`python -m backend.scripts.upstream_gate check refs/heads/main` →
`FALSE … ahead/behind=523/252`, merge base `94feb43cf`). The debt is cleared on the lane and
still open on the trunk. The trunk write is an operator leg, named under *Landing* below.
K1 makes the anti-fork gate activatable; nothing here makes it active or installed.

**Shape: four two-parent merges, not one, because both sides moved while the lane was under
review.** No rebase, no squash, no cherry-pick series — 252 upstream commits and our 529 are
all reachable as themselves.

| merge | parents | committed tree vs a naive auto-merge of the same two parents | hand-resolved |
|---|---|---|---|
| `955ac2952` | `1e3ddcfdd` (trunk then) + `3691d14c4` | `63fac8477` vs `fd9a40e45` — deviates in **32** files | 28 conflicts |
| `f4037c868` | `955ac2952` + `ca1b17e71` | — | re-merged the trunk, which had moved under the lane |
| `5869cd7e2` | `f4037c868` + `476e2ead1` | `943b9ea2d` vs `943b9ea2d` — **equal** | **0** |
| `c1fda3c7b` | `4936d76f3` + `41b217aa0` | `517c00ab2` vs `517c00ab2` — **equal** | **0** |

A merge whose committed tree equals `git merge-tree --write-tree` of its own parents had
nothing picked by hand, which is why the last two rows are evidence and not assertion. Two
normal commits sit on top of the merges: `4936d76f3` and `48c036e4d`, each a `uv lock`
re-resolve to upstream's freshly bumped release number.

**What users get:** the product-side difference is small and it is real, and it is not what
the card was for. Over the trunk the lane moves 27 files (+1702 / −1842), and the upstream
bytes in it are `fa7453845` "Remove sharp and libvips from Stash web builds (#1292)" and
`57a0156da` "Patch the dependencies behind Stash's 93 Dependabot alerts (#1294)" plus two
release bumps. The rest of the 252 commits arrived as ancestry already carried: against the
merge base, upstream touched no file our trunk had not also touched, so the curator, wiki,
eval and reward-model surfaces came through as code STAS-374 already recorded as upstream's.
What the anchoring is for is the trunk's standing: Render builds `origin/main`, and our trunk
can now sit on top of it instead of beside it.

**What upstream now owns:** no surface changed hands. No `modify/delete` conflict appeared in
any of the three absorbs, so class R — follow the retirement, delete the local edit — never
fired. That includes the family durable memory expected to leave with the code it policed:
`www/test/cli-docs-coverage.test.ts`, `www/test/cli-docs-registry.test.ts`,
`www/test/docs-home-commands.test.ts` and `www/test/lib/cli-registry.ts` are present in our
tree and absent at `41b217aa0`, but they were never in conflict here, so this merge had no
authority to delete them and deleted nothing. They still read a CLI-docs page upstream
retired; that disposition stays open and is reported rather than silently repaired.

**What survived deletion:** the rulebook. Root `CLAUDE.md` (`100644`, blob `c164f89ce`) and
its tracked `AGENTS.md` symlink (`120000`, blob `681311eb9`) are byte-identical to the
trunk's and still begin `Write extremely easy to consume code.` Upstream carries neither at
the root, so nothing put them at risk — they are named because a blanket "delete what
upstream deleted" would have deleted them.

**What the card itself changed, beyond ancestry:** three files. `.changeset/` this record,
`docs/upstream-absorb-procedure.md` (only the two sentences this absorb made false), and
`backend/tests/test_folder_curators.py`. The last is a test-mechanics repair and it is not
this merge's doing: the test patched the worker's ceilings with
`monkeypatch.setattr(celery.conf, …)`, but `celery.conf` is a ChainMap whose keys never live
in the instance dict, so pytest 9's `setattr` teardown answers a found value with `delattr`
on a key that was never an attribute and reddens a run in which every test passed. The test
arrived from our trunk (`1e3ddcfdd`, STAS-423) and `backend/requirements-dev.txt` asks only
for `pytest>=8.0.0`, which now resolves to 9.2.0. `setitem` is the same temporary override
with a teardown that restores; the assertion is untouched.

**Alembic needed no surgery, and the ceiling is a measurement of the merged tree.** Upstream's
250 revision files reach highest id `0256` and `bookings current: 250 ids on
refs/remotes/origin/main, highest 0256`; STAS-371 had already moved our local-only tail
`0257_session_folders_share_wiki` .. `0271_curator_turn_ceiling` (15 files) strictly above
that ceiling, and it stays unbooked. The collision probe — slug diff over `git ls-tree`
basenames, numerically sorted before the join, stderr untouched — prints **zero** lines
against the final absorb's own two parents, **zero** against the current upstream tip, and
**zero** for the second, unguarded environment in `backend/managed/migrations/versions`. Zero
is the only green answer here, because two colliding ids are two different paths that merge
with no git conflict at all: the file counts either side (265 ours, 250 upstream) say nothing
about it either way. `python backend/migrations/check_heads.py` closes
`migration graph ok: single head 0271`, and the whole chain re-applies base-to-head on a
database created empty.

**Known red this card does not own, reported not repaired:** `cd frontend && npm ci` fails
with `Missing: sharp@ from lock file`. `frontend/package.json` (`f5eec74da`) and
`frontend/package-lock.json` (`5cd68d8eb`) in the merged tree are byte-identical to
`refs/remotes/origin/main`, where the override `"sharp": "<0.0.0-0"` and the sharp-free lock
come from upstream's own `57a0156da` and `fa7453845`. The pair is upstream's, so syncing it
here would mean rewriting an upstream release artifact this card's own rules forbid touching.

**Landing — the open leg.** `refs/heads/main` was not advanced by this card.
`git worktree list --porcelain` shows `/home/schindler/git/stash` on
`branch refs/heads/main`, so the trunk ref is held by a checkout. `AGENTS.md` decides that
case outright: the ref must not be advanced from a linked worktree while a worktree holds it,
because `update-ref` cannot tell that checkout's `HEAD` anything and the stranded tree reads
as a huge staged revert — the STAS-244 incident verbatim. The handover is therefore the
deliverable, not a shortcut around it, and it is a pure formality because forwardness holds:
`git merge-base --is-ancestor refs/heads/main refs/heads/fusion/stas-427` exits **0**. The
exact command and its expiry are in the STAS-427 task log.

**Enforcement, stated precisely.** This card makes the gate *activatable*; it does not make it
*active*. `.githooks/reference-transaction.sh` is still not installed on this machine —
`.git/hooks/` holds no `reference-transaction` and `git config --get core.hooksPath` still
exits 1 — and `backend/scripts/install_hooks.py` was not run, because hook installation is the
operator's leg (K3) and writes outside every path this card may touch.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased
bullet is the authoritative release note. This file is the removal and survival record the
absorption owes. The per-file ledger behind every side-pick, with the class and the side named
per path, lives in the STAS-427 task documents `merge-conflict-ledger` and
`merge-conflict-ledger-correction`.
