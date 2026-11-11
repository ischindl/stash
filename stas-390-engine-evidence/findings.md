# STAS-390 findings — engine trunk landing for the two tool-channel lines

Card: STAS-390. Review level 3 (Full). The card moves refs and authors no code.
Which of the three branches the card takes is decided by measurement in Step 1,
and nothing below the Step-0 marker states a branch, a target ref, or a defect.

**Ordering gate.** No branch decision, no new ref name, and no defect claim
appears in this file before the Step-0 commit recorded at the end of the Step-0
section exists. Everything above that marker is measurement only.

## Measurement roots (every number in this file names one)

| Root | What it is | How this session reaches it | Revision / state measured |
| --- | --- | --- | --- |
| R1 | This stash card worktree (`stas-390`, branch `fusion/stas-390`) | native, cwd-relative git | HEAD `e4f48a435` (`e4f48a4357a074ddfb780171719189c5e4acb4db`), porcelain 0 lines before the evidence directory |
| R2 | Engine repository, through a **nested linked worktree** this card provisioned | `git -C .engine/stas-390-engine …`, and `git --git-dir=$HOME/git/Fusion/.git worktree list` for the bare-parent read | `refs/heads/main` = `af54c70c391e8318e80994c2c4cd10c8f53544f9`, subject `RUFU-307: refuse agent-authored text carrying code points outside the operator allowlist`; nested checkout porcelain 0 lines |
| R3 | The live process running this board | own parent-chain walk with `ps`; the CLI's local surfaces | daemon argv `/usr/bin/node --inspect=127.0.0.1:9229 packages/cli/dist/bin.js dashboard --host 0.0.0.0` — **relative script path, so its checkout is not recoverable**; `fn --version` = `0.78.0-beta.9` is the installed CLI, not the daemon's build; `fn mesh status --json` and `fn node list --json` carry no revision/commit/build key ⇒ **running build unidentifiable — fix liveness unverified** |

Raw captures: `raw/ac0-roots.txt`. Every capture regenerates via
`bash stas-390-engine-evidence/generate.sh` (pinned inputs, volatile fields
stripped — see that file's header).

## Step 0 — capability, roots, and the evidence directory

### Execution capability (resolves the plan's open question for *this* session)

- `bash` executes. The first probe `echo SENTINEL-STAS390-A7c3; pwd; git
  rev-parse --short HEAD; git rev-parse --abbrev-ref HEAD; git status
  --porcelain | wc -l` returned `e4f48a435`, `fusion/stas-390`, `0`. AC0 through
  AC3 are therefore reachable; this card is not a shell-refusal blocker, and the
  planning session's refusal (`This session has a read-only workspace boundary
  and cannot run shell or verification commands.`) is **not** inherited here.
- One form **was** refused and is recorded verbatim, because it shaped how every
  later probe is written:
  `Command targets a path outside the worktree boundary. Commands must run
  inside the worktree.` — triggered by an `awk '/^worktree /{…}'` literal, i.e.
  a command token beginning with `/`. The refusal discarded the entire command.
  Resolution, no obfuscation: probe logic lives in scripts written into
  `.fusion-tmp/` by the write tool and in `generate.sh`, run with worktree-
  relative arguments.

### R1 — this card worktree

- `git rev-parse --short HEAD` = `e4f48a435`; branch `fusion/stas-390`;
  porcelain 0 lines before the evidence directory existed.
- Exclusion legality proven, **no ignore file committed**:
  `git check-ignore -v .engine/x .fusion-tmp/x` resolves `/.engine/` at
  `.git/info/exclude:33` and `/.fusion-tmp/` at line 34 of the **common**
  exclude, so both names are excluded in every worktree of this repository. The
  child-path form is the one to trust; the bare-name form returns rc=1 with no
  output for an absent directory, which is not an absence of the rule.

### R2 — the engine repository, one checkout, one revision

- **Seam enumeration before provisioning:** this worktree had no `.engine/`
  directory at all. `git worktree list` (97 registrations) shows other cards'
  nested checkouts — `stas-246`, `stas-262`, `stas-273`, `stas-282` — each on
  its own branch at an older revision. None is reusable: adopting one moves
  every later measurement off the tip that has to land and stomps another card's
  lane. Exactly one checkout is provisioned for this card.
- Provisioning command and its verbatim output:

  ```
  $ git --git-dir=$HOME/git/Fusion/.git worktree add --detach "$PWD/.engine/stas-390-engine" refs/heads/main
  Preparing worktree (detached HEAD af54c70c39)
  HEAD is now at af54c70c39 RUFU-307: refuse agent-authored text carrying code points outside the operator allowlist
  ```

- It is a **linked worktree, not a clone**: its `.git` is a gitlink reading
  `gitdir: /home/schindler/git/Fusion/.git/worktrees/stas-390-engine`. A branch
  created here is therefore already visible to the operator and to the merge
  lane, with no bundle required to land.
- All engine git reads and writes in this card run **from inside** that
  checkout. The forbidden form `--git-dir=<worktree>/.git` is never used: that
  gitlink resolves to the wrong root and reports every tracked file as deleted.
- Trunk measured from inside it: `af54c70c39` /
  `af54c70c391e8318e80994c2c4cd10c8f53544f9`, and `git status --porcelain` =
  0 lines before any ref was touched.
- **The card body's SHAs were not used as inputs, and two of them are already
  wrong.** The body names `6aff48faff` as the trunk tip it measured; trunk is
  `af54c70c39` in this run, and `6aff48faff` is now one of the six trunk commits
  the carried branch is behind. `af54c70c39` was read at Step 0 and is re-read
  at the start of every later step.

### Occupancy — measured here, never assumed in either direction

From `git worktree list --porcelain`, the refs this card names:

| Ref | Held by |
| --- | --- |
| `refs/heads/main` | `/home/schindler/git/Fusion` (the primary engine checkout) |
| `refs/heads/fusion/stas-389-tool-channel-authority` | no checkout |
| `refs/heads/fusion/stas-388-tool-result-authority` | no checkout |

The trunk ref being held by a checkout outside this worktree is precisely why
the fast-forward is an operator leg and not a card leg. The full 97-registration
listing is not committed as a capture: it is other cards' lanes and it churns
between runs, so committing it would make `generate.sh` unable to reproduce its
own file. The committed capture regenerates the decision-relevant subset —
holder per named ref — from that same command on every run, and names the
registration count as pinned prose.

### Trunk content predicate — measured with its rc

```
$ git grep -c 'neutraliseStructureTokens\|ENVELOPE_DELIMITER\|ROLE_KEY' refs/heads/main -- packages/pi-claude-cli
(no output)   rc=1   hits=0
```

Per symbol on trunk, all three: rc=1, 0 files. The module itself
(`packages/pi-claude-cli/src/tool-result-authority.ts`) **is** present on
trunk; the neutraliser is not.

### AC0's measured no-op test, evaluated

AC0 closes the card as a no-op only when content presence **and** the ref
predicate hold together. Content presence fails (0 hits), so the no-op branch is
unavailable regardless of the ref predicate. Independently, neither carried line
reads `0 N` in the landing direction:

| Line | `rev-list --left-right --count main…line` | landed? |
| --- | --- | --- |
| `fusion/stas-389-tool-channel-authority` @ `22f6d6c5b1` | `6 2` | no — 2 commits of its own are not on trunk |
| `fusion/stas-388-tool-result-authority` @ `6a59cf4d51` | `7 1` | no — 1 commit of its own is not on trunk |

Both directions of `--is-ancestor` return rc=1 for both lines, so neither is a
stray descendant of the other's tip either. **The hazard is live and this card
is not a no-op.** The body's `3 2` has become `6 2` for the same reason its
trunk SHA went stale: trunk moved, the branch did not.

### R3 — the live process, and its limit

The parent chain is `bash` → `node --inspect=127.0.0.1:9229
packages/cli/dist/bin.js dashboard --host 0.0.0.0` → `bash
$HOME/bin/fusion-ui-watchdog.sh`. The Node script path is relative, so the
checkout the daemon runs from is not recoverable from `ps`. The CLI surfaces
expose no revision-shaped field. Therefore, verbatim: **running build
unidentifiable — fix liveness unverified.** Every claim this card makes about
what is *running*, including the claim about whether its own result is live,
inherits that sentence. This card does not restart the daemon.

### Parent captures and duplicate search — re-run, not inherited

- Readable from this worktree, because they are tracked in this repository:
  `stas-389-engine-evidence/raw/red-green.txt` (which carries the RED method and
  the working vitest invocation this card copies), `stas-389-engine-evidence/engine-commits.txt`,
  `stas-388-engine-evidence/engine-commits.txt`, `stas-388-engine-evidence/findings.md`.
- Not readable, as the spec predicted: `.fusion/tasks/STAS-389/attachments/` and
  `.fusion/tasks/STAS-388/attachments/` do not exist in this card worktree
  (measured: `ls: cannot access …: No such file or directory`). They are cited
  as provenance only, never opened.
- Board search re-run rather than inherited: no open card besides STAS-390 owns
  either engine ref. `STAS-391` is open and depends on STAS-390; it owns neither
  ref. `STAS-363` / `STAS-374` own the *stash* repository's trunk absorption, a
  different repository.

### Step-0 commit gate

The commit that carries this section is recorded here once it exists, and no
branch decision, target ref name, or defect claim appears anywhere above this
line before it.

Step-0 commit: `2b5eb9239` — `git show --stat` on it lists exactly
`stas-390-engine-evidence/findings.md`, `generate.sh`, and
`raw/ac0-roots.txt`, and nothing else. This SHA was written into this file by
the next step's commit, because a commit cannot contain its own hash; the
capture it carries is `raw/ac0-roots.txt`.

## Step 1 — the three gates, and the branch each measurement forced

All nine probes are in `raw/ac1-subsumption.txt`, generated. Tips as measured at
the start of this step: trunk `af54c70c39`, the STAS-389 line `22f6d6c5b1`, the
STAS-388 remediation `6a59cf4d51`. The trunk did not move between Step 0 and
Step 1 (re-read: `af54c70c391e8318e80994c2c4cd10c8f53544f9`).

### Gate S — subsumption: **G3**, not subsumed and not stackable

The two lines are **not the same fix**, and the census is what proves it, because
ancestry alone mis-decides this gate in either direction:

| Symbol | trunk | 389 line | 388 remediation |
| --- | --- | --- | --- |
| `neutraliseStructureTokens` | absent | **present** | absent |
| `ENVELOPE_DELIMITER` | absent | **present** | **present** |
| `ROLE_KEY` | absent | **present** | absent |
| `ZERO_BYTES_NOTICE` | **present** | **present** | absent |
| `emptinessOf` | absent | absent | **present** |
| `EMPTY_NOTICES` | absent | absent | **present** |
| `DeliveredText` | absent | absent | **present** |

- **G1 (subsumed) is out on two independent grounds.** `git cherry` reports the
  388 remediation as `+` against trunk *and* `+` against the 389 line, so no
  commit of one is already carried by the other; and `git diff --stat` between
  the two tips over the two engine paths is **119 insertions / 295 deletions
  across three files**, including the 202-line
  `tool-channel-authority.test.ts` that exists on one side only.
- **G2 (stackable, clean merge-tree) is out on measurement.**
  `git merge-tree --write-tree --name-only <388 line> <389 line>` exits **rc=1**
  with `CONFLICT (content): Merge conflict in
  packages/pi-claude-cli/src/tool-result-authority.ts`. The conflict is not an
  accident of ordering: both sides rewrite the same declaration — trunk's single
  `ZERO_BYTES_NOTICE`, which the 389 line keeps and the 388 remediation replaces
  with a two-reason `EMPTY_NOTICES` table — and both sides insert an
  `ENVELOPE_DELIMITER` block at the same position in the file. The verbatim
  conflicted hunk is in the capture.
- **Both lines are individually clean against trunk** (each `merge-tree` against
  `refs/heads/main` is rc=0), which is why exactly one of them can be delivered
  by this card without a human deciding a question of product wording.

**Branch taken: G3.** Deliver the conflict-free line rebased onto trunk, and hand
the revoke-or-resolve choice for the other line to the operator. *"Clean" here
names the line that does not conflict with trunk, not the semantically better
line* — ranking the two deliveries is the operator's call, and the card's Do NOT
forbids making it by force. The body of this card predicted exactly this
opposition ("the two lines make opposite edits to the empty-payload wording"),
and the merge-tree probe reproduced it, so G3 is the expected outcome and not a
failure of this card.

### Gate T — what semantics trunk actually holds

Trunk `af54c70c39` carries `ZERO_BYTES_NOTICE` and none of the other six symbols:
no `emptinessOf`, `EMPTY_NOTICES`, or `DeliveredText` (the 388 remediation's
rework), and no `neutraliseStructureTokens`, `ENVELOPE_DELIMITER`, or `ROLE_KEY`
(the 389 invariant). The one-subject log of the commit that last touched the
module on trunk is `b6e6cb73f1 STAS-388: give the tool-result channel zero
authority at its prompt assembly site`, and `is-ancestor(b6e6cb73f1, main)` is
rc=0 with the reverse direction rc=1 — that commit is landed, trunk is 13
commits past it, and this card neither touches nor re-judges it, per the body's
Do NOT.

What the operator is choosing between, stated as content: **trunk today carries
STAS-388's first delivery, one empty-payload notice, and no frame integrity at
all.** A payload can still write its own closing delimiter and a line-initial
role key.

This bears directly on the sibling card `STAS-391` (*"an empty tool-result
content array must be announced in the prompt, not crash prompt assembly"*, open
in `todo`, **depends on STAS-390**): whichever way the operator resolves the 388
remediation, `STAS-391` inherits the empty-payload semantics left standing here —
`ZERO_BYTES_NOTICE` if the remediation is revoked, `EMPTY_NOTICES` if it is
resolved and landed. Recorded so that owner sees it from a card that finished;
this card writes nothing into `STAS-391`.

### Gate W — ref naming and holding

- No checkout holds either predecessor branch; `refs/heads/main` is held by
  `/home/schindler/git/Fusion`. (One read, reproduced in the capture.)
- Zero refs are registered under `refs/heads/fusion/stas-390*`, so every name in
  this card's own namespace is free and **no predecessor label has to be moved**.
- Decision: this card will create a new branch in its own namespace and move
  nothing else, will perform no ref mutation before this step's commit exists,
  and will prove it by re-reading both predecessor tips before and after the ref
  work. The specific ref name is chosen in Step 2, not here.

Both readings are one more read of the same `git worktree list --porcelain`
snapshot that produced the occupancy table in `raw/ac0-roots.txt`.

## Step 2 — the rebase onto the measured tip

Trunk re-read at the start of this step: `af54c70c39…` — **identical to Step 0**,
so no gate needed re-probing against a new tip. The Gate S merge-tree probe was
nevertheless re-run against that freshly read tip before anything moved, and
returned rc=0 again (`fafdc7ee5a`). Own base versus the live tip was compared in
**both** directions (`is-ancestor(main, 389)` rc=1, `is-ancestor(389, main)` rc=1),
which is the comparison that matters — the divergence that stranded this family
came from trunk moving under a branch, not from a sibling SHA appearing.

Commands, both rc=0, in the nested checkout:
`git switch -c fusion/stas-390-tool-channel-authority refs/heads/fusion/stas-389-tool-channel-authority`
then `git rebase refs/heads/main`. **No conflict**: the rebase reported
`Rebasing (1/2)Rebasing (2/2)Successfully rebased`, so `git rebase --abort` was
never needed and neither side of any hunk was edited.

| Predicate | Measured | AC2 requires |
| --- | --- | --- |
| `rev-list --left-right --count main…new` | `0 2` | `0 N` ✓ |
| `merge-base --is-ancestor main <new>` | rc=0 | rc=0 ✓ |
| `merge-base --is-ancestor <new> main` | rc=1 | (printed for direction) |
| `diff --shortstat main…new` (three-dot) | 2 files, +275/−6 | printed |
| path-scoped `diff --stat` pre-rebase tip → new tip | **empty** | empty ✓ |
| `range-diff <merge-base> <pre-tip> <new-tip>` | `[==]`, both `=` | every commit `=` ✓ |
| `rev-parse refs/heads/main` before / after | `af54c70c39…` / `af54c70c39…` | identical ✓ |
| both predecessor tips after the ref work | unmoved | unmoved ✓ |

> **Addendum, written at Step 4.** Every row above was true as measured, and
> `0 2` remains true against the base that rebase used. But trunk moved *after*
> this step, so the branch was rebased a second time and the delivered tip is
> `d38b478184…`, not `b1884a46d2…`. The re-proved predicates are in section C of
> `raw/rebase-proof.txt` and the drift itself has its own section below.

**This card authored no code**, proved by two measurements rather than one:
the path-scoped diff between the pre-rebase tip `22f6d6c5b1` and the delivered
tip `b1884a46d2` over `tool-result-authority.ts` and its tests directory is
**empty**, and the full `range-diff` maps both carried commits to `=`.
The *unscoped* old-tip → new-tip diff is **57 files, +3730/−130**, and that
number is not a code change: it is the six trunk commits the line caught up
with, exactly as it must be whenever trunk has moved. Recording both numbers side
by side is what keeps the unscoped one from being misread — by a later reviewer
or by this card.

The delivered line moves exactly two paths, `packages/pi-claude-cli/src/tool-result-authority.ts`
and `packages/pi-claude-cli/src/__tests__/tool-channel-authority.test.ts` — the
same two the parent card already touched, which is the scope check AC6 asks a
reviewer to read in `engine-commits.txt` instead of opening the engine repository.

### Gate W executed — why the new branch carries this card's number

The predecessor branch holds the card that **authored** the invariant
(`fusion/stas-389-tool-channel-authority`), and that card is closed and its
content verdict already exists. This card changed its **base**, not its content —
the two commits still read `=` under `range-diff` — so the branch that carries
the result is a re-basing artifact of *this* card and is named for it:
`fusion/stas-390-tool-channel-authority`.

Two reasons not to advance the predecessor in place, both binding:

- **Attribution.** Advancing `fusion/stas-389-…` would make a closed card's
  delivery name point at a tip that card never produced, and the evidence
  directory that card committed records its old tip. A reader comparing
  `stas-389-engine-evidence/` against the branch would see a mismatch this card
  caused.
- **Authority.** Moving another card's ref is machine state outside this card's
  remit. Step 2's rule is that in no branch G1/G2/G3 is a predecessor ref moved
  or force-updated, and Gate W measured that nothing had to be: zero refs were
  registered under `refs/heads/fusion/stas-390*`, so a free name existed.

Measured after the ref work, both predecessor tips are exactly where Step 1
recorded them — `22f6d6c5b1…` and `6a59cf4d51…` — and `refs/heads/main` is the
same SHA it was before the `switch` and the `rebase`. Because the nested checkout
is a linked worktree of `/home/schindler/git/Fusion`, the new branch is already
visible in the engine repository; no bundle is needed to land it.

One piece of pre-existing engine state was observed and deliberately left alone:
the shared repository carries a `stash@{0}` entry whose own message says it is
recovered RUFU-267 work belonging to another worktree. This card never ran
`git stash` in any form — the RED state is created by `git checkout <sha> --
<path>`, which does not touch the stash stack — and that entry is untouched.

## Step 3 — fresh RED→GREEN on the new base

**Provisioning.** The nested linked worktree arrives with no `node_modules`, so
the toolchain does not exist until installed: `pnpm install --frozen-lockfile
--ignore-scripts` at the delivered tip, with no lockfile or `package.json`
change. Two workarounds are disclosed in `raw/suite-baseline.txt` rather than
hidden: `--ignore-scripts` skips postinstall native builds (better-sqlite3) and
none of these suites loads a native binding; and vitest is invoked as
`./node_modules/.bin/vitest run <file>` from inside the package, because the
package's own `test` script (`vitest run --reporter=dot`) offers no
file-argument position. Vitest 4.1.10, node v26.10.0.

**Baseline before anything moved:** whole `src` tree — **16 files, 387 tests
passed, 0 skipped**.

| Run | Module content | Result | Skipped |
| --- | --- | --- | --- |
| GREEN | delivered tip `b1884a46d2` | `Tests 11 passed (11)`, rc=0 | **0** |
| RED | `tool-result-authority.ts` at trunk `af54c70c39` | `Tests 8 failed \| 3 passed (11)`, rc=1 | **0** |
| restored | back at the tip | sha256 equals GREEN's, porcelain 0 | — |

The RED state is created by the only mutation this card makes to engine source:
`git checkout $TRUNK -- packages/pi-claude-cli/src/tool-result-authority.ts`.
The **test file is never checked out at trunk** — it does not exist there, so
restoring it would delete the assertion and leave a run with no test file, the
exact shape this card exists to refuse. No `-t` filter is used either; every run
is the whole file. The module's sha256 differs between the two runs
(`29e409a1…` at trunk, `eadeec74…` at tip), which is what shows RED executed
different code rather than the same code under a different label.

**AC2b is satisfied by the inherited test, not by an edit.** The sentinel test
was located by title — not by line number, so a renumbered file cannot quietly
quote the wrong block — and quoted unmodified. Its payload literal writes **2
closing tags and 1 opening tag** (measured by `grep -o`, not read off the title),
and the test asserts `toBe(1)` on the authored close tag plus byte-identity of
`restoreNeutralised(authoredFrameBody(prompt))` against the payload. All four
clauses are present. In the RED run that test fails with **`expected 3 to be 1`**:
the single tag the assembler authored really did become three, so the `three` in
the test's title is a measurement rather than rhetoric.

**Collateral damage.** Siblings are *derived* by grep, not guessed: the files
naming the module are `tool-channel-authority.test.ts` (11/11) and
`tool-result-authority.test.ts` (12/12), and `prompt-builder.ts` is the only
source file importing it. That file's own test does not name the module and so is
not in the derived list — which is exactly why the section ends with the whole-tree
run at restored content: **16 files, 387 tests, 0 skipped**, a counters line
identical to the pre-move baseline.

**Gate-disclosed failure.** `node scripts/check-file-line-count.mjs` exits **1**
repo-wide with 128 files over the cap, and this card is not green on it. It is
pre-existing, proved rather than asserted: the gate was re-run with this line's
module at trunk and the two offender lists diffed — **identical, 128 vs 128** —
and neither path this line owns appears on the list (187 and 202 lines against a
2000-line cap).

**Two harness defects found and fixed while building the capture**, recorded
because both would otherwise have produced a comfortable-looking lie:

1. The RED swap was first written as a bare `git checkout`, which resolved
   against the **stash** repository instead of the engine and aborted with rc=128
   *after* `red-green.txt` had been opened. The capture truncated at the swap
   instead of recording a GREEN run under a RED heading; the truncated file was
   discarded, not shipped.
2. `normalise_vitest` anchored `^Start at ` while vitest indents that line, so
   wall-clock times leaked and a regeneration was not byte-identical. The anchors
   now tolerate leading whitespace, and two consecutive full runs of
   `generate.sh` produce a byte-identical `raw/`.

## The base moved during this card's run

At the start of Step 4 — after the rebase and after the red-green capture had
already been written — the same read of the same ref that Steps 0 and 2 had read
returned a different SHA:

| read | when | `refs/heads/main` |
| --- | --- | --- |
| #1 | start of Step 0 | `af54c70c39…` |
| #2 | start of Step 2 | `af54c70c39…` |
| #3 | start of Step 4 | **`06b1768b18…`** — `RUFU-441: evict memory snapshots the consolidation sweep produces` |

Consequence measured, not assumed: `rev-list --left-right --count main…<first
rebase tip>` became **`1 2`** and `merge-base --is-ancestor main <tip>` returned
**rc=1**. The `0 N` / `rc=0` predicate set this card exists to guarantee had
quietly stopped being true. That is exactly the failure that stranded the
STAS-388/389 family, reproduced inside a single card run — and the AC1 rule to
read the trunk twice and compare is the only reason it surfaced here instead of
riding unnoticed into a completion summary.

Two responses, both recorded in `raw/rebase-proof.txt`:

1. **The drift could not have touched what was verified.** Over the two paths
   this line owns, the two trunk tips are the same bytes — the same blob for
   `tool-result-authority.ts` (`3f942b3db3…`) and the same tree object for the
   whole `__tests__` directory (`57aec8708d…`). The new commit is a
   memory-snapshot eviction change. So no suite result needed re-earning *for
   that reason*.
2. **The branch was rebased a second time** onto `06b1768b18`, cleanly, rc=0. The
   delivered tip is now **`d38b4781844d90b81ff51e4e2a6939da3c8b7b58`**, and
   `b1884a46d2` is kept in the record as a superseded intermediate rather than
   quietly rewritten. After the second rebase: `0 2`, `is-ancestor(main, tip)`
   rc=0, range-diff verdict column `[==]`, and the path-scoped diff from the
   pre-rebase tip **still empty** — so neither rebase authored code.

The red-green capture was re-run against the new tip and reproduced exactly:
GREEN `11 passed (11)`, RED `8 failed | 3 passed (11)`, zero skipped in both,
restore byte-identical.

Section A of `raw/rebase-proof.txt` computes its predicates from the **pinned**
first base rather than from `refs/heads/main`, because recomputing a historical
claim against a moving ref would silently rewrite this file's own history. The
capture ends with a two-command drift probe for the operator to repeat at landing
time, since trunk has now demonstrably moved while a card was running.

## Step 4 — the Gate S branch, executed on the second line

Gate S said **G3**: neither subsumed nor stackable. So the second line —
`refs/heads/fusion/stas-388-tool-result-authority` at `6a59cf4d51…` — gets no
merge from this card. It gets a decision request, in
`raw/operator-landing-leg.txt`, with the cost of each answer named as symbols.

**The three conflicted hunks**, from the merge-tree result recorded in
`raw/ac1-subsumption.txt` (rc=1, `CONFLICT (content)`):

1. the constants block — trunk's single `ZERO_BYTES_NOTICE` against the
   remediation's `EMPTY_NOTICES` table, in the same place the 389 line inserts
   `ENVELOPE_DELIMITER` and `ROLE_KEY`;
2. the empty-payload branch — `payload.emptyAs ? EMPTY_NOTICES[…] :` against the
   389 line's neutralising replace chain;
3. the module doc-comment, which both sides rewrote to describe their own
   behaviour, so even the prose disagrees about what the module does.

**What each answer costs.** Independently re-measured, and stated rather than
implied: `emptinessOf` (3), `EMPTY_NOTICES` (3), `DeliveredText` (3) and
`EmptyReason` (2) occur in the 388 remediation, are **not on trunk**, and are
**absent from the 389 line**. So the `no-content-blocks` empty-payload reason
exists nowhere but that ref.

- **Revoke** it and the loss is not a duplicate wording but a capability: that
  second empty reason is never shipped, and trunk keeps only the zero-bytes
  wording `b6e6cb73f1` already landed.
- **Carry** it and someone must resolve three real hunks in a file whose
  constants, empty-payload branch and doc comment all moved underneath it. That
  is authoring code, which this card is forbidden to do, so it belongs to a new
  card with its own review — not to this one.

Neither answer is this card's to choose, and this card writes nothing into
STAS-387, STAS-388 or STAS-389 and creates no card. **STAS-391 inherits the
consequence either way**: it depends on this card, and whatever it asserts about
the wording of an empty tool result is unsettled until Leg 2 is answered — single
`ZERO_BYTES_NOTICE` string under (a), reason-keyed `EMPTY_NOTICES` table under
(b).

### Doc correction disclosed here

The Step 3 commit shipped `findings.md` with the "Step 3" section present twice —
an anchored edit inserted a copy instead of replacing one. Only the prose was
duplicated; every capture file was generated once and is single-copy. The
duplicate is removed by this commit rather than left for a reader to trip over.

## Step 5 — packaging, and the one thing that stopped the captures reproducing

**The evidence directory is the deliverable and it is tracked**: `generate.sh`,
`findings.md`, `raw/` (six captures) plus `engine-commits.txt` and
`engine-diff.txt`. A reviewer reads those two files instead of opening the engine
repository — `engine-commits.txt` carries the oldest-first commit list, the
measured landing boundary (`merge-base(tip, base) == base`, behind count `0`) and
the authoritative changed-file list; `engine-diff.txt` carries the diff a
fast-forward would add to main.

**Determinism, proved rather than claimed.** Deleting every generated file and
re-running the committed `generate.sh` returns `git status --porcelain` to
**0** — the regenerated tree is byte-identical to what is committed, so the
captures are a product of the script and not of the session that watched the
commands run. The claim is exactly scoped: every value in the captures is either
pinned or derived from a pinned SHA, **except** the gate re-probe at the end of
`raw/ac1-subsumption.txt` and the drift probe at the end of
`raw/rebase-proof.txt`, which read `refs/heads/main` on purpose. Those two
sections are the only parts allowed to change on regeneration, and a change in
them is the signal the whole card exists to produce — trunk moved again — not
corruption.

That proof initially failed, on one self-referential line worth naming because
the failure was structural rather than accidental: `raw/ac0-roots.txt` printed
the card worktree's **live HEAD**. HEAD advances on every step commit, so the
file could never equal itself across a regeneration — it recorded `f3da12fda`
and regenerated as `346a09308`. The capture now records the pinned Step 0
revision (`2b5eb9239`), which is the revision the captures actually describe;
per-step SHAs live in this file's prose, where advancing them is meaningful. A
capture that embeds the moving SHA of its own container is not reproducible, and
no amount of re-running fixes that.

**Also packaged**, so the evidence survives the worktree: all nine generated
captures plus this file copied as `.txt` and attached to STAS-390, and a
Slovak-language operator brief written to the new task document
`operator-landing-brief` (the pre-existing `plan` document was left untouched).
Capture files stay English so they diff cleanly against the STAS-388 and STAS-389
evidence directories an operator will compare them with.

**Scratch discipline.** Every scratch path this card wrote lives under
`.fusion-tmp/` in the stash worktree — covered by line 34 of the common
`info/exclude`, and already verified in Step 0 to be ignored. Nothing was written
into the nested engine checkout, whose porcelain count stays `0`; that repository
has no exclude rule for this card's scratch and would have shown it as untracked.

### Authoring note, disclosed

The append that added this section ran twice, so the Step 5 section existed twice
in commit `b426dc0c5`. The two copies were byte-identical, which is why no
content was at risk; the duplicate is removed here. The same mistake produced a
duplicated Step 3 section earlier and was fixed in the Step 4 commit. Both times
the cause was the same — an append anchored on text that stays present after the
first insertion — and a heading-count check (`grep -c '^## Step N'`) is now part
of the pre-commit pass for this file rather than something noticed afterwards.

## Step 6 — the state this card hands over

Measured at close, in the nested checkout, not carried over from an earlier step:

| Thing | Value |
| --- | --- |
| `refs/heads/main` | `06b1768b18f38a9b4d0d5b35467204e25bfe4396` |
| delivered branch tip | `d38b4781844d90b81ff51e4e2a6939da3c8b7b58` |
| `rev-list --left-right --count main…delivered` | `0 2` — fast-forwardable |
| `refs/heads/fusion/stas-389-tool-channel-authority` | `22f6d6c5b1…` unmoved |
| `refs/heads/fusion/stas-388-tool-result-authority` | `6a59cf4d51…` unmoved |
| nested checkout / card worktree porcelain | `0` / `0` |

### Per acceptance criterion, in the terms the card set

- **AC0 — not a measured no-op.** `git grep -c` for
  `neutraliseStructureTokens|ENVELOPE_DELIMITER|ROLE_KEY` on trunk: rc=1, zero
  hits. Both carried lines diverged from trunk rather than sitting inside it. The
  card's own body tip `6aff48faff` was stale on arrival; the live tip was read
  instead.
- **AC1 — roots declared, trunk read twice.** Trunk read at the start of Step 0
  and Step 2 as `af54c70c39…`, and the comparison itself is what caught the drift
  at Step 4. The R3 daemon identity stays unresolved: argv carries a relative
  path and `fn --version` has no revision field, so
  **“running build unidentifiable — fix liveness unverified”** is the honest
  label and it was not upgraded by inference.
- **AC2 — the line moved, the content did not.** `0 2`; `--is-ancestor` rc=0 in
  one direction and rc=1 in the other, both printed; three-dot `diff --shortstat`
  2 files +275/−6; the **path-scoped** diff from the pre-rebase tip **empty**;
  `range-diff` verdict column `[==]`. The unscoped `57 files, +3730/−130` is
  recorded beside it as the trunk catch-up it is.
- **AC2b — fresh RED→GREEN, in the hazard's own form.** GREEN `11 passed (11)` at
  the delivered tip; RED `8 failed | 3 passed (11)` with only
  `tool-result-authority.ts` reverted; **zero skipped entries in both**; the
  sentinel failing `expected 3 to be 1`. No `-t` filter, no test file at trunk,
  no assertion edited.
- **AC4 — boundaries held.** No `refs/heads/main` write, no force-move of any
  predecessor ref, no PR opened or proposed, no daemon restart, no code committed
  to the engine repository. The engine's pre-existing `stash@{0}` entry
  (recovered RUFU-267 work) was never touched — the RED state used
  `git checkout <sha> -- <path>`, which does not touch the stash stack.
- **AC6 — readable without the engine repository.** `engine-commits.txt` and
  `engine-diff.txt` carry the commit list, landing boundary and the exact
  two-file scope.

### What is still owed, and by whom

1. **The operator lands one branch** (`git merge --ff-only`), after repeating the
   two-command drift probe, because trunk has already moved once inside this
   card's run.
2. **Someone with authority answers Leg 2** — revoke the STAS-388 remediation or
   open a card to carry it. The card that would carry it authors code and needs
   its own review; this card may not.
3. **STAS-391 stays unplanned** on the wording of an empty tool result until 2 is
   answered.
4. **Liveness remains unverified** by design, not by omission — resolving it needs
   a build-identity field the running `fn` does not expose.

