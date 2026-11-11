# STAS-391 findings — an empty tool-result content array on the prompt path

Card: STAS-391. Review level 3 (Full). The code deliverable of this card lives in
the engine repository, not in this one; what lives here is the evidence that lets a
reviewer read the verdict without opening it.

**Ordering gate.** No branch decision, no engine ref name chosen by this card, and
no acceptance-criterion verdict appears in this file before the Step-0 commit
recorded at the end of the Step-0 section exists. Everything above that marker is
measurement only.

## Measurement roots (every number in this file names one)

| Root | What it is | How this session reaches it | Revision / state measured |
| --- | --- | --- | --- |
| R1 | This stash card worktree (`stas-391`, branch `fusion/stas-391`) | native, cwd-relative git | HEAD `49fd708a9`, porcelain 0 lines before the evidence directory; `.engine/…` and `.fusion-tmp/…` proven ignored via the **common** `info/exclude` lines 33/34 in **child-path** form (the bare-name form returns rc=1 for an absent directory, which is not an absence of the rule) |
| R2 | Engine repository, through a nested **linked** worktree this card provisioned | `git -C .engine/stas-391-engine …` from inside it | `refs/heads/main` = `06b1768b18f38a9b4d0d5b35467204e25bfe4396`, subject `RUFU-441: evict memory snapshots the consolidation sweep produces`; nested checkout porcelain 0 lines, both before and after provisioning its toolchain |
| R3 | The live process running this board | own parent-chain walk with `ps` | daemon argv `/usr/bin/node --inspect=127.0.0.1:9229 packages/cli/dist/bin.js dashboard --host 0.0.0.0`, parented by `bash /home/schindler/bin/fusion-ui-watchdog.sh` — the Node script path is **relative**, so the checkout it runs from is not recoverable from `ps`, and no agent-readable surface of this build carries a revision field ⇒ **running build unidentifiable — fix liveness unverified**. Every claim below about what is *running*, including the claim about whether any result of this card is live, inherits that sentence. |

Raw captures land in `raw/`. Every capture regenerates through
`bash stas-391-engine-evidence/generate.sh`, which pins its inputs and marks the
one section per file that is allowed to read a moving ref.

## Step 0 — capability, roots, and the evidence directory

### Execution capability (resolves the plan's open question for *this* session)

`bash` executes. The first probe of this run,

```
$ pwd; echo "SENTINEL-PROBE-A9f3"; git rev-parse --abbrev-ref HEAD; git rev-parse --short HEAD
/home/schindler/git/stash/.fusion/worktrees/stas-391
SENTINEL-PROBE-A9f3
fusion/stas-391
49fd708a9
```

returned non-trivially, so AC0 onward is reachable and the planning session's
refusal (`This session has a read-only workspace boundary and cannot run shell or
verification commands.`) is **not** inherited here. The plan's warning about an
empty tool result was also earned in this run: two multi-command probes returned a
completely empty result and every command inside them re-ran normally moments
later, so no measurement below rests on an empty output — each one is paired with a
sentinel that prints something non-trivial in the same call.

No path-guard refusal was needed or triggered: engine reads go through the
`$HOME/git/Fusion` form only in the one provisioning command below, and every other
git call is `git -C <worktree-relative nested path>`.

### R1 — this card worktree

`git rev-parse --short HEAD` = `49fd708a9`; branch `fusion/stas-391`; `git status
--porcelain` = 0 lines before anything was created. Ignore legality of the two
excluded names was proven rather than assumed:

```
$ git check-ignore -v .engine/stas-391-engine .fusion-tmp/scratch.txt
.git/info/exclude:33:/.engine/     .engine/stas-391-engine
.git/info/exclude:34:/.fusion-tmp/ .fusion-tmp/scratch.txt
rc=0
```

Both rules live in the **common** exclude, so they hold in every worktree of this
repository and no ignore file needs to be committed.

### R2 — the engine repository, one checkout, one revision

**Seam enumeration before touching git:** this worktree had no `.engine/` directory
at all (`ls: cannot access '.engine': No such file or directory`), so there was
nothing to reuse and one checkout was provisioned. Adopting another card's nested
checkout was rejected on sight: it would move every later measurement off the tip
this card has to describe and would stomp a lane this card does not own.

Provisioning command and its verbatim output:

```
$ git --git-dir=$HOME/git/Fusion/.git worktree add --detach "$PWD/.engine/stas-391-engine" refs/heads/main
Preparing worktree (detached HEAD 06b1768b18)
HEAD is now at 06b1768b18 RUFU-441: evict memory snapshots the consolidation sweep produces
```

It is a linked worktree, not a clone: a ref created there is immediately visible in
the engine repository, so no bundle is needed as a handoff. Every engine read and
write in this card runs **from inside** that checkout; the forbidden
`--git-dir=<nested checkout>/.git` form is never used, because its gitlink resolves
to the wrong root and reports every tracked file as deleted.

Measured from inside it: `refs/heads/main` = `06b1768b18` /
`06b1768b18f38a9b4d0d5b35467204e25bfe4396`, and `git status --porcelain` = 0 lines.

**Occupancy, measured here rather than assumed in either direction.**
`git worktree list --porcelain` prints 389 lines across 97 registrations; exactly
one of them holds a branch this card names:

```
/home/schindler/git/Fusion  06b1768b18 [main]
```

`refs/heads/main` is therefore held by the primary engine checkout outside this
worktree, and no card checkout holds any `fusion/stas-38*` or `fusion/stas-39*`
branch. The full listing is not committed as a capture — it is other cards' lanes
and it churns between runs, which would make the generator unable to reproduce its
own file.

**Toolchain.** The nested checkout arrives with no `node_modules`, so the engine's
own package manager provisioned it: `pnpm install --frozen-lockfile
--ignore-scripts` at the nested root, rc=0, exit in 4.1 s against a warm store,
with no lockfile or `package.json` change; `packages/pi-claude-cli/node_modules/.bin/vitest`
exists afterwards and `git status --porcelain` in the nested checkout is still 0
because `.gitignore:7` carries `node_modules/`. Toolchain measured: node `v26.10.0`,
pnpm `10.33.0`. `--ignore-scripts` skips postinstall native builds; the later runs
in this card load no native binding.

### Gate D — is the dependency actually closed

Read per-ID, because this board's index is known to carry a stale column while the
per-ID record is authoritative.

| Field | Measured |
| --- | --- |
| `fn_task_show(STAS-390)` column | `done` |
| steps | 7/7 done |
| `workflowStepResults[0]` verdict | **not exposed** — `fn_task_show` prints column/steps/prompt/log only, and `fn task show STAS-390 --json` ignores `--json` and prints the same prose (measured: 2 203 bytes, no JSON object) |
| nearest content, the card's own log | quoted verbatim in `raw/dependency-gate.txt`; the lane that failed is the **board-side** post-merge CI evidence gate, whose stated cause is that the `postMergeCiRepo` project setting is unset in this deployment |

Gate D's stop-condition as the card states it — stop while STAS-390 sits in
`triage`, `todo`, `in-progress` or `failed` — does **not** fire on this measurement.

What a `done` badge cannot make true is content, so the content behind the badge was
measured instead of trusted, and it is in `raw/dependency-gate.txt`: the branch
STAS-390 delivered is **ahead of the pinned base by 2 commits with the base behind
by 0**, and `merge-base --is-ancestor` in that direction returns rc=0 — the shape of
a line that has been prepared for a fast-forward and not yet fast-forwarded. The
engine's trunk-side census in the same capture is what decides this card's branch
later, and nothing in this section pre-empts it.

### AC0 census, and the message census

Recorded with rcs and hit counts in `raw/dependency-gate.txt`, read at the pinned
base and not at a moving ref. In one line each, so a reader can see what was looked
for: `ZERO_BYTES_NOTICE` is present at one file with 2 hits; `no-content-blocks`,
`EMPTY_NOTICES`, `DeliveredText`, `emptinessOf`, `neutraliseStructureTokens`,
`ENVELOPE_DELIMITER` and `ROLE_KEY` each return rc=1 with zero files; the string
`no content to mark` appears at 5 locations across 2 files and `no payload was
delivered` at 2 locations across 2 files. The two single-stated guards name one
production module and its test file each. Which of those numbers forces which
branch is Step 1's job.

### Parent captures — what was actually readable

| Cited by the plan | Readable from this worktree? |
| --- | --- |
| `.fusion/tasks/STAS-388/attachments/…` and the other five attachment paths | **No.** `ls .fusion/tasks/` → `No such file or directory`; this worktree has no `.fusion/` directory at all. Cited as provenance, never opened, and no claim below depends on them. |
| `stas-388-engine-evidence/` | Yes, tracked here: `findings.md`, `engine-diff.txt` (the landed module and its landed tests verbatim), `raw/` captures. |
| `stas-389-engine-evidence/raw/red-green.txt` | Yes, read in full — it is the admissible red-green shape this card copies: RED = only the production file checked out at the base SHA, test file never touched, both vitest counters, failing assertion lines quoted, no `-t` filter. |
| `stas-389-engine-evidence/raw/step1-witness.txt` | Yes, read in full — the probe method this card copies: an untracked test written under `src/__tests__/` inside the nested checkout, calling `buildPrompt` / `buildResumePrompt` directly, writing its report to a file, then deleted. |
| `stas-389-engine-evidence/raw/suite-baseline.txt` | Yes — lane boundary and counters. |
| `stas-389-engine-evidence/pi-ai-census.txt` (`raw/pi-ai-census.txt`) | Yes — second upstream normalisation site and the lane-2 verdict. |
| `stas-390-engine-evidence/findings.md` and its six captures | Yes, and this card reads them as the most recent word on the same file: STAS-390 measured trunk at `af54c70c39`, delivered a rebased line at `d38b478184`, and left two things owed — the operator fast-forward, and an answer on the other, unlanded line. |

### Duplicate search, re-run rather than inherited

`fn_task_list` over this board: the only in-progress card is STAS-391 (this one). No
open card besides this one names `packages/pi-claude-cli/src/tool-result-authority.ts`
— STAS-388, STAS-389 and STAS-390 are all in `done`, STAS-363 and STAS-374 own the
*stash* repository's trunk absorption, a different repository. Nothing to stop for.

### Step-0 commit gate

The commit that carries this section is recorded here once it exists, and no branch
decision, engine ref name chosen by this card, or acceptance verdict appears anywhere
above this line before it.

Step-0 commit: `f2a97bbbd` — `feat(STAS-391): complete Step 0 — capability probe,
roots, dependency gate`. It exists, so the ordering gate above is discharged and the
Step-1 verdicts below are legal to state.

## Step 1 — what `content: []` does on the prompt path, and which branch that forces

### The base, re-read at the start of this step

`git -C .engine/stas-391-engine rev-parse refs/heads/main` → `06b1768b18f38a9b4d0d5b35467204e25bfe4396`,
identical to the base Step 0 pinned. Nothing drifted between the two steps, so the
census in `raw/dependency-gate.txt` and the witness below describe the same commit.

### The witness — nine payload shapes through both real builders

`raw/base-witness.txt` holds the probe, the honesty label, and the full assembled
prompts. Eighteen synthesised calls into `buildPrompt` and `buildResumePrompt`, the two
subject shapes plus controls. Verdict per shape:

| # | `content` | lane | outcome on **both** builders | message, verbatim |
| --- | --- | --- | --- | --- |
| W1 | `[]` | built-in `bash` | **THREW** | `tool result payload has no content to mark: the content-block array is empty, so no payload was delivered` |
| W2 | `[]` | custom / MCP `linear__search_issues` | **THREW** | the same message |
| W3 | `""` | `bash` | rendered | `TOOL RESULT (Bash):` then `<untrusted_tool_output authority="none" delivered="zero-bytes">` / `[the tool delivered an empty payload: zero bytes of output]` |
| W4 | `[{type:"text",text:""}]` | `bash` | rendered | W3's bytes |
| W5 | `undefined` | `bash` | THREW | `…expected a string or a content-block array, got undefined` |
| W6 | `null` | `bash` | THREW | `…got object` |
| W7 | `42` | `bash` | THREW | `…got number` |
| W8 | `[{type:"text"}]` | `bash` | THREW | `tool result text block has no content to mark: expected a string, got undefined` |
| W9 | `"3 issues found"` | custom | rendered | payload verbatim inside one `authority="none"` envelope |

W1 and W2 are the card's subject, and they fail identically on both lanes — that is the
point: the throw is not a built-in-tool edge case, it sits in the wrapper every tool
result passes through. W3 is the announcement this card wants `[]` to travel; it already
exists, in the same module, and it is exactly what W1 would have printed.

Honesty, stated where the capture states it too: W1 and W2 are synthesised. The corpus
search the card body cites found zero recorded instances of a `toolResult` message
carrying an empty content array, and this card implies none. What moved from
reachability into measurement is the code path, not an observed outage.

### What makes that table a read of the base — a repair, not an original guarantee

A probe that runs in a checkout sitting on the branch measures the branch. The hand-run
Step-1 capture got this right: `git show 8c6fa9338:stas-391-engine-evidence/raw/base-witness.txt`
records W1 and W2 as `RESULT: THREW`. The regression arrived with the packaging commit
`73610da20`, when the probe moved into `generate.sh` and ran in a working tree that the
suite-baseline block had already restored to the branch tip. The committed capture then
recorded W1 and W2 as `RESULT: returned a prompt` carrying `delivered="zero-bytes"` plus
the notice — a behaviour only the post-fix module has — underneath an honesty label that
claimed a read of `06b1768b18`. As shipped, the pack refuted the premise of the card that
ships it, and the operator reading only this directory could not tell `land this` from
`already fixed`. Code review caught what this run did not, against PROMPT.md's own
decisive check.

The generator now puts the tree on the base the same way the two captures that were
already correct do — `git checkout $BASE_SHA -- <module> <test>` before the probe is
installed, `git checkout $TIP -- <module> <test>` after it — and the capture prints the
mechanism rather than asking to be believed:

| Printed in `raw/base-witness.txt` | Value in the regenerated capture | What it excludes |
| --- | --- | --- |
| `git rev-parse 06b1768b18:…/tool-result-authority.ts` | `3f942b3db329f34811d60147e854a49b58e74b61` | — |
| `git hash-object` of the file the probe found | the same blob, printed `EQUAL` | the probe importing the branch's fix |
| porcelain while swapped, then after the restore, plus `git diff --exit-code <tip>` | `2`, then `0` with rc=0 and no bytes | a partial restore or a surviving probe |

Regenerated, the capture prints **THREW** for W1 and W2 on both builders and both lanes,
with the guard's message verbatim, and `RESULT: returned a prompt` only for W3, W4 and W9.
The rows above are unchanged by the repair — they were transcribed from the hand run — but
the capture behind them now says the same thing, which is what AC7 asks of the pack: a
reviewer who never opens `/home/schindler/git/Fusion` can see which revision the witness
loaded and can judge the card against it.

### The upstream claim, re-measured rather than inherited

| Site | Installed location | Line |
| --- | --- | --- |
| the producer the card body names | `@earendil-works/pi-agent-core@1.1.0` → `dist/agent-loop.js`, inside `createToolResultMessage` (4 store copies, 1 distinct checksum) | `content: finalized.result.content ?? [],` at line 663, under upstream's own comment `Untyped tools (JS extensions) can return results without content; normalize` |
| the second site the parent census found | `@earendil-works/pi-ai@1.1.0` → `dist/api/transform-messages.js` (5 store copies, 1 distinct checksum) | `msg.content == null ? { …msg, content: [] } : msg` at line 49 |

Both packages resolve to 1.1.0 in this checkout's own tree; `pi-ai` and
`pi-coding-agent` are linked directly under `packages/pi-claude-cli` and `pi-agent-core`
is present only through the virtual store. The body's line numbers were re-read, not
copied: both are exact.

### Gate B — read once as a stop, re-read as a mandate, and what the re-read cost

The measurements in this file did not change between the two readings; the reading did.

**Step 1's reading, recorded and then discarded.** With the throw live on trunk and
STAS-390's neutraliser absent from it, Step 1 called the situation "Branch C", concluded
the module must not be edited, and delivered the change as
`raw/unapplied-draft.patch` plus a decision request.

**Why that was wrong.** The card's own text was read again against the witness:

| What the card says | Where | What it forces |
| --- | --- | --- |
| "End on a verified branch tip plus the operator's landing leg" | mission | a tip that can be verified, i.e. a branch |
| "Author the change on a newly named engine branch" | Step 2's title | a branch, unconditionally |
| "Verify the branch tip and write the operator landing leg" | Step 5's title | the same |
| the later steps become unnecessary only if the live trunk already renders a notice for `[]` | review-level paragraph | W1/W2 refute that, so the later steps stand |
| "do not touch `tool-result-authority.ts` before STAS-390 closes" | the card's one hard constraint on the file | discharged: STAS-390 is `done` (7/7, gate D above) |

Nothing in that set conditions the work on STAS-390's *landing*; it conditions it on
STAS-390's *closure*, which had already happened. The unapplied patch was therefore an
under-delivery dressed as a gate, and Step 2 onward was executed after the correction.
The card log carries the same correction independently: an earlier session of this card
measured the same absent-remediation fact and recorded it as the branch that **proceeds**
with this card's own vocabulary, not the one that stops.

The correction is also why `raw/unapplied-draft.patch` no longer exists. Its content is
now a commit, and keeping a draft of a change beside the change would leave two sources
of truth; `engine-diff.txt` is `git diff base..tip` and nothing else.

What the measurements do still decide is the **vocabulary**: the STAS-388 remediation's
`no-content-blocks` / `EMPTY_NOTICES` wording is absent from trunk (`rc=1`, zero files)
and trunk's own `zero-bytes` notice is present, so this card announces the empty array
with the notice trunk already owns instead of minting a synonym. Which word the lane
should prefer is still an operator call and is put in `raw/operator-landing-leg.txt`
with the costs measured.

## Step 2 — the change, authored on its own branch

| Field | Measured |
| --- | --- |
| branch | `refs/heads/fusion/stas-391-empty-tool-result-announcement` |
| tip | `eb20a6bfc583786b8c31cfe4f677237ebae54e10` (`eb20a6bfc5`) |
| base it was cut from | `06b1768b18f38a9b4d0d5b35467204e25bfe4396` — the measured trunk tip, not STAS-390's unlanded line |
| commits | 1, `STAS-391: announce an empty tool-result content array instead of crashing prompt assembly` |
| files | `packages/pi-claude-cli/src/tool-result-authority.ts` (+11/−18) and `src/__tests__/tool-result-authority.test.ts` (+35/−2); `git diff --name-only base..tip` prints exactly these two |
| nested checkout after the commit | `git status --porcelain` = 0 lines |

Why the base is clean trunk and not `d38b478184`: the STAS-390 line is still 2 commits
ahead of trunk with trunk 0 behind, so building on it would make this card carry two
STAS-389 commits through the lane and pre-commit the operator to a line that has not
landed. The cost of that choice is not hidden — it is the collision measurement below.

What the commit does:

- **deletes** the `if (content.length === 0) throw …` arm in `readToolResultText`, so an
  empty array returns `""` and falls into the **existing** emptiness branch in
  `markToolResultAsUntrusted` — `delivered="zero-bytes"` plus `ZERO_BYTES_NOTICE`. No new
  branch, no `?? default`, no second codepath, no synthesised content: AC1 and AC2 are
  satisfied by deletion, the smallest change available.
- **keeps** the `Array.isArray` / `typeof` arms, so W5–W8 keep throwing exactly as they do
  now (AC3), and keeps the envelope emitted from the one place at `authority="none"` — the
  landed `b6e6cb73f1` zero-authority layer is untouched.
- **reconciles the two landed pins** in the same commit, at
  `src/__tests__/tool-result-authority.test.ts:128-141`: the `dropped` fixture loses `[]`,
  and the test that refused to print the same bytes for the two empty states is replaced by
  one that requires it. The replacement's name carries the upstream normalisation —
  `createToolResultMessage writes result.content ?? []`, `pi-ai normalises a null content
  the same way` — so the test encodes *why* the array is an input and not a program error.

## Step 3 — green on the new tip, and the lane's prior state beside it

`raw/suite-baseline.txt` pairs the same command at both revisions, and the pairing is now
honest about how the base is reproduced while the nested checkout sits on the branch: the
branch's whole footprint is two files, so checking exactly those two back to their base
blobs reproduces the base tree. The restore is printed, not promised (`porcelain 0`).

| Revision | `vitest run` in `packages/pi-claude-cli` | rc |
| --- | --- | --- |
| base `06b1768b18` (both files checked out at base blobs) | 15 files, 376 tests, 0 failing | 0 |
| tip `eb20a6bfc5` | 15 files, 376 tests, 0 failing | 0 |

Zero delta is expected here, not a coincidence: one pin is *replaced*, not added beside
the other, so equal counters are the check that no case was quietly dropped.

| Gate | Command, at the tip | Result |
| --- | --- | --- |
| lane test file | `vitest run src/__tests__/tool-result-authority.test.ts` | 12 passed, rc=0 — printed case by case in `raw/red-green.txt` |
| package typecheck | `tsc --noEmit` | rc=0 |
| repo line-count guardrail | `node scripts/check-file-line-count.mjs` | rc=1 repo-wide (128 files), **0** of them under `packages/pi-claude-cli`, at the base and at the tip. The module grows 120 → 127 lines against a 2000-line cap, so this card neither tripped the gate nor inherited a trip |

Two claims from Step 1's draft of this file are corrected by that last row. The guardrail
is a **root** script (`check:line-count` in the root `package.json`), not a step of the
package's `test` script, which is only `vitest run --reporter=dot`; and it names **no**
`pi-claude-cli` file — the earlier sentence naming `prompt-builder.ts` at 1054 lines and
`extension-tools.ts` at 1025 as "the package's red" was read off a truncated tail and is
withdrawn. The package's own suite exits 0 at both revisions.

## Step 4 — red, in the parent card's method, on tracked files

`raw/red-green.txt`, produced by the same generator that produces the green:

```
module checked out at the base, test file left at the tip:
  git checkout 06b1768b18 -- packages/pi-claude-cli/src/tool-result-authority.ts
  vitest run src/__tests__/tool-result-authority.test.ts

  × … > announces that no content was delivered when the content-block array is empty
    → tool result payload has no content to mark: the content-block array is empty, so no payload was delivered
  ❯ readToolResultText src/tool-result-authority.ts:78:11
  ❯ markToolResultAsUntrusted src/tool-result-authority.ts:109:16
  ❯ toolResultContentToText src/prompt-builder.ts:583:10
  ❯ buildPrompt src/prompt-builder.ts:344:18
  Test Files  1 failed (1)      Tests  1 failed | 11 passed (12)      vitest rc=1
```

One red of twelve, and it is this card's own test, failing with the guard's message,
through the real prompt path. The other eleven pass on both revisions, which is the
statement that nothing else in the lane moves. No `-t` name filter is used anywhere, so a
renamed test would surface as a vanished case rather than a false pass. The swap is
undone in the same capture and the undoing is verified: `git status --porcelain` = 0 and
`git diff --exit-code <tip> -- <both files>` silent, with `HEAD` printed as the tip.

The two scratch probes Step 1 needed to learn anything at all (RUN S, RUN R) are gone from
the evidence. They were the reachable substitute while no branch existed: RUN R ran the
patch's own test text against the untouched base and produced 1 failed of 12 — the same
number the formal method produces now that the tracked file exists on a tip.

## Step 5 — the tip, and what it costs to land

Trunk moved while this card ran. `raw/operator-landing-leg.txt` measures trunk at run
time instead of restating the base: `refs/heads/main` = `c20b8e4eaa` (`RUFU-480: retire
the stale-binary schema refusal…`), **ahead=1 behind=1** against the branch, so no
fast-forward is available and the branch must be rebased onto it. The rebase is measured, not assumed cheap: `git diff --stat base..trunk` over the module, its test file and the
STAS-390 test file is **empty** — the new trunk commit touches none of them — and the
branch's own diff is accepted by that revision by both instruments (GNU patch rc=0,
`git apply --check` rc=0).

That fact is now stated at the top of `engine-commits.txt` as well, derived from the same
measurement instead of written as prose. An earlier revision of that file printed
`(refs/heads/main, unchanged)` nine lines above its own measurement of trunk one commit
ahead, so a reader who opened only `engine-commits.txt` concluded a fast-forward was
available. The label is computed now: `unchanged` only when the two SHAs are equal,
otherwise `MOVED by N commit(s) since the pinned base, and NOT by this card`, naming the
required rebase in the same block. AC6's "the card says so" belongs at the point of
delivery, not only in the landing-leg file.

The revisions where landing actually costs something:

| Against | `patch --dry-run` | `git apply --check` | `merge-file` at the true merge base |
| --- | --- | --- | --- |
| `c20b8e4eaa` (current trunk tip) | rc=0 | rc=0 | — |
| `d38b478184` (STAS-390's line) | **rc=0** — hunk 1 with fuzz 2, hunks 2–3 at a 44-line offset | **rc=1** — `patch failed: …tool-result-authority.ts:14` | rc=0, 0 conflict blocks per file; merge base = the pinned base |
| `6a59cf4d51` (STAS-388 remediation) | rc=1 — 3/3 module hunks and 1/2 test hunks FAILED | rc=1 on both files | rc=2, 2 conflict blocks per file; merge base = `b6e6cb73f1` |

The disagreement in the middle row is the landing risk in one number: a fuzz-tolerant
apply onto STAS-390's line silently produces a module whose strict applicator refuses, and
a merge produces **no** textual conflict at all — so nothing would warn that the line's
own pin at `src/__tests__/tool-channel-authority.test.ts:200`,
`expect(() => promptWithToolResult([])).toThrow(/no content to mark/)`, asserts the
opposite of what this branch now does. That file is not touched here: deciding which empty
state deserves a throw is a vocabulary call for the operator, quoted in full in
`raw/base-witness.txt`.

Against `6a59cf4d51` there is nothing to reconcile: that revision already made this card's
change under the name `no-content-blocks`. If it lands first, STAS-391 is duplicate work
and should close as subsumed.

## Determinism

Two consecutive full runs of `generate.sh` — each including the witness, both suite runs,
the whole guardrail scan, the RED swap and all three collision measurements — produce
**7 of 7 captures byte-identical**, compared with `md5sum` over the seven files. The pair
was re-run after the witness repair described above and after the `engine-commits.txt`
label became derived, and the seven digests still agreed; the repair is deterministic
because the blob hashes it prints are properties of the two pinned commits, not of the
machine. What determinism does **not** buy is stated in error 7 below: it reproduced a
capture that had loaded the wrong revision. The script's header records what buys the
repeatability: one pinned
base for every read, trunk read through one named `MAIN_SHA` measured at run time, vitest
output scrubbed of timings and its absolute run path, `grep`-with-no-match rcs captured
rather than left to `set -e`, and no `head` in any pipeline.

One exception is measured rather than waved away, because a later run caught it. Across a
session boundary the same seven digests differ in exactly one line: the `LIVE` block of
`raw/dependency-gate.txt` prints the engine repository's current worktree-registration
count, which rose from 94 to 95 while this card sat between runs. That block is declared
in the generator as the only prose allowed to move, and its rows are other cards' lanes.
So the claim is stated at its true strength: **7 of 7 byte-identical for two consecutive
runs in one machine state, and 6 of 7 across machine states, the seventh differing only in
that declared live count.** A reviewer comparing this directory against a fresh generation
on a busier machine should expect that one line and no other.

Two determinism defects had to be fixed to earn it, both of them silent:

1. Whole-suite `--reporter=verbose` output interleaves differently on every run because
   vitest runs files concurrently. The baseline and tip runs now keep only the summary
   lines; the per-case list this card relies on is single-file, in `raw/red-green.txt`.
2. `out="$(cmd)"; rc=$?` under `set -e` aborts the generator on any non-zero command —
   which is exactly the class of result worth recording. It is now `rc=0` followed by
   `out="$(cmd)" || rc=$?`.

## Lint and the gates that could actually apply

| Gate | Command | Result | Why it is the right gate here |
| --- | --- | --- | --- |
| shell syntax | `bash -n …/generate.sh` | rc=0 | the generator is an executable deliverable of this card |
| shell lint | `shellcheck …/generate.sh` | rc=0 — after SC2038 (`find … | xargs md5sum` → `-exec … {} +`), SC2034 (`left` unused) and five SC2028 lines (`\'` inside double quotes prints a backslash) | the only linter in this environment that covers what this card wrote; regeneration afterwards is 7/7 byte-identical, so the fixes changed no measurement |
| repo lint | `ruff check backend/ cli/`, `ruff format --check backend/ cli/` | rc=0 — `All checks passed!` / `867 files already formatted` | the repository's CI-identical gate; it covers `backend/` and `cli/`, which this card does not touch, so it is shown green rather than argued irrelevant |
| engine typecheck | `tsc --noEmit` in `packages/pi-claude-cli`, at the tip | rc=0 | the engine gate this card can actually move, now that it moves code |
| engine suite | `vitest run`, base and tip | 15 files / 376 tests / 0 failing / rc=0 at both | prior state and delta in one capture |
| engine lint | not run | — | the engine repository exposes no lint script in this package (`package.json` has `test` and `typecheck` only), so there is nothing to run; the suite and `tsc` are the gates it has |

## Seven errors this run caught in its own instruments

1. `merge-file` was first run against `6a59cf4d51` using the pinned base as the common
   ancestor; that commit forked from `b6e6cb73f1`, inflating the conflict count by 35
   unrelated commits. Re-measured at the true merge base the numbers survive — rc=2, 2
   blocks per file — and the merge base is printed beside every number since.
2. An earlier reading claimed STAS-390's line also touches `tool-result-authority.test.ts`
   near line 205. It does not: `git diff -U0` prints module hunks only. The number came
   from a scratch merge output read as a revision diff.
3. `git apply --check` was reported as stricter than GNU patch on the same content. The
   strictness is real, but the first instrument used to prove it was invalid — see 5.
4. The simulation's scratch copy of the patched module was first installed under
   `packages/pi-claude-cli/src/`, and the probe reported 3 failures instead of 1. Two were
   the instrumentation: the lane enforces "one production module states the zero-authority
   rule" with a tracked test that greps untracked files under `packages/`, and it found a
   second stating file — mine. Moving the scratch copy under `src/__tests__/`, the same
   exclusion that test applies, restored 1 of 12. Read the other way: AC5's invariant is
   enforced by a test that caught a violation within one run of this card existing.
5. **`git apply --check` was answering for the wrong repository.** A scratch copy that
   merely *sits inside* this checkout is judged by the enclosing engine worktree, and every
   revision then returned rc=0 — including the two that GNU patch rejects, and including a
   revision where the file content differs in three hunks. `--no-index` is no fix: it makes
   git *skip* untracked files and report rc=0 for them. The instrument now gives each
   scratch copy its own one-commit repository, and the verdicts changed accordingly:
   trunk-tip rc=0, STAS-390 line rc=1, STAS-388 remediation rc=1. The legend in
   `raw/operator-landing-leg.txt` states this so the next reader does not trust a bare
   `git apply --check` in a scratch directory again.
6. **The baseline's "base" run was measuring the tip** once the branch existed, because it
   was an unqualified `vitest run` in a working tree that had moved onto the branch. It is
   now an explicit two-file checkout at the base blob, with the branch's `--name-only` diff
   printed as the proof that those two files *are* the whole delta, plus a printed restore.
7. **The regenerated base witness measured the fixed tip and was committed as the base
   witness.** Same shape as 6, one decisive difference: 6 was caught while writing it, this
   one shipped in `73610da20`. The probe ran in a tree sitting on the branch, so
   `raw/base-witness.txt` recorded the post-fix announcement for `content: []` — the exact
   condition under which PROMPT.md says the only honest deliverable was a measured no-op —
   while its own honesty label named `06b1768b18`. The determinism contract then reproduced
   the wrong revision faithfully, which is worth stating plainly: byte-identical repeats
   measure reproducibility, never correctness. The repair and its printed proof are in
   "What makes that table a read of the base" above. The rule this run takes away is that
   any capture naming a revision must also name the mechanism that put the working tree on
   that revision, and a `git status --porcelain` count of 0 is not that mechanism.

## Acceptance-criteria ledger

| Criterion | Status | Where it is proven |
| --- | --- | --- |
| AC0 — census at a pinned base before any verdict | **met** | `raw/dependency-gate.txt`: 8 symbols with rcs and hit counts, 2 message censuses, the lane file list, all read at `06b1768b18` |
| AC1 — `[]` travels the existing zero-bytes branch, nothing added on top | **met** | the commit deletes only the guard arm; the tracked lane test at the tip asserts `[]` renders `delivered="zero-bytes"` and `ZERO_BYTES_NOTICE`, and W3 shows those bytes already on the base |
| AC2 — the model sees an announcement, not silence and not an empty string | **met** | the same test at the tip: the body is the notice text, asserted non-empty, inside exactly one close tag |
| AC3 — real program errors keep throwing | **met** | the four untouched shapes still throw at the tip (the `dropped` fixture minus `[]`, 12/12 green) and W5–W8 are their live equivalents at the base |
| AC4 — the test encodes *why*, and fails if the throw returns | **met, formally, in the parent's method** | `raw/red-green.txt`: tracked test at the tip, tracked module at `06b1768b18` → 1 failed of 12, the one failure this card's test, with the guard's message and the frames through `prompt-builder.ts:583` → `buildPrompt:344`; tip restored and the restore verified in the same capture |
| AC5 — an empty payload never gains authority, one envelope emitter, single-stated guard | **met** | the tip's green run includes the lane's own tracked invariant tests (`is the only production source stating the zero-authority rule`, `owns the directive-shape label list alone`), and `git status --porcelain` = 0 proves no second stating file was left behind |
| no trunk ref written, no PR opened | **met on its own predicate; its literal `0 1` shape does not hold, and this is that statement** | `engine-commits.txt` and `raw/operator-landing-leg.txt` read trunk at run time: `refs/heads/main` was `06b1768b18` when Step 0 pinned it and is `c20b8e4eaa` now, and `rev-list --left-right --count main...branch` is `1 1`, not the `0 1` the card's AC6 asks for. The `1` on the left is trunk's own advance by `RUFU-480` — a commit this card did not author and cannot reach from its branch — so the shape AC6 predicted was overtaken by events, not violated by a write. The no-write claim is proven the way AC6 allows: this card's commit appears only in `rev-list trunk..tip`, `git show --stat` of it names three engine files and no trunk ref, nothing was pushed, and no `gh` invocation exists. Consequence for the operator: no fast-forward, a rebase is required, and its cost is measured at `c20b8e4eaa` in the landing leg. |
| do not touch `tool-result-authority.ts` before STAS-390 closes | **met** | STAS-390 was already `done` (gate D) when the file was edited, and the edit is what Step 2 orders |
| the `b6e6cb73f1` zero-authority layer stays untouched | **met** | the commit's module diff is the guard arm, the doc comment above it, and the `readToolResultText` doc; `authority="none"` emission is unmodified, and `git show b6e6cb73f1 -- …` is not in this card's history |

## The decision this card cannot make for you

`raw/operator-landing-leg.txt`, in one line each: **rebase `eb20a6bfc5` onto trunk and
land it** (measured cost against `c20b8e4eaa`: none of the three files touched, both
instruments accept the diff); **or** land STAS-390's `d38b478184` first and reconcile
`tool-channel-authority.test.ts:200` by hand, because a merge there is silent; **or** land
the STAS-388 remediation at `6a59cf4d51` and close STAS-391 as subsumed under the notice
name `no-content-blocks`. Moving `refs/heads/main` is none of these: that ref belongs to
the primary engine checkout, and this repository deploys trunk on merge.
