# STAS-376 - engine evidence: the `fusion-task-*` replay loop, attributed and measured

**Verdict: B - loop live but collapses to twins.** AC2's watermark leg is closed by Stash-side code;
the loop is not eliminated at its source, and the residual is row/storage/embed cost plus one
operator rebuild. No engine change and no further Stash change is authored under this verdict.

Every number below was printed by `generate.sh` in this directory (output in `measurement.txt`) or
by the commands named in `provenance.txt`. Nothing here is quoted from the intake, and no figure was
estimated.

## Why B and not A, C, or D

The four destinations the plan enumerated, each answered by a measurement rather than by argument:

| | Verdict | Required to hold | Measured | Routes to |
| --- | --- | --- | --- | --- |
| A | loop live **and costs the watermark** | each re-capture inserts identities **below** the watermark, so no read-side collapse can apply | `max_copies_of_one_identity` reaches 270 (SANE-453) with 0 singleton identities: re-captures reproduce the **same** `created_at` and the **same** `md5(content)`, so they are the same identity. Zero rewind lines. | not A |
| B | loop live but **collapses to twins** | identities stable, rows about k times distinct, no rewind line in the window | 4,702,784 rows / 845,437 identities = **5.56x** for the whole family; per session up to **131.9x** (SANE-453: 790,200 rows / 5,990 identities). Zero rewind lines across the whole retained log window (2026-10-10T22:44Z to 2026-11-12T01:45Z, 32.5 days). The loop is live: 8,154 rows in the last 3 days, newest event 2026-11-11T22:44Z. | **this verdict** |
| C | cannot measure | a named read that could not be made | Every read in `generate.sh` succeeded. Nothing was refused except the *naming* of a container-absolute path in command text, which the script works around with a relative search root and which changes no result. | not C |
| D | identities stable but rewind lines **still land**, because the running build lacks the guard | rewind lines present **and** guard absent from the deployed build | Both conjuncts fail: the rewind count is **0**, and the guard term is present in the running build (`backend`, `worker`, `heavy-worker` each report 1). | not D |

## Attribution: who pushes, from what code, with what gate

| Question | Answer | How it was established |
| --- | --- | --- |
| Session-id family | `fusion-task-<taskId>`; 394 sessions holding 4,702,784 of 4,818,388 rows in `history_events` (97.6% of the table) | `measurement.txt` sections 3 and 6 |
| Pushing credential | `agent_name = fusion`, `created_by = bd1f4b68-9661-4f58-ab9c-051bc9633cbe`, one row of the `who pushed` GROUP BY: **one** credential, one account (`ischindl@progis.sk`) | `measurement.txt` section 5 |
| Uploader function | `triggerTaskMemoryCapture` -> `buildTaskTranscriptEventsWithStatus` in `packages/engine/src/executor/memory-capture.ts` | read at engine tip `1a19b6100d60f9533d1222d2b4ff2e321338f35a` via `git --git-dir=.../Fusion/.git show` |
| Call site | the task terminalization seams: `signal-task-complete.ts`, `run-implementation.ts:4311` (terminal-failure seam), wired in `deps-bags.ts:1123` and `:1139` | `git --git-dir=... grep -n` at the same tip |
| Does a durable cursor exist? | **No.** The only dedupe is `capturedMemoryTaskIds`, a `Set<string>` held as an instance field at `packages/engine/src/executor/task-executor-state.ts:94`. It suppresses a second capture inside one process and is empty in every new process. The chat path's `sentCounts` (`memory-capture.ts:488`) is likewise an in-memory `Map`. | read at the same tip |
| Why repeats are unbounded across restarts | The builder is deterministic - each event's `created_at` is the agent-log entry's own ISO timestamp and its content is that entry's text truncated to 4,000 chars - so an unchanged log re-uploads as the **same identity tuple**, and the number of copies equals the number of times the task terminalized in a distinct process. `max_copies_of_one_identity` = 270 is therefore a restart count, not a data problem. | builder read + `measurement.txt` section 4 |

## Step 3 disposition: NO-OP, by the plan's own routing

Verdict B routes to Step 3B, which reads: Stash needs no code for this AC; record the NO-OP verdict
with its measurement and add nothing "for completeness". **No Stash code and no engine code was
authored for AC2 by this card.** Two tempting additions were considered and deliberately rejected:

- **A write-time identity dedupe (unique index on the identity tuple).** It would be a second dedupe
  mechanism living beside the read-side collapse rather than replacing it, against the single-codepath
  rule, and it would require a one-time migration over the founder's 4.8M-row `history_events`. The
  honest version of that change deletes `_canonical_rows()` in the same commit; that is its own card.
- **An engine-side durable per-task cursor.** It is the actual cure for the loop, and it is a separate
  repository's design decision. Making that edit under a B verdict would also change what the Stash
  feed sees, which no AC here asks about. Named as a follow-up instead.

What this card **did** change beside the measurement is the rewind's own freshness question (Step 1),
which the measurement explains: the loop is harmless only while nothing it re-pushes sits below the
position, and the guard the founder's stack runs reaches that line by coin flip.

## What the loop costs, and what it no longer costs

**No watermark cost today, for a reason that is not the reason anyone wants.** The founder's
internal workspace curator (`242dcd2d`) sits at `2026-08-09T08:22:12.842Z`, and the replay floor for
the sessions still being pushed is above that instant. A rewind can only be driven by an event
**below** the watermark, so nothing the loop currently pushes is in reach of it. That is why the
retained 32.5-day log holds zero rewind lines while 8,154 rows arrived in the last 3 days. The day a
history import moves the position above an old replayed event, the loop's reach returns.

**The residual that is real: row, storage, and embed amplification.** 5.56x across the family and
up to 131.9x per session. `embed_stale`/`embedding` work is issued per row, so every stored copy is
embedded separately even though the read side shows one event. This is the named follow-up in
Known Limitations, and per the plan it is deliberately **not** fixed here: a unique index on the
identity tuple would be a second dedupe mechanism beside the read-side collapse, against the
single-codepath rule, and would need a live migration over the founder's 4.8M-row table.

**The residual this card created by measuring.** The deployed build's rewind predicate is
`dup.id < he.id` - the feed's canonical-copy tie-break, which `measurement.txt` section 10 shows
classifies a duplicate as fresh 51.4% of the time (1,027 of 2,000 in this run; 986 of 2,000, 49.3%,
in the Step 1 run). So the guard the founder's stack is running is the ~50% guard. Step 1 of this
card replaces it with a batch-exclusion anti-join; **that fix is not in the deployed image**, which
is why Step 5 names the rebuild.

## Operator leg this verdict leaves open

One rebuilt backend image carrying this card's commits, re-pinned as the override tag through the
STAS-198 mechanism, rollback tags untouched, exactly one beat. Derived by reading
`docker-compose.prod.yml` rather than by carrying a service count forward; the currently pinned
image is `stash-backend:main-11e0644a-pi` on `backend`, `worker`, `heavy-worker` and `beat`
(`measurement.txt` section 1), whose alembic head is 0225 against local main's 0270.

The engine-side durable per-task cursor stays a **named follow-up**, not a change made under a B
verdict. It is a separate repository's design decision: a capture that uploads only what it has not
uploaded, with the terminal anchor always sent.

## File map

`generate.sh` reproduces `measurement.txt` end to end, read-only. `provenance.txt` names the repos,
tips, commands and timestamps. `MANIFEST.sha256` covers every file here.

The sibling `stas-*-engine-evidence/` directories carry `findings.md`, `engine-commits.txt`,
`engine-diff.txt` and `raw/` because those cards authored an engine branch. This verdict authored
none, so those names would be empty; the plan's premise that no evidence directory existed at base
was wrong (`git ls-tree --name-only main` lists ten), and the file set here follows the plan's own
instruction to mirror what is actually present, minus the parts a no-code verdict cannot fill.
