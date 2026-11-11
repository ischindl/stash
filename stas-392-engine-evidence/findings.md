# STAS-392 — findings

Card: **STAS-392** — land the engine tool-result family in order: make the STAS-390 line
fast-forward-ready, announce the window, then land the STAS-391 guard deletion with all
three empty-array pins reconciled.

What this file is: the record a reviewer can judge **without opening the engine
repository**. Every number in it was printed by `generate.sh` in this run; each capture
names the tip it was measured on, and a capture is admissible only for that tip.

Regenerate everything: `bash stas-392-engine-evidence/generate.sh` (run from the root of
this card's worktree).

---

## Root table — where each fact was measured

| Root | Surface | Instrument | What it returned in this run |
|---|---|---|---|
| **R1** | This card's worktree `/home/schindler/git/stash/.fusion/worktrees/stas-392` | `git rev-parse --short HEAD`, `git branch --show-current`, `git status --porcelain \| wc -l`, `git check-ignore -v <child-path>` | HEAD `0900d17d7`, branch `fusion/stas-392`, porcelain **0 lines**. Engine seam `.engine/stas-392-engine/` excluded by `/home/schindler/git/stash/.git/info/exclude:33` pattern `/.engine/`; scratch `.fusion-tmp/` excluded by line 34 pattern `/.fusion-tmp/`. Both proven by probe, neither assumed, and **no ignore file is committed by this card**. |
| **R2** | The engine repository **through a nested linked worktree** at `.engine/stas-392-engine` | `git -C .engine/stas-392-engine …`; `--git-dir=$HOME/git/Fusion/.git` used only for the provisioning read | Provisioned at `refs/heads/main`, detached. `git rev-parse refs/heads/main` = `4fa1ea0e64bccc32e2d3116c534706d1b953931c`, porcelain **0 lines**. Linked, not cloned, so a branch created here is operator-visible immediately. |
| **R3** | The running build of this board | — | No revision is exposed on any agent-readable surface: **running build unidentifiable — fix liveness unverified**. |

Full output of every one of these commands: `raw/ac0-roots.txt`.

### Provisioning detail worth stating

`git worktree add --detach` was **refused** first (rc=128): this exact path was registered
by a previous session of this card and its directory had since been cleaned. The fix was
`add -f`, scoped to this path. A `git worktree prune` would have succeeded too and was
deliberately **not** run: it would also have deleted STAS-391's own (also stale)
registration entry, and this card does not move another card's artifacts.

### Refs in play — named by ref, never by memorised SHA

| Ref | Holder, read live from `git worktree list --porcelain` | Meaning for this card |
|---|---|---|
| `refs/heads/main` | `/home/schindler/git/Fusion` (the **primary** engine checkout) | Exactly where the operator's merges must run. This card never writes it. |
| `refs/heads/fusion/stas-390-tool-channel-authority` | **no live checkout** (absent from `worktree list`) | Free to rebase in place; no other lane holds it. |
| `refs/heads/fusion/stas-391-empty-tool-result-announcement` | registered to `…/worktrees/stas-391/.engine/stas-391-engine`, and that entry is marked `prunable gitdir file points to non-existent location` | This card's **discovered** 391 ref name; its predecessor ref is never moved. Occupancy is reported as printed, not interpreted. |
| `refs/heads/fusion/stas-392-empty-tool-result-announcement` | created by Step 4 of this card | The delivered 391 line. Newly named so the predecessor stays exactly where it was (AC6). |

**The 391 ref name was discovered, not assumed.** `git for-each-ref
--format='%(refname) %(objectname:short)' refs/heads/fusion/` filtered to `stas-39`
returned exactly two refs; the 391 one is
`refs/heads/fusion/stas-391-empty-tool-result-announcement`. The parent's spec named a
different branch than its delivery used, so assuming either name would have rebased the
wrong object.

---

## The ground, as measured in this run

| Measurement | Value |
|---|---|
| `refs/heads/main` at run start | `4fa1ea0e64bccc32e2d3116c534706d1b953931c` (short `4fa1ea0e64`) |
| Nested-checkout porcelain | 0 lines |
| Trunk commits since the family's shared merge-base `06b1768b18` | 2 — `c20b8e4eaa RUFU-480: retire the stale-binary schema refusal…`, `4fa1ea0e64 perf(RUFU-756): stop the overseer sweep…` |
| Files those two commits touched | `packages/core/**`, `packages/engine/**`, `docs/**`, `.changeset/**` — **nothing under `packages/pi-claude-cli`** |
| `rev-list --left-right --count main...390` | `2 2` → diverged, fast-forward **not** available at Step 0 |
| `rev-list --left-right --count main...391` | `2 1` |
| `merge-base` of main, 390 and 391 | all three pairwise merge-bases are `06b1768b18` — both lines sit on the same stale base |
| AC1 probe `git grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src` | **0 hits (rc=1)** — the invariant is **not** on the trunk |
| vitest binary at `packages/pi-claude-cli/node_modules/.bin/vitest` | present after `pnpm install --frozen-lockfile --ignore-scripts` (rc=0, 2.6 s, warm store). `--ignore-scripts` skips postinstall native builds; the vitest lane needs no native module. |

---

## Gate V — which emptiness vocabulary the ground holds

Per-symbol counts over `packages/pi-claude-cli` (full table in `raw/ac0-roots.txt`):

| Symbol | main | 390 | 391 |
|---|---|---|---|
| `neutraliseStructureTokens` | 0 | 2 | 0 |
| `ENVELOPE_DELIMITER` | 0 | 2 | 0 |
| `ROLE_KEY` | 0 | 8 | 0 |
| `ZERO_BYTES_NOTICE` | 2 | 2 | 4 |
| `EMPTY_NOTICES` | **0** | **0** | **0** |
| `emptinessOf` | **0** | **0** | **0** |
| `DeliveredText` | **0** | **0** | **0** |
| `no-content-blocks` | **0** | **0** | **0** |

**Verdict:** `EMPTY_NOTICES` and `no-content-blocks` are zero on trunk and on both carried
refs, so the STAS-388 remediation has **not** landed in the meantime. The reconciliation
target is therefore the landed branch: `delivered="zero-bytes"` plus `ZERO_BYTES_NOTICE`,
and both literals are read from the measured ground —

* `const ZERO_BYTES_NOTICE = "[the tool delivered an empty payload: zero bytes of output]";` (`tool-result-authority.ts:44` at `4fa1ea0e64`)
* `if (text.length === 0) attributes.push('delivered="zero-bytes"');` (`tool-result-authority.ts:112` at `4fa1ea0e64`)

— never typed from this card's body. Gate V exists so this card cannot silently pre-empt
the operator's open revoke-or-carry decision about STAS-388's remediation; nothing here
collapses two notices into one.

---

## Pin census, classified

Three probes (`no content to mark`, `no payload was delivered`, `promptWithToolResult([])`)
run against trunk and both carried refs; every hit with its file, its `it()` title and its
classification is in `raw/pin-census.txt`. Counts per class:

| Class | main | 390 | 391 |
|---|---|---|---|
| empty-array-must-go | 2 (pins a, b) | 3 (pins a, b, **c**) | 0 |
| retained | 4 (3 production throw messages + the `content: undefined` buildPrompt pin) | 5 (same four + the sibling file's byte-for-byte frame test carries no empty-array call) | 4 |
| already-reconciled | 0 | 0 | 2 (pins a and b, by STAS-391's own commit) |

The three pins, located **by title** rather than by line number:

* **(a)** `it("refuses to render a payload that was never delivered, …")` in
  `tool-result-authority.test.ts`. On trunk the loop reads
  `const dropped: unknown[] = [undefined, null, [], [{ type: "text" }], 42]` and asserts
  `/no content to mark/`. **On STAS-391's commit `[]` is already gone from that list**, the
  other four entries are intact, and a comment states why. → *already-reconciled by the
  parent; this card verifies, it does not re-author.*
* **(b)** same file, trunk's
  `expect(() => promptWithToolResult([])).toThrow(/no payload was delivered/)` — the
  fragment a `no content to mark` grep cannot see. **Already reconciled by the parent**: on
  its ref the probe `no payload was delivered` returns **rc=1 (zero hits)**, the `it()` is
  retitled to the announcement, and the assertions are `delivered="zero-bytes"`, the
  `ZERO_BYTES_NOTICE` line, and the equality with the empty string. → *already-reconciled.*
* **(c)** `tool-channel-authority.test.ts`,
  `it("keeps the empty payload saying so instead of going silent, because this card must
  not widen or weaken the fail-loud rule it builds on", …)` inside
  `describe("an ordinary payload is not rewritten (the anti-sledgehammer guard)")`. Its
  last line is `expect(() => promptWithToolResult([])).toThrow(/no content to mark/);`.
  **This file does not exist on trunk and does not exist on STAS-391's ref** — it arrives
  only with the 390 line, which is why the parent's commit could not reach pin (c) and why
  the parent's own green said nothing about it. → **this card's delta.**

Retained pin the same greps will find and this card must not touch:
`it("propagates a failure inside the marking step to the caller, …")`, which builds
`{ role: "toolResult", toolName: "bash", content: undefined }` and asserts
`expect(() => buildPrompt(context)).toThrow(/no content to mark/)`. It matches the literal,
says nothing about `[]`, and is AC3 ground — it must still throw after the guard deletion.

---

## Board observations (per-ID reads, not list badges)

| Card | Column | Steps | What its own log says |
|---|---|---|---|
| **STAS-390** | Done | 7/7 done | `Merge confirmed finalization blocked — required post-merge evidence gate 'post-merge-verification' is not approved`, then `Workflow graph observed confirmed merge while task was 'in-review' — finalizing to done (graph-failure)`, then `Post-merge evidence was not required … (git surface ref-absent, landing confirmed …)`. The engine ref did not move — the AC1 trunk census above measures that independently of this badge. |
| **STAS-391** | Done | 9/9 done, including Step 7 `Fix: Step-1 base witness measures the fixed tip, so the committed evidence contradicts the card's central premise` | Same shape: `Merge already confirmed; refreshing row and completing task`, then `Workflow graph observed confirmed merge … — finalizing to done (graph-failure)`. Its P0 was a base witness that imported the fixed module while its header claimed the base; **this card's procedural answer** is that every RED capture here checks the module out at the ground blob first, prints `git status --porcelain` as the restore proof, and names the SHA it was measured on. |

The read surface exposes column, step statuses, prompt and log tail. `workflowStepResults[0].verdict`
and `priorAttempts[]` are **not rendered by this tool**, so they are recorded as not readable from
this surface rather than as empty. The first two attempts at these reads returned
`fn extension TaskStore boot timed out after 30000ms` (connect=2ms applySchema=10289ms
schemaBackend=11550ms) followed by a 5000 ms cooldown refusal; the retry succeeded. That board
flakiness is a known host condition (STAS-384), not a finding of this card.

A `done` badge in this family has twice meant only that the evidence folder landed. Both log tails
above are the mechanical form of that sentence.

## Duplicate search for an open card owning either engine ref

All four open lanes enumerated with `fn_task_list`: **in-progress** = STAS-392 (this card) only;
**in-review** = empty; **planning** = STAS-377 (/tmp inodes), STAS-378 (recurrence of STAS-370);
**todo** = all 17 cards enumerated. None names `fusion/stas-390-tool-channel-authority` or
`fusion/stas-391-empty-tool-result-announcement`. The trunk-adjacent open cards (STAS-363,
STAS-372, STAS-373, STAS-374) own the **stash** repository's `main`, not the engine repository's,
so no open lane can move either ref this card is about to move. Corroborated independently:
`git worktree list --porcelain` shows no live checkout holding the 390 ref.

## Parent artifacts: which were readable from this card worktree

| Artifact | Read? | What it decided |
|---|---|---|
| `stas-390-engine-evidence/` (`findings.md`, `generate.sh`, `raw/ac0-roots.txt`, `raw/rebase-proof.txt`, `raw/red-green.txt`, `raw/operator-landing-leg.txt`) | **read** | The occupancy and census method, the nested-checkout command forms (including why `--git-dir=<worktree>/.git` is forbidden: its gitlink reports phantom mass deletions), and the landing-leg shape this card copies. |
| `stas-391-engine-evidence/engine-diff.txt` | **read in full** | Authoritative for which pins its commit already reconciled: pin (a)'s `[]` removed from `dropped`, pin (b) rewritten and retitled, the guard deleted, both doc comments rewritten. Pin (c) is absent from it, which is this card's delta. |
| `stas-391-engine-evidence/{findings.md,generate.sh}` | read (targeted greps) | The install and vitest invocation, the `--ignore-scripts` disclosure, the capture-header convention. |
| `.fusion/tasks/STAS-388/attachments/1793838540398-engine-diff.txt`, `.fusion/tasks/STAS-389/attachments/1793851386557-engine-diff.txt` | **not readable from a card worktree — recorded as unread** | `.fusion/` does not exist inside this worktree (a `python3` read returned `FileNotFoundError`), which is the condition this card's spec anticipated. Nothing is missing for that: the landed pins and the neutraliser hunks are reachable from the measured refs themselves via `git show <ref>:<path>`, which is a stronger source than a snapshot attachment. |
| Engine `CLAUDE.md` landing-hygiene rule | **does not exist at the measured ground** | `git grep -i "landing\|ff-only\|fast-forward" refs/heads/main -- CLAUDE.md` returns nothing, though the file exists. There is therefore no engine-side landing rule to cite and none is duplicated here. This repository's own rules were verified present in `CLAUDE.md`: `FAIL LOUD` ×1, `single source of truth` ×2, `PR creation is operator-gated` ×1. |

---

## Step 1 — the 390 line rebased onto the tip measured in-run

Trunk was re-read at Step 1 entry before anything moved: `4fa1ea0e64bccc32e2d3116c534706d1b953931c`,
nested-checkout porcelain 0 — identical to Step 0's reading, so the Step 0 ground still held.
`refs/heads/fusion/stas-390-tool-channel-authority` had **no live checkout**, so it was
checked out in this card's nested worktree and rebased; nothing was force-moved and nothing
was resolved by hand.

| | value |
|---|---|
| PRE (tip found on the ref) | `d38b4781844d90b81ff51e4e2a6939da3c8b7b58` |
| POST (delivered tip) | `e6b1fe4c7f4bafa36727e33e8df7c400e87c6a91` |
| rebased onto | `4fa1ea0e64bccc32e2d3116c534706d1b953931c` (the measured trunk) |
| `rev-list --left-right --count main...390` | `2 2` → **`0 2`** |
| `merge-base --is-ancestor main 390` | rc=1 → **rc=0** |
| `merge-tree --write-tree --name-only` | rc=0, **no CONFLICT line**, before and after |
| merge result tree printed by `merge-tree` | `c7c6a80f22…` **identical before and after** the rebase |
| scoped `diff --stat PRE POST -- tool-result-authority.ts __tests__/` | **empty** (rc=0) |
| `range-diff 06b1768b18 PRE POST` | both carried commits map to **`=`**; only trunk's own two commits appear as new |

Two facts a reviewer should not have to infer: the conflict predicate was clean **before** the
rebase too, which is why the rebase could not silently author anything; and the identical
merge-result tree is an independent statement of the same fact as the all-`=` range-diff.
The full block, in command form, is `raw/rebase-proof.txt`.

## Step 2 — fresh red-green of the channel invariant on the new base

Both runs name **both** test files and were executed with the package's own vitest binary from
inside `packages/pi-claude-cli`; no `-t` filter anywhere. Full transcripts with every test name:
`raw/red-green.txt`.

| Run | `Test Files` | `Tests` | rc |
|---|---|---|---|
| GREEN at the delivered 390 tip `e6b1fe4c7f` | 2 passed (2) | **23 passed (23)** | 0 |
| RED — only `tool-result-authority.ts` checked out at the freshly measured trunk `4fa1ea0e64` | 1 failed \| 1 passed (2) | **8 failed \| 15 passed (23)** | 1 |
| GREEN again after `git checkout HEAD -- <module>` | 2 passed (2) | **23 passed (23)** | 0 |

Zero skipped in every run (the totals equal the pass counts). The restore proof is
`git status --porcelain` printing **nothing** after the restore; `git stash` was used nowhere.

**Which tests went red, and why that is the right set.** All eight failures are exactly the
invariant's own tests: four in `a tool result cannot write the frame it is delivered in (AC2)`
and four in `a tool result cannot mint a role key (AC1)`. The quoted assertion lines are the
defect — `expected 2 to be 1`, `expected 3 to be 1` (one authored close tag became three in a
payload's own hands) and `expected [ 'USER:', 'ASSISTANT:', …(2) ] to deeply equal
[ 'USER:', 'ASSISTANT:', …(1) ]` (a payload minted a third role key). The fifteen survivors
include both anti-sledgehammer tests and every STAS-388 zero-authority test, which is what
"this card sits on the landed layer rather than re-judging it" looks like as an observation.

Only the production module was ever checked out at trunk. `tool-channel-authority.test.ts`
**does not exist at trunk** — it arrives with this line — so restoring the test files would have
deleted the assertion under test and left a run with no test file, the shape that reports
success without running anything.

**Suite baseline at the delivered tip**, by failing-name set rather than colour: `src` is
`16 files / 387 tests passed`, the failing-name set is **empty**, and zero entries were skipped.
The claim this supports is that **this diff added zero failures** — not that the suite is green.
The repo-wide line-count guardrail (`node scripts/check-file-line-count.mjs`) exits rc=1 with
**128 files over cap**: pre-existing debt, printed in full in `raw/suite-baseline.txt`, never
`--update`d by this card, and the offender count for `pi-claude-cli` is a measured **0**, so
neither the module nor either test file this line owns is on that list.

## Step 4 — the restacked announcement line and the three-pin reconciliation

The delivered ref is **newly named**: `refs/heads/fusion/stas-392-empty-tool-result-announcement`.
The predecessor's ref was left exactly where STAS-391 left it — both SHAs are printed side by side
in `raw/rebase-proof.txt`, and `eb20a6bfc5…` still reads as the parent's tip.

**Why the restack is not cosmetic — a defect this step found by measurement, not by reading.** On
the predecessor's ref as the parent left it:

| Probe on the predecessor `refs/heads/fusion/stas-391-…` | Result | Consequence |
|---|---|---|
| `git merge-base --is-ancestor <390 tip> <predecessor tip>` | **rc=1** | The parent's line is **not above** the 390 line, so the operator's second `git merge --ff-only` would have **refused** it once the first had landed. The two-leg landing the parent promised was not mechanically executable. |
| `git grep -c neutraliseStructureTokens … <module>` | **rc=1, zero hits** | Its module carries **no neutraliser**: the announcement was authored on ground that does not hold the invariant this pair exists to protect. |
| The same grep on the delivered tip | **2 hits** | The delivered line is announced on ground that does hold it. |

Ground choice: AC1 is re-measured inside the capture and still reads zero (`rc=1`) — the engine
trunk does not carry the neutraliser — so the 392 line is rebased onto the **rebased 390 tip**
(`e6b1fe4c7f`) and AC3's stack proof is printed: `0 1` above that ground, `is-ancestor` rc=0 against
both the 390 tip and trunk, conflict predicate rc=0 with no `CONFLICT` line. The range-diff of this
leg is one commit against one commit and shows exactly one `!`, whose body is pin (c) plus the
commit-message paragraph recording it: nothing else moved.

**The three pins, inside the one code commit** (`037a480cc5`; `git show --stat` names all three
files, `git log --oneline refs/heads/main..<ref>` shows three commits in total and no separate
test-fix commit):

| Pin | Where | What it asserts now |
|---|---|---|
| (a) | `tool-result-authority.test.ts`, the `dropped` list | `[]` is out of the list; `undefined`, `null`, `[{type: "text"}]` and `42` stay in it and keep throwing. |
| (b) | `tool-result-authority.test.ts`, the announcement `it()` | Retitled onto the announcement; asserts `delivered="zero-bytes"`, the notice, one envelope at zero authority, and the equality with the `""` state whose equality the old pin refused. |
| (c) | `tool-channel-authority.test.ts`, the anti-sledgehammer `it()` — **this card's delta** | Retitled; the crash assertion is replaced by `delivered="zero-bytes"` + `ZERO_BYTES_NOTICE` for **both** empty states, plus `authoredFrameBody([]) == authoredFrameBody("")` so the two empties cannot drift apart. |

Every reconciled pin carries the one justification sentence in its title: upstream **authors**
`content: []` for a content-less untyped result (`createToolResultMessage` writes
`result.content ?? []`; the `pi-ai` transform normalises a null content the same way), so fail-loud
legitimately narrows **only** for that value. The retained throws and the `buildPrompt` propagation
pin are untouched; `git show` of both test files is in `raw/ac4-probe.txt`.

**AC4 probe, every hit classified** (`raw/ac4-probe.txt`). Grep 1 (`no content to mark`) returns
four hits at the delivered tip: two production throw messages naming payloads that cannot be read at
all, one test line walking the four retained ambiguous payloads, one test line pinning that the
failure reaches the caller through the assembler. Grep 2 (`no content blocks` / `nothing delivered`
/ `empty content array`) returns two: a pre-existing unrelated provider fixture, and pin (c)'s
reconciled title. The only emptiness vocabulary this deliverable introduces is
`delivered="zero-bytes"` and `ZERO_BYTES_NOTICE` — the two the landed code already authors.
Trunk still carries the guard arm at `tool-result-authority.ts:77` (`content.length === 0`); at the
delivered tip that arm is gone and only the emptiness branch on `text` remains, grep-printed rather
than asserted.

## Step 5 — the second fresh red-green, on the announcement layer

Run at the delivered tip `037a480cc5`, both files named, no `-t` filter; the full per-test
transcript is `raw/red-green.txt` under `LAYER 2`.

| Run | `Test Files` | `Tests` | rc |
|---|---|---|---|
| GREEN at the delivered tip | 2 passed (2) | **23 passed (23)** | 0 |
| RED — only `tool-result-authority.ts` checked out at the ground SHA `e6b1fe4c7f` | 2 failed (2) | **2 failed \| 21 passed (23)** | 1 |
| GREEN again after `git checkout HEAD -- <module>` | 2 passed (2) | **23 passed (23)** | 0 |

**This is the discriminating red-green, and it discriminates.** The two failures are exactly the two
announcement tests — `an empty tool result carries the provenance of its emptiness > announces that
no content was delivered when the …` and `an ordinary payload is not rewritten (the anti-sledgehammer
guard) > delivers an untyped tool's empty content array …`. Every frame and role-key test stayed
green, which is what separates this ground from the one the parent card had to work on: reverting the
guard arm on a trunk base would have taken the neutraliser with it and red-lined the whole channel
suite. Checking the module out at the **390 ground** reverts the guard arm alone.

The witness imported nothing that had been fixed: only the module was checked out, both test files
stayed at the delivered tip, and `git status --porcelain` printed nothing after the restore.

**Suite baseline, both delivered lines measured** (`raw/suite-baseline.txt`): the 390 line
(`e6b1fe4c7f`) and the 392 line (`037a480cc5`) each read `16 files / 387 tests passed`, the failing-
name set is **empty** at both, and zero entries were skipped. So the claim stays what it was —
**this diff added zero failures** — and is not stated as "the suite is green". The repo-wide
line-count guardrail still exits rc=1 over 128 files of pre-existing debt, printed in full and never
`--update`d, with a measured **0** offenders in `pi-claude-cli`.

## Step 6 — the close, and what the operator now holds

`raw/operator-landing-leg.txt` is the operator's instrument: both refs with their counts, the
occupancy reading, AC1 re-probed at the close, the trunk equality check, the verbatim liveness
line, and the two merges in order —

```
  git rev-parse refs/heads/main          # must print 4fa1ea0e64… (the trunk this file names)
  git merge --ff-only fusion/stas-390-tool-channel-authority
  git rev-parse refs/heads/main          # must now equal e6b1fe4c7f…
  git merge --ff-only fusion/stas-392-empty-tool-result-announcement
```

with the stop condition written next to them: a trunk that moved between the two voids the second
leg, because that leg was rebased onto `e6b1fe4c7f` specifically.

`generate.sh` was then run end to end (all ten sections). It rewrote every capture, including
`engine-diff.txt` and `engine-commits.txt`, and left the nested checkout clean and on
`fusion/stas-392-empty-tool-result-announcement`. The regenerated layer counters match the
committed ones exactly (`8 failed / 15 passed` and `2 failed / 21 passed`), which is what makes the
captures reproducible rather than a transcript of one lucky run. One correction came out of that
re-run: layer 1 initially reported `10 failed` because it had been measured while layer 2's tests
were already in the tree, so the section now checks each layer's own branch out first — the
counters in `raw/red-green.txt` are labelled by the tip they belong to.

## The close block, as sent

Printed in one command block at `2026-11-05T18:47:39Z` and sent the same tick, after the prints,
through the two operator-visible channels this session has (`fn_send_message` is not on the
Workflow Executor tool surface — the Step 3 deviation applies unchanged). The message body is
ASCII-punctuation Slovak because the board's agent-text gate rejects typographic characters
(`U+2014`, `U+2013`, `U+201E`, `U+2026`, `U+00D7` were named by the first rejection and nothing
was persisted by it); refs, SHAs, commands and the liveness line are verbatim either way.

Two channels carried it: a card log entry, and registered artifact
`2a48bb8a-e5d6-439e-bdb3-611664382166`.

```
STAS-392 - uzaviraci blok: obe engine linie su pripravene na fast-forward, trunk zostal nedotknuty.

Merane tohto ticku (2026-11-05T18:47:39Z), vsetko v jednom prikazovom bloku tesne pred odoslanim:

1) refs/heads/main = 4fa1ea0e64bccc32e2d3116c534706d1b953931c
   Na zaciatku behu (krok 0) aj na jeho konci ma trunk tutu istu hodnotu - karta do neho nezasiahla.

2) Dve dodane linie, obe pripravene na fast-forward:
   - fusion/stas-390-tool-channel-authority = e6b1fe4c7f4bafa36727e33e8df7c400e87c6a91
     rev-list main...390 = "0 2"; merge-base --is-ancestor rc=0; merge-tree rc=0, ziaden riadok CONFLICT;
     diff --shortstat = 2 files changed, 275 insertions(+), 6 deletions(-)
   - fusion/stas-392-empty-tool-result-announcement = 037a480cc562ef994aac0b19215613cb93e2b500
     rev-list main...392 = "0 3"; is-ancestor rc=0; merge-tree rc=0;
     voci 390-linii = "0 1" a is-ancestor rc=0, takze po prvom merge je aj druha noha fast-forward.
   - Predchodcu karta nepohla: fusion/stas-391-empty-tool-result-announcement = eb20a6bfc583786b8c31cfe4f677237ebae54e10

3) Ziadna z dvoch noh nie je pristata. Patria operatorovi - presne dva prikazy v tomto poriadi,
   z checkoutu enginu, ktory je na refs/heads/main, s cerstvym overenim trunku pred kazdym:

     git rev-parse refs/heads/main
     git merge --ff-only fusion/stas-390-tool-channel-authority
     git rev-parse refs/heads/main
     git merge --ff-only fusion/stas-392-empty-tool-result-announcement

   Pohyb trunku medzi nimi zneplatnuje druhu nohu: potom sa rebazuje, nikdy sa nemerguje nasilu.

4) Dokazy cerstve na novej baze (nic z rodicovskych kariet nie je pouzite ako nas dovod):
   - vrstva 1 (neutralizer ramca a role-key): zelena Test Files 2 passed (2), Tests 23 passed (23);
     cervena na trunku = 8 failed | 15 passed, s checkoutnutym iba tool-result-authority.ts.
   - vrstva 2 (mazany guard + tri piny): zelena 23/23; cervena na zemi nesucej neutralizer =
     2 failed | 21 passed. Padaju len oznamovacie testy; kazdy test ramca a role-key ostava zeleny.
   - baseline celeho src: 16 files / 387 tests passed, mnozina zlyhanych nazvov oproti zemi prazdna,
     0 skipped. Tvrdenie tohto diffu: nepridal ziadne zlyhanie, nie "suite je zeleny".

5) AC1 na konci: git grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src
   = 0 zasahov (rc=1). invariant na trunku NIE JE a tato karta to sama nemoze splnit; splni ho az
   prvý z oboch merge-ov. Dovtedy su karty 388/389/390/391 zelene len na doske.

6) running build unidentifiable - fix liveness unverified: ani po oboch merge-och nie je zmena ziva,
   kym sa neprebuildne a znova nespusti daemon; tato karta sa ho nedotkla.

Okno ma hodiny, nie dni: engine rail mergeuje 2-4-krat denne a presne takto zomrel predchadzajuci
zeleny stav 390 - dva rail merge-oe po uzavreti karty.

Kanal: fn_send_message nie je na povrchu nastrojov Workflow Executor (krok 3 meral tu istu
skutocnost: "Tool fn_send_message not found"). Identické bajty preto išli v tom istom ticku ako
zaznam logu karty a ako tento artifact.
```

The close print that produced item 1 and item 2 verbatim, kept here as the instrument that backs
them (`git -C` against the nested checkout, run from this card's worktree root):

```
CLOSE_PRINT_2026-11-05T18:47:39Z
trunk=4fa1ea0e64bccc32e2d3116c534706d1b953931c
porcelain=0
R390=e6b1fe4c7f4bafa36727e33e8df7c400e87c6a91
R392=037a480cc562ef994aac0b19215613cb93e2b500
R391predecessor=eb20a6bfc583786b8c31cfe4f677237ebae54e10
count_390=0 2
count_392_vs_trunk=0 3
count_392_vs_390=0 1
anc_390_rc=0
anc_390_over_392_rc=0
shortstat: 2 files changed, 275 insertions(+), 6 deletions(-)
shortstat: 3 files changed, 333 insertions(+), 22 deletions(-)
ac1_probe_rc=1 (1 + no output = ZERO hits on trunk)
```

### Corrections made while closing

1. `generate.sh` used to hard-code the run-start trunk SHA into the landing section, which is the
   memorised-SHA shape AC0 forbids. It now derives the run-start value from Step 0's own
   `raw/ac0-roots.txt` measurement, so a re-run against a moved trunk prints `NO - trunk moved
   during this run` instead of restating a stale constant as if it were current.
2. The landing capture now probes the engine repository's own `CLAUDE.md` for a landing-hygiene
   rule rather than leaving the citation question unanswered. Measured at this ground: the blob
   exists (`git cat-file -t refs/heads/main:CLAUDE.md` prints `blob`) but holds no landing-hygiene,
   fast-forward, or merge rule (`git grep -n -iE 'landing hygien|ff-only|fast-forward'` prints
   nothing, rc=1). There is therefore nothing in the engine's guidance to cite, and the
   `Landing hygiene` section this card was pointed at belongs to the *stash* repository's
   `CLAUDE.md` - importing or duplicating another repository's pinned rule is what the instruction
   forbids, so the two commands stand on their own measurements.
3. A defect in this card's own instrument surfaced while closing and is reported rather than left
   in place: `section_baseline` printed its line-count sentence with backticks inside double
   quotes, so `` `--update` `` ran as a command substitution. The committed `raw/suite-baseline.txt`
   therefore carried a stray shell error line (`generate.sh: line 500: --update: command not found`)
   and a mangled sentence ("neither created nor s.") in the middle of the guardrail disclosure.
   Neither line came from the engine or from vitest; both came from the capture script. The quoting
   is fixed and the section was re-run at `2026-11-05T18:54:42Z`: the same counters as before
   (`Test Files 16 passed (16)`, `Tests 387 passed (387)` at every delivered tip, failing-name set
   empty, `skipped entries: 0`), now without the instrument's own noise. An unused `local` the same
   file carried (`GROUND391`) was removed in the same edit; `shellcheck -S warning` is now clean on
   this script and `bash -n` passes. The earlier `suite-baseline.txt` attachment is superseded by a
   re-attachment of the corrected file.

## Not done yet (running list)

| Item | Status at Step 0 |
|---|---|
| AC1 — invariant on the trunk | **not satisfied and not satisfiable by this card alone**; the trunk census reads 0. Only the operator's two merges change that. |
| AC2 — 390 fast-forward-ready + window announced | **satisfied**: rebase done (`0 2`, ancestor rc=0, merge-tree rc=0), window announced in the measurement tick |
| AC3 — 392 rides on the neutraliser-carrying line, green against the tests 390 brought | **satisfied**: AC1 re-probe still read 0, so the ground is the rebased 390 tip; the delivered line is `0 1` above `e6b1fe4c7f` with `merge-base --is-ancestor` rc=0, and its green run names both test files (`23/23`) and contains every frame and role-key test that was green in Step 2 |
| AC4 — three pins reconciled in the guard-deleting commit | **satisfied**: one commit, three files, no test-fix commit; probe hits classified in `raw/ac4-probe.txt` |
| AC5 — two fresh red-greens on the new ground | **satisfied**: layer 1 `23/23` green, `8 failed / 15 passed` red at trunk; layer 2 `23/23` green, `2 failed / 21 passed` red at the 390 ground — only the two announcement tests, every frame and role-key test green |
| AC6 — delivery boundary held | **satisfied**: `refs/heads/main` = `4fa1ea0e64bccc32e2d3116c534706d1b953931c` at run start and at `2026-11-05T18:47:39Z`; the predecessor `fusion/stas-391-empty-tool-result-announcement` still reads `eb20a6bfc5…`, its Step 0 value; no push, no PR |
| AC7 — reviewable without opening the engine repo | **satisfied**: `generate.sh` regenerates every capture (the `landing` section was re-run at close and rewrote its file), and every capture is attached to this card as a `.txt` — `raw/*.txt`, `engine-diff.txt`, `engine-commits.txt`, plus this file as `findings.txt` |
| The two merges themselves | **operator's hands, by design** — `raw/operator-landing-leg.txt` |
| AC1 (neutraliser on the trunk) | **not satisfied by this run and not satisfiable without violating AC6** — stated, not hidden |
| Board reads for STAS-390 / STAS-391 | done — see Board observations |
