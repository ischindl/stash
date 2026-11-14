# Daily upstream absorb

The procedure the **Upstream Absorber** scheduler agent runs on each heartbeat. It is the
visible half of the anti-fork gate: the rule that our trunk must always carry upstream's
trunk is enforced by `.githooks/reference-transaction.sh`, and this job is what keeps that
rule true instead of merely enforced.

Why a daily cadence and not a weekly one: upstream moves about two commits a day, and the
last time this was left alone the gap ran six weeks and reached 28 conflicted paths — measured
2026-11-12 by the job's own refusal list. STAS-427 resolved all 28 on a lane of its own the same day,
and that lane landed: the trunk's own reflog names it
(`72998ad18 refs/heads/main@{2026-11-13 15:11:39 +0100}: fusion landing: STAS-427`). The predicate was
FALSE again inside twelve hours, because the debt is re-opened by upstream's next commit rather than
by an unlanded lane — `1eca3516c` and `419efb459` cost a fresh lane (STAS-439, 12 conflicted paths)
two days later. Our migration tail had to be renumbered as damage control while it grew (STAS-371,
STAS-374).

## The run

From the repository root:

```
python -m backend.scripts.upstream_gate absorb
```

That one command fetches `origin main`, merges it into `refs/heads/main` inside a scratch
detached worktree, re-runs the predicate and the alembic booking guard there, refreshes the
upstream revision-id bookings, and publishes with a compare-and-swap. It never touches a
checkout it does not own.

## Report what it printed

The verdict line is machine-shaped; quote it rather than paraphrasing it:

```
TRUE  refs/heads/main origin_main=<sha> refs/heads/main=<sha> merge_base=<sha> ahead/behind=<n>/<n>
```

Record, in the board run log and in one `task_log` line on the task named at the bottom of
this file:

- the exit code,
- `origin_main` and the resulting `refs/heads/main` sha,
- for a no-op ("already anchored") say so in those words,
- for a refusal, every conflicted path it printed.

If the job says the trunk is checked out somewhere with uncommitted changes, report that and
stop. It is not your index to clean.

## What this job never does

- It does not resolve a conflict or pick a side in one. A conflict is a lane of its own with a
  reviewer; the refusal message says so. Side-picking is the revert class recorded in project
  memory.
- It does not `reset`, `switch -C`, `checkout -f`, `clean`, or `stash` anything, in any tree.
- It does not push, open a pull request, or install git hooks. Hook activation is one
  deliberate operator command (`python backend/scripts/install_hooks.py`) taken after the
  trunk passes the predicate, because installing it while the trunk fails stops every merge on
  this machine.
- It does not edit `core.hooksPath` or any file under `.git/`.

## While upstream is unabsorbed

The trunk still carried this state when STAS-427 finished its lane work on 2026-11-13, and the
state returns the moment upstream moves ahead and a day is skipped. In it, this job exits 1 every
day and prints its conflict list — 28 paths at that measurement, all 28 resolved on the STAS-427
lane, every side recorded per path in the STAS-427 task document `merge-conflict-ledger`. Upstream
moved twice more while that lane was under review and both moves merged into it with zero
conflicts, and upstream then moved twice again — `1eca3516c` removed the evals product and the
release bump `419efb459` followed it — which re-opened the debt against that lane and took a fresh
lane to clear: STAS-439 resolved 12 conflicted paths (9 content, 3 modify/delete) against
`419efb459`, every side recorded per path in the STAS-439 task document `merge-conflict-ledger`.
So a lane, never the trunk, is where the debt is cleared: the trunk write is held up because a
checkout holds `refs/heads/main`, which makes it an operator leg (`AGENTS.md`, *Landing hygiene*),
not because anything is unresolved. The lane waited long enough for the trunk
to move under it: `refs/heads/main` was fast-forwarded to `10e11a922` (STAS-431) at 11:00Z on
2026-11-13 — a lane cut from the still-unanchored trunk, so the trunk's merge base with upstream
did not move and the debt grew by one more commit beside it — and the STAS-427 lane re-merged that
trunk commit conflict-free instead of rebasing. Nothing refused that trunk write, because the hook
below is not installed: with the gate inert, the predicate only speaks when someone runs it. That refusal is the job working, not a broken job: the trunk
is refused rather than quietly forked. Clearing the debt is always a lane of its own with a
reviewer, never a side-pick here (`STAS-373` named the predicate; `STAS-427` and `STAS-439` are the lanes that
cleared it). Pause this agent only on the operator's order.

That lane has to be creatable, which is why the installed hook does not refuse lane creation
while the trunk itself is unanchored. `reference-transaction` refuses every write that would move `refs/heads/main`, and for
a new `refs/heads/fusion/*` branch it refuses only when the trunk already carries `origin/main`
while the new base does not; while the trunk itself is behind, it prints the verdict on stderr and
lets the lane exist. A hook refusal has no `--no-verify`, so refusing the unabsorbed case would
have locked the repair out of the machine that needed it, and it would have prevented no fork —
the trunk cannot advance to an unanchored commit either way.

One consequence of there being no bypass: while the trunk is unanchored the hook stops writes to
`refs/heads/main` in both directions, so an operator's undo of a bad landing — pointing the trunk
back at the commit it held — is refused too, because that commit is unanchored as well. The way
through an undo is `python backend/scripts/install_hooks.py --uninstall`, the ref move, then
install again. Installing is itself refused while the trunk is behind (`--force` overrides), so on
a machine carrying the debt the order is absorb, then install.
