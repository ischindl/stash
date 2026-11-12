"""Fail unless the checked ref is built on top of upstream's trunk.

This repository is a fork. Every commit upstream lands is a commit our trunk does
not have until somebody absorbs it, and for six weeks nobody did: the debt grew
to 248 unabsorbed upstream commits and 594 conflicting files, which is what a
fork *is*. The absorption then had to be done by hand (STAS-374, 118 conflicts)
and the alembic tail had to be renumbered as damage control (STAS-371), because a
revision id upstream already used had been booked here too. Discipline did not
hold, so the invariant became a predicate:

    git merge-base --is-ancestor <origin/main> <ref>     # TRUE = anchored

`check` answers that question for one or more refs after fetching, and a FALSE
exit is what the git hooks under `.githooks/` act on — a refused `update-ref` on
`refs/heads/main`, not advice. `absorb` does the daily fetch-and-merge that keeps
the answer TRUE, and `bookings` keeps the alembic booking guard in
`backend/migrations/check_heads.py` able to see the revision ids upstream holds.

The upstream sha is always resolved from `refs/remotes/origin/main` after a fetch
this process ran. `.git/FETCH_HEAD` is deliberately never read: whichever fetch
last touched the repository wrote it, so on a box where several agents fetch
different refs it can name a branch days older than the question asked.

Run from the repo root:
  `python -m backend.scripts.upstream_gate check [ref ...]`
  `python -m backend.scripts.upstream_gate absorb [--into fusion/<task>]`
  `python -m backend.scripts.upstream_gate bookings [--live]`

Needs nothing but Python and git — no database, no backend settings, and no
network except `git fetch`, which is why a git hook is allowed to call it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM_REMOTE = "origin"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = f"refs/remotes/{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"
TRUNK_REF = "refs/heads/main"
LANE_PREFIX = "refs/heads/fusion/"
DEFAULT_REFS = ("main", "HEAD")

FIX = (
    "Fix: absorb upstream into the trunk before anything else lands — "
    "`python -m backend.scripts.upstream_gate absorb`. Conflicts are resolved in a "
    "lane of their own with a reviewer; this gate never picks a side."
)

VERSIONS_DIR = Path("backend/migrations/versions")
MANIFEST = Path("backend/migrations/shipped_revisions.json")

# Published only what these accept. pytest deliberately is not among them: this runs
# unattended, and backend/tests/conftest.py defaults to a shared database it truncates.
#
# Both ruff gates are spelled `python -m ruff` rather than `ruff`. This job is started by a
# scheduler agent's heartbeat, whose PATH need not contain the console script, and
# `subprocess.run` raises `FileNotFoundError` for a missing program — a traceback in the middle
# of an unattended merge instead of the named refusal the operator is owed. Named after the
# interpreter that is running this file, the gate is the same ruff the graph guard runs under,
# or it is reported missing before any work starts.
PREPUBLISH_CHECKS: tuple[tuple[str, ...], ...] = (
    (sys.executable, "backend/migrations/check_heads.py"),
    (sys.executable, "-m", "ruff", "check", "backend/", "cli/"),
    (sys.executable, "-m", "ruff", "format", "--check", "backend/", "cli/"),
)
RUFF_PROBE = (sys.executable, "-m", "ruff", "--version")


class GateError(Exception):
    """A named failure the caller must exit 1 on rather than substitute a value for."""


def run_git(*args: str, cwd: Path | None = None) -> str:
    """Run one git command and return stdout, or raise naming the command and its stderr.

    Every git read in this module goes through here so exactly one place decides what
    a non-zero exit means; no caller can read one as an empty answer.
    """
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        shown = " ".join(args)
        raise GateError(f"`git {shown}` exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def resolve(ref: str, cwd: Path | None = None) -> str:
    """The commit a ref names, or a named error — never a default."""
    return run_git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd).strip()


def merge_base(one: str, other: str) -> str:
    return run_git("merge-base", one, other).strip()


def is_ancestor(ancestor: str, descendant: str) -> bool:
    """The predicate. Only git's own 0 (yes) and 1 (no) are answers; anything else raises."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise GateError(
            f"`git merge-base --is-ancestor {ancestor} {descendant}` exited "
            f"{result.returncode} instead of answering: {result.stderr.strip()}"
        )
    return result.returncode == 0


def ahead_behind(upstream: str, ref: str) -> tuple[int, int]:
    """(commits the ref has that upstream lacks, commits upstream has that the ref lacks)."""
    behind, ahead = run_git("rev-list", "--left-right", "--count", f"{upstream}...{ref}").split()
    return int(ahead), int(behind)


def verdict_line(ref: str, upstream: str) -> tuple[bool, str]:
    """The single line `check` prints for one ref, and whether that ref passed."""
    sha = resolve(ref)
    anchored = is_ancestor(upstream, sha)
    base = merge_base(upstream, sha)
    ahead, behind = ahead_behind(upstream, sha)
    verdict = "TRUE" if anchored else "FALSE"
    return anchored, (
        f"{verdict} {ref} origin_main={upstream} {ref}={sha} merge_base={base} "
        f"ahead/behind={ahead}/{behind}"
    )


def check_refs(refs: list[str], *, fetch: bool) -> int:
    """Print the predicate for every ref; exit 0 only when all of them pass.

    `fetch=False` exists for the git hooks, which must never touch the network. Every
    other caller fetches first, so the answer describes today.
    """
    if fetch:
        fetch_upstream()
    upstream = resolve(UPSTREAM_REF)
    failed = False
    for ref in refs:
        anchored, line = verdict_line(ref, upstream)
        print(line)
        failed = failed or not anchored
    if failed:
        print(FIX, file=sys.stderr)
    return 1 if failed else 0


def fetch_upstream() -> None:
    """Fetch the upstream trunk ref, failing loud when the network or the ref fails."""
    run_git("fetch", UPSTREAM_REMOTE, UPSTREAM_BRANCH)


# -------------------------------------------------------------------------- absorb


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run one gate command in a tree, returning its exit code and combined output."""
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def require_prepublish_tools() -> None:
    """Refuse to start an absorb whose gates cannot be run, and name the missing one.

    Checked before any worktree exists, so the answer is a named refusal and not a
    FileNotFoundError halfway through a merge. A gate that is red is a different answer, and it
    comes from the gates themselves after the merge; this one only says the tool is not here.
    """
    code, output = _run(list(RUFF_PROBE), Path.cwd())
    if code != 0:
        probe = " ".join(RUFF_PROBE)
        raise GateError(
            f"absorb cannot run its pre-publish gates: `{probe}` exited {code}: {output}\n"
            "Install the dev requirements (`pip install ruff`) or run absorb from an "
            "interpreter that has them. Nothing was fetched and nothing was published."
        )


def _merge_in(worktree: Path, source: str) -> tuple[int, str]:
    """Merge one ref into whatever the scratch worktree has checked out."""
    result = subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", source],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _conflicted_paths(worktree: Path) -> list[str]:
    return [
        line
        for line in run_git("diff", "--name-only", "--diff-filter=U", cwd=worktree).splitlines()
        if line
    ]


def _make_scratch_worktree(base_ref: str) -> Path:
    """A detached worktree at `base_ref`, in a directory nobody else owns.

    Never `reset`, `switch -C`, `checkout -f`, `clean` or `stash` in a checkout this
    job does not own: the primary checkout on this box is shared and detached, and
    `refs/heads/main` is usually held by a worktree whose index must not be surprised.
    """
    parent = Path(tempfile.mkdtemp(prefix="upstream-absorb-"))
    worktree = parent / "wt"
    run_git("worktree", "add", "--detach", str(worktree), base_ref)
    return worktree


def _remove_scratch_worktree(worktree: Path) -> None:
    run_git("worktree", "remove", "--force", str(worktree))
    parent = worktree.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def _commit(worktree: Path, message: str) -> str:
    run_git("add", str(MANIFEST), cwd=worktree)
    run_git("commit", "-m", message, cwd=worktree)
    return resolve("HEAD", cwd=worktree)


def _publish(ref: str, old_tip: str, new_tip: str, why: str) -> None:
    """Move one branch by compare-and-swap, so a concurrent advance is never overwritten.

    Also the only place that decides whether a branch may move at all: it refuses while a
    checkout holds it with tracked edits in the index, because `update-ref` cannot tell git
    that the worktree it belongs to is now describing a different commit.
    """
    holder = _holder_of(ref)
    if holder is not None:
        dirty = run_git("status", "--porcelain", "--untracked-files=no", cwd=holder).strip()
        if dirty:
            raise GateError(
                f"refused: {ref} is checked out at {holder} with uncommitted changes to "
                f"{len(dirty.splitlines())} tracked file(s). Commit or set those aside in that "
                "checkout first: moving a checked-out branch out from under it would leave its "
                "index describing one commit while HEAD names another, and this job never "
                "stashes, resets or overwrites work in a checkout it does not own."
            )
    try:
        run_git("update-ref", ref, new_tip, old_tip)
    except GateError as error:
        raise GateError(
            f"refused: {ref} moved while this absorb was running, so the result was discarded "
            f"instead of forced over whoever advanced it. Re-run and it starts from the new tip. "
            f"({error})"
        ) from error
    print(f"absorb: advanced {ref} {old_tip[:12]} -> {new_tip[:12]} ({why})")


def _holder_of(ref: str) -> Path | None:
    """The checkout that has `ref` checked out, or None when no worktree holds it."""
    wanted = f"branch {ref}"
    for block in run_git("worktree", "list", "--porcelain").split("\n\n"):
        lines = block.splitlines()
        if wanted in lines:
            return Path(lines[0].removeprefix("worktree ").strip())
    return None


def _trunk_holder() -> str:
    """Which checkout holds `main` and is therefore left with an index behind the ref."""
    holder = _holder_of(TRUNK_REF)
    return str(holder) if holder else "none (no checkout has main checked out)"


def _stage_bookings(worktree: Path) -> str:
    """Refresh the `upstream` key inside `worktree`, describing the change, or return ''.

    The manifest is written here and never by hand, so its upstream ids are always the
    ids a fetch produced.
    """
    upstream = upstream_revision_ids()
    manifest_path = worktree / MANIFEST
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("upstream") == upstream:
        return ""
    manifest["upstream"] = upstream
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=True) + "\n")
    return f"booked {len(upstream)} upstream revision ids, highest {max(upstream)}"


def absorb_command(into: str | None = None) -> int:
    """Fetch upstream and land it on a branch, or change nothing and name why.

    With no target the branch is the trunk: the daily job's write, and the one the
    predicate is defined on. With `--into fusion/<task>` the branch is a lane, and the lane
    absorbs the trunk as well as upstream — the move that unblocks a lane stranded behind a
    trunk change without deleting the work on it, which is the alternative a reviewer found
    expensive enough to be a defect.

    What runs before the compare-and-swap publish is exactly `PREPUBLISH_CHECKS`:
    `check_heads.py` and both ruff gates, inside the merged scratch tree. What
    deliberately does not run is pytest: this job is unattended and the test suite
    defaults to a database it would truncate, so a merge that is clean but
    semantically broken is a gap this job states instead of pretending to close.
    """
    target = TRUNK_REF if into is None else _lane_ref(into)
    require_prepublish_tools()
    # A lane is a child of the trunk, so it takes both: upstream first, so the trunk merge
    # cannot resurrect a conflict upstream already settled.
    sources = (UPSTREAM_REF,) if target == TRUNK_REF else (UPSTREAM_REF, TRUNK_REF)

    fetch_upstream()
    upstream = resolve(UPSTREAM_REF)
    old_tip = resolve(target)

    if target == TRUNK_REF and is_ancestor(upstream, old_tip):
        print(f"absorb: already anchored, origin_main={upstream[:12]} {TRUNK_REF}={old_tip[:12]}")
        return _advance_bookings_only(old_tip)

    worktree = _make_scratch_worktree(target)
    try:
        for source in sources:
            code, merge_output = _merge_in(worktree, source)
            if code != 0:
                conflicts = _conflicted_paths(worktree)
                run_git("merge", "--abort", cwd=worktree)
                print(
                    f"absorb: refused, `git merge {source}` into {target} did not complete.\n"
                    f"{merge_output}",
                    file=sys.stderr,
                )
                print("conflicting paths:", file=sys.stderr)
                for path in conflicts:
                    print(f"  {path}", file=sys.stderr)
                print(FIX, file=sys.stderr)
                return 1

        new_tip = resolve("HEAD", cwd=worktree)
        for check in PREPUBLISH_CHECKS:
            check_code, output = _run(list(check), worktree)
            if check_code != 0:
                shown = " ".join(check)
                print(
                    f"absorb: refused, `{shown}` exited {check_code} in the merged tree at "
                    f"{worktree}. Nothing was published; {target} still names {old_tip[:12]}.",
                    file=sys.stderr,
                )
                print(output, file=sys.stderr)
                return 1

        bookings_note = _stage_bookings(worktree)
        if bookings_note:
            new_tip = _commit(
                worktree, f"chore(upstream-gate): book upstream ids at {upstream[:12]}"
            )

        if not is_ancestor(upstream, new_tip):
            print(
                f"absorb: refused, the merged {target} still fails the predicate it exists to "
                f"satisfy: {UPSTREAM_REF} at {upstream[:12]} is not an ancestor of {new_tip[:12]}.",
                file=sys.stderr,
            )
            return 1

        _publish(target, old_tip, new_tip, f"merged {' + '.join(sources)} at {upstream[:12]}")
        if bookings_note:
            print(f"absorb: {bookings_note}")
        print(f"absorb: {target} is held by {_trunk_holder()}; that index is now behind the ref.")
        return 0
    finally:
        _remove_scratch_worktree(worktree)


def _lane_ref(branch: str) -> str:
    """The full ref for a lane branch, refusing anything that is not an engine lane.

    The trunk is guarded by the reference-transaction hook; the lane namespace is guarded
    by post-checkout. Absorbing into a branch outside `refs/heads/fusion/` would be a merge
    this card enforces nothing about, so it is not this command's job.
    """
    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    if not ref.startswith(LANE_PREFIX):
        raise GateError(
            f"{branch} is not an engine lane: absorb --into takes a fusion/<task> branch "
            f"(under {LANE_PREFIX}), the only branches this card's lane rule enforces."
        )
    resolve(ref)
    return ref


def _advance_bookings_only(old_tip: str) -> int:
    """Publish a bookings refresh when the trunk is anchored but the manifest drifted.

    A stale `upstream` key is the STAS-371 incident waiting to happen — the booking
    guard stops covering ids upstream is already using. The repair is a manifest
    commit, never a renumbering, which belongs to the renumber lane.
    """
    worktree = _make_scratch_worktree(TRUNK_REF)
    try:
        note = _stage_bookings(worktree)
        if not note:
            print(f"absorb: bookings current in {MANIFEST}")
            return 0
        new_tip = _commit(worktree, f"chore(upstream-gate): refresh upstream bookings ({note})")
        _publish(TRUNK_REF, old_tip, new_tip, note)
        print(f"absorb: {note}")
        return 0
    finally:
        _remove_scratch_worktree(worktree)


# ----------------------------------------------------------------------- bookings


def upstream_revision_ids(ref: str = UPSTREAM_REF) -> dict[str, str]:
    """Every revision id upstream's trunk carries, keyed by upstream's own slug.

    Read from the ref's tree and never from the working tree: the question is what
    upstream booked, and the checked-out directory answers a different question. The
    listing needs `-r`, or `ls-tree` names the directory instead of its files.
    """
    listing = run_git("ls-tree", "-r", "--name-only", ref, "--", str(VERSIONS_DIR))
    revisions: dict[str, str] = {}
    for name in listing.splitlines():
        file_name = name.rsplit("/", 1)[-1]
        if not file_name.endswith(".py") or file_name == "__init__.py" or "_" not in file_name[:-3]:
            continue
        revision, slug = file_name[:-3].split("_", 1)
        revisions[revision] = slug
    if not revisions:
        # Every caller of this books ids against the ceiling, so an empty answer is not a
        # harmless one: it would say upstream has booked nothing, which has never been true of
        # this project's trunk and means the read did not look at what was asked.
        raise GateError(
            f"{ref} lists no revision files under {VERSIONS_DIR}, so upstream's booked revision "
            "ids cannot be answered. Check that the ref is fetched and that the migrations "
            "directory is still there; this command will not treat an empty listing as 'nothing "
            "booked'."
        )
    return revisions


def local_revision_ids() -> dict[str, str]:
    """Every revision id this working tree ships, keyed by its own slug."""
    revisions: dict[str, str] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if "_" not in path.stem:
            continue
        revision, slug = path.stem.split("_", 1)
        revisions[revision] = slug
    return revisions


def booking_collisions(upstream: dict[str, str], local: dict[str, str]) -> list[str]:
    """Ids upstream books that this tree re-uses for a different migration.

    This is the STAS-371 class: our `0257_session_folders_share_wiki` and upstream's
    `0257_something_else` merge cleanly in git and leave every database stamped
    `0257` skipping one of the two forever.
    """
    return [
        f'upstream books revision id {revision} as "{slug}"; this tree has "{local[revision]}" '
        f"under the same id, so one of the two must move upstream's number. Renumbering is "
        f"the renumber lane's job, not this command's.\n{FIX}"
        for revision, slug in sorted(upstream.items())
        if revision in local and local[revision] != slug
    ]


def _staleness(booked: dict[str, str], upstream: dict[str, str]) -> str:
    missing = sorted(set(upstream) - set(booked))
    extra = sorted(set(booked) - set(upstream))
    changed = sorted(r for r in set(booked) & set(upstream) if booked[r] != upstream[r])
    return ", ".join(
        [f"+{r}" for r in missing] + [f"-{r}" for r in extra] + [f"~{r}" for r in changed]
    )


def read_manifest() -> dict:
    if not MANIFEST.exists():
        raise GateError(
            f"{MANIFEST} is missing; the booking guard cannot answer anything without it"
        )
    try:
        return json.loads(MANIFEST.read_text())
    except ValueError as error:
        raise GateError(f"{MANIFEST} is not readable JSON ({error})") from error


def bookings_command(*, live: bool) -> int:
    """Say whether the tracked `upstream` booking set matches upstream right now.

    Exit 0 means the manifest describes upstream; exit 1 means the map is missing,
    stale, or a collision is open, and either way the reason is printed, never
    swallowed. `--live` is the only writer of that map, so a manifest that never had
    one is repaired the same way a stale one is.
    """
    if live:
        fetch_upstream()
    upstream = upstream_revision_ids()
    errors = booking_collisions(upstream, local_revision_ids())
    manifest = read_manifest()
    booked = manifest.get("upstream")

    drift = ""
    if not isinstance(booked, dict):
        drift = (
            'carries no "upstream" map, so the guard cannot know which ids upstream is '
            "already holding"
        )
    elif booked != upstream:
        at = resolve(UPSTREAM_REF)[:12]
        drift = f'"upstream" is stale versus {UPSTREAM_REF} at {at}: {_staleness(booked, upstream)}'

    if drift:
        if live:
            manifest["upstream"] = upstream
            MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=True) + "\n")
            print(
                f'bookings: wrote "upstream" into {MANIFEST}, {len(upstream)} ids '
                f"(highest {max(upstream)}); it {drift}"
            )
        else:
            errors.append(
                f"{MANIFEST} {drift}. Run "
                "`python -m backend.scripts.upstream_gate bookings --live` to generate it."
            )
    else:
        print(f"bookings current: {len(upstream)} ids on {UPSTREAM_REF}, highest {max(upstream)}")

    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="upstream_gate", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    checker = commands.add_parser("check", help="print TRUE/FALSE for each ref; FALSE exits 1")
    checker.add_argument("refs", nargs="*", default=list(DEFAULT_REFS))
    checker.add_argument(
        "--no-fetch",
        action="store_true",
        help="answer from the refs this repository already holds; only git hooks may use this",
    )

    commands.add_parser("absorb", help="fetch upstream and land it, or refuse").add_argument(
        "--into",
        metavar="fusion/<task>",
        help="absorb upstream and the trunk into this lane instead of into the trunk",
    )

    bookings = commands.add_parser("bookings", help="check the tracked upstream revision-id set")
    bookings.add_argument(
        "--live", action="store_true", help="fetch first, then write the refreshed set"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return check_refs(list(args.refs or DEFAULT_REFS), fetch=not args.no_fetch)
        if args.command == "absorb":
            return absorb_command(into=args.into)
        return bookings_command(live=args.live)
    except GateError as error:
        print(f"upstream_gate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
