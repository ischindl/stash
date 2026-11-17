# Guard test: red at the guard commit, green at the tip

Test file: `packages/engine/src/__tests__/reliability-interactions/worktree-release-decides-from-occupancy.real-git.test.ts`
(8 cases: real `git worktree` + real `git status`, driven through the public `removeWorktree` with a defensive
reason). The guard commit is the direct parent of the commit that changes the gate.

| ref | commit | result |
| --- | --- | --- |
| guard commit (base src + this file, tree `e1e1fe94c9`) | `b90f5bbce1` | **3 failed / 5 passed** |
| tip of this lane | `fb9c89f35e` | **8 passed** |

The guard commit and `15fe7bee63` carry the *same tree hash* `e1e1fe94c9a315e1c1fbcb9ca0b257888e01f494` - the
history was rebuilt afterwards to make the guard the parent of the fix - so the run printed in
`raw/guard-test-red-at-guard-commit.log` is that commit's own tree, proven by hash and not by inference. The
red run was executed by checking that commit out in place, so `node_modules` and every harness input were the
same ones the green run used.

Raw logs: `raw/guard-test-red-at-guard-commit.log`, `raw/guard-test-green-at-tip.log` (+ `.cases.txt`).

## An earlier draft measured 4 failed / 4 passed, and why that number is not the one above

`raw/guard-test-red-at-base.log` (kept, not deleted) is a run of a *superseded draft* of this file, taken
against the base src while the delivery history was still detached. Its fourth red was the refusal-naming case
asserting a `lane` key on the `worktree:removal-preserved` row. The implementation this card landed does not
add that key: the surviving refusal names the lane through `reason` (which the base row already carried) and
the path through `target`, and `lane` is the key of the NEW discard row `worktree:removal-discarded-ignored-content`.
The assertion was retargeted to the keys the contract actually has, which moves that case from red to green at
base and makes the count 3 / 5. The naming requirement is not dropped - see item 2 below for where it lives.

## The three that flip

Each is a claim about the *new* contract that the old gate cannot satisfy, so the row is red before the
code changes and green after - not a tautology in either direction. All three die at the base with
`WorktreeContentPreservationError: preserving ...: uncommitted or ignored content present`, i.e. a refusal
where a release is owed.

1. `releases a lane holding only ignored build output the retired allowlist never named` - a uv-shaped tree
   (`.venv/` plus `stashai.egg-info/`). This is the wedge from the field, and the discard row it now writes is
   what carries the lane naming: `metadata.lane` = the removal reason, `target` = the checkout path, plus
   `entryCount` and `source`.
2. `releases a lane whose ignored path git quotes, because quoting is not occupancy` - a non-ASCII ignored
   filename arrives from porcelain quoted/octal-escaped; the base fails closed on quoted paths by design, and
   the row pins that quoting no longer carries any verdict.
3. `discards a lane's ignored local credential file, because the ignore rule is the declaration` - the honest
   cost of the change, pinned rather than hidden: an ignored `.env` *inside a lane* now dies with it.

The `names the lane and the path on the record a refusal leaves behind` case is green on both sides and stays
in the file for that reason: it is the guard that the refusal keeps writing `target` (checkout path),
`metadata.reason` (the lane) and the content class. What the base did *not* do, and what no single-case
measurement can show, is that the self-healing `tip-already-merged` call site - the one behind the 6004-line
production loop - passed **no auditor at all**, so for the loop that actually happened in the field not even
that partial row was written. The fix passes one and widens its type to the half removal calls, and the
warning line now reads `tip-already-merged cleanup failed (lane <reason>, checkout <path>)`.

## The five that must stay green (they guard the refusals the fix must not swallow)

- refuses on an untracked file, a tracked modification, a staged entry - occupancy still refuses;
- refuses the project root checkout before occupancy can authorize anything - the guard that keeps the
  `.env` cost above from ever touching the real root;
- and the refusal-naming row described above.

These passed at the guard commit and pass at the tip, which is what makes the red above evidence about the
gate rather than about a broken harness.
