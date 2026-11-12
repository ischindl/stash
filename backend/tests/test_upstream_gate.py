"""The anti-fork predicate measured against a synthetic fork it can really break.

Every assertion here runs the shipped command as a subprocess, in a throwaway
repository built to be provably unanchored, and reads its real exit code and real
stdout. The fixture is a two-repo fork: `upstream` is the trunk somebody else
advances, `downstream` is this repository's shape — a clone with a lane branch.
The next migration lands on upstream only, so downstream's trunk is unanchored by
construction, and the only way it becomes anchored is the merge `absorb` performs.

A predicate that has never been seen FALSE is not a gate, so the red case is the
headline test and the green case is what `absorb` has to earn.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SOURCE = REPO_ROOT / "backend" / "scripts" / "upstream_gate.py"
HOOK_SOURCE = REPO_ROOT / ".githooks" / "reference-transaction.sh"
INSTALLER_SOURCE = REPO_ROOT / "backend" / "scripts" / "install_hooks.py"

# Mirrors this repository's measured ruff settings, because `absorb` runs ruff over the
# merged tree and a fixture that lints under different rules would refuse for the wrong
# reason. Kept as a standalone file so the fixture does not inherit any config by accident.
FIXTURE_RUFF_TOML = (
    "line-length = 100\n"
    'target-version = "py312"\n'
    "\n"
    "[lint]\n"
    'select = ["E", "F", "W", "I", "UP"]\n'
    'ignore = ["E501"]\n'
)
MIGRATION_TEMPLATE = '"""Fixture migration {revision}."""\n'
GUARD_STUB = (
    '"""Stub graph guard: the fixture tree always has exactly one head."""\n'
    "\n"
    'if __name__ == "__main__":\n'
    '    print("fixture migration graph ok")\n'
)
RED_GUARD_STUB = '"""Stub graph guard that is red."""\n\nraise SystemExit(1)\n'
# Formatted the way ruff wants it: `absorb` runs the format gate before it publishes, and
# this stub has to survive that gate to reach the compare-and-swap it exists to test.
INTRUDER_GUARD_STUB = (
    '"""Stub graph guard that advances the trunk, standing in for a concurrent merge."""\n\n'
    "import subprocess\n\n"
    'subprocess.run(["git", "update-ref", "refs/heads/main", "HEAD^2"], check=True)\n'
)
FIXTURE_MANIFEST = {
    "note": "Fixture manifest.",
    "captured_at": {"commit": "0", "date": "2026-01-01", "head": "0001"},
    "revisions": {"0001": "first"},
    "stamp_only": {},
    "absorbed": [],
}


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"`git {' '.join(args)}` failed: {result.stderr}"
    return result.stdout


def interpreter_without_ruff(where: Path) -> str:
    """A real python that cannot find ruff, which is what an unattended environment looks like.

    Built rather than mocked: the claim under test is what happens when `python -m ruff` is not
    there, and only an interpreter that agrees with the premise can answer it. `--without-pip`
    keeps the fixture offline, and the probe is the premise asserted instead of assumed.
    """
    home = where / "no-ruff"
    built = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(home)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    binary = home / "bin" / "python"
    probe = subprocess.run(
        [str(binary), "-m", "ruff", "--version"], capture_output=True, text=True, check=False
    )
    assert probe.returncode != 0, "the fixture interpreter must be one that has no ruff"
    return str(binary)


class MiniFork:
    """A fork of two repositories plus the gate command, built for one test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.upstream = root / "upstream"
        self.downstream = root / "downstream"

    def gate(self, *args: str, interpreter: str | None = None) -> subprocess.CompletedProcess:
        """Run the shipped command inside the downstream clone, as an operator would.

        `interpreter` is here for the state the daily job actually meets when nobody is watching:
        a python whose environment does not contain the tools the gates are spelled with.
        """
        return subprocess.run(
            [interpreter or sys.executable, "-m", "backend.scripts.upstream_gate", *args],
            cwd=self.downstream,
            capture_output=True,
            text=True,
            check=False,
        )

    def head(self, ref: str, cwd: Path | None = None) -> str:
        return git("rev-parse", ref, cwd=cwd or self.downstream).strip()

    @property
    def git_hooks(self) -> Path:
        return self.downstream / ".git" / "hooks"

    def ship_engine_hooks(self) -> dict[str, str]:
        """Stand in for the three files the Fusion engine owns, and digest them.

        `.git/hooks` is shared by every worktree on a real box and the engine writes those
        three names itself, so an installer that touched them would silently disarm the
        engine's identity guard. The digests are what the installer is held to.
        """
        digests = {}
        for name in ("pre-commit", "prepare-commit-msg", "commit-msg"):
            entry = self.git_hooks / name
            entry.write_text(f"#!/usr/bin/env bash\n# the engine's {name}, do not touch\nexit 0\n")
            entry.chmod(0o755)
            digests[name] = hashlib.sha256(entry.read_bytes()).hexdigest()
        return digests

    def link_gate_hook(self) -> None:
        """Link the shipped hook source into `.git/hooks` the way the installer does.

        By hand rather than through `install_hooks.py`, so the hook can be measured while the
        trunk is unanchored — the state the installer itself refuses to install into.
        """
        source = self.downstream / ".githooks" / "reference-transaction.sh"
        source.chmod(0o755)
        (self.git_hooks / "reference-transaction").symlink_to(source)

    def git_try(self, *args: str) -> subprocess.CompletedProcess:
        """Run git in the downstream clone and hand back the exit code, whatever it is."""
        return subprocess.run(
            ["git", *args], cwd=self.downstream, capture_output=True, text=True, check=False
        )

    def config_text(self) -> str:
        return (self.downstream / ".git" / "config").read_text()

    def installer(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run the installer, from the primary checkout unless a worktree is named.

        Which checkout runs it is the whole question in the pruning test below, so it is a
        parameter rather than something the helper decides.
        """
        return subprocess.run(
            [sys.executable, "backend/scripts/install_hooks.py", *args],
            cwd=cwd or self.downstream,
            capture_output=True,
            text=True,
            check=False,
        )

    def ancestor(self, earlier: str, later: str) -> bool:
        """Whether `later` carries `earlier`, asked of git rather than of this test file."""
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", earlier, later],
            cwd=self.downstream,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def worktree_count(self) -> int:
        """How many checkouts this repository has — a scratch one must not survive."""
        listing = git("worktree", "list", cwd=self.downstream)
        return len([line for line in listing.splitlines() if line.strip()])

    def admin_entries(self) -> list[str]:
        """The names under `.git/worktrees/`: a refused `worktree add` must add none.

        git removes the branch and the checkout on a refusal but the administrative directory is
        the part that would keep `git worktree list` reporting a lane nobody can enter.
        """
        admin = self.downstream / ".git" / "worktrees"
        return sorted(p.name for p in admin.iterdir()) if admin.exists() else []

    def lanes(self) -> list[str]:
        return [
            line
            for line in git("branch", "--format=%(refname)", cwd=self.downstream).splitlines()
            if line.startswith("refs/heads/fusion/")
        ]

    def prune_worktree(self, path: Path) -> None:
        """What the board does when a card lands and its lane directory is cleared away."""
        git("worktree", "remove", "--force", str(path), cwd=self.downstream)
        git("worktree", "prune", cwd=self.downstream)

    def commit_file(self, repo: Path, path: str, body: str, message: str) -> str:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        git("add", path, cwd=repo)
        git("commit", "-q", "-m", message, cwd=repo)
        return self.head("HEAD", cwd=repo)

    def add_upstream_migration(self, revision: str, slug: str) -> str:
        """Land one migration on upstream only, so downstream is provably unanchored."""
        return self.commit_file(
            self.upstream,
            f"backend/migrations/versions/{revision}_{slug}.py",
            MIGRATION_TEMPLATE.format(revision=revision),
            f"add {revision}_{slug}",
        )


@pytest.fixture
def fork(tmp_path: Path) -> MiniFork:
    """A fork whose downstream trunk can be made unanchored by one upstream commit.

    The gate command is copied from this repository so the fixture exercises the
    shipped code. Its migration guard is a stub, because the fixture ships no real
    alembic graph — and `absorb` still has to pass it, which is the point.
    """
    mini = MiniFork(tmp_path)
    mini.upstream.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=mini.upstream)
    git("config", "user.email", "upstream@example.test", cwd=mini.upstream)
    git("config", "user.name", "Upstream Author", cwd=mini.upstream)

    mini.commit_file(mini.upstream, "ruff.toml", FIXTURE_RUFF_TOML, "lint config")
    mini.commit_file(mini.upstream, "README.md", "trunk\n", "commit A")
    mini.commit_file(
        mini.upstream, "backend/migrations/check_heads.py", GUARD_STUB, "ship graph guard"
    )
    mini.commit_file(
        mini.upstream,
        "backend/migrations/shipped_revisions.json",
        json.dumps(FIXTURE_MANIFEST, indent=1) + "\n",
        "ship bookings manifest",
    )
    mini.commit_file(
        mini.upstream,
        "backend/migrations/versions/0001_first.py",
        MIGRATION_TEMPLATE.format(revision="0001"),
        "add 0001_first",
    )
    mini.commit_file(mini.upstream, "backend/__init__.py", "", "package")
    mini.commit_file(mini.upstream, "backend/scripts/__init__.py", "", "package")
    mini.commit_file(
        mini.upstream,
        "backend/scripts/upstream_gate.py",
        GATE_SOURCE.read_text(),
        "ship the gate",
    )
    mini.commit_file(
        mini.upstream,
        ".githooks/reference-transaction.sh",
        HOOK_SOURCE.read_text(),
        "ship the hook",
    )
    # Ship it executable, the way this repository has to. git skips a hook file with no
    # executable bit and says nothing about it, so a tracked hook that checks out 644 is a gate
    # that is silently not running in every checkout on every machine.
    git("update-index", "--chmod=+x", ".githooks/reference-transaction.sh", cwd=mini.upstream)
    git("commit", "-q", "-m", "keep the shipped hook executable", cwd=mini.upstream)
    mini.commit_file(
        mini.upstream,
        "backend/scripts/install_hooks.py",
        INSTALLER_SOURCE.read_text(),
        "ship the hook installer",
    )
    mini.commit_file(mini.upstream, "cli/__init__.py", "", "package")

    git("clone", "-q", str(mini.upstream), str(mini.downstream), cwd=tmp_path)
    git("config", "user.email", "downstream@example.test", cwd=mini.downstream)
    git("config", "user.name", "Downstream Author", cwd=mini.downstream)
    return mini


def test_check_is_false_and_names_the_upstream_commit(fork: MiniFork) -> None:
    """AC1's red half: a fork that has not absorbed names the commit it is missing."""
    missing = fork.add_upstream_migration("0002", "second")

    result = fork.gate("check", "refs/heads/main")

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"FALSE refs/heads/main origin_main={missing}" in result.stdout, result.stdout
    assert f"main={fork.head('refs/heads/main')}" in result.stdout, result.stdout
    assert "merge_base=" in result.stdout and "ahead/behind=" in result.stdout, result.stdout


def test_check_is_true_only_after_the_upstream_commit_is_absorbed(fork: MiniFork) -> None:
    """AC1's green half: the same command turns TRUE by absorbing, never by arguing."""
    landed = fork.add_upstream_migration("0002", "second")

    absorbed = fork.gate("absorb")
    assert absorbed.returncode == 0, absorbed.stdout + absorbed.stderr
    assert "advanced refs/heads/main" in absorbed.stdout, absorbed.stdout

    result = fork.gate("check", "refs/heads/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"TRUE refs/heads/main origin_main={landed}" in result.stdout, result.stdout


def test_the_fetch_is_load_bearing_not_decorative(fork: MiniFork) -> None:
    """Without the fetch the answer is wrong, which is why `check` always fetches.

    `--no-fetch` exists only because a git hook may not touch the network. This is the
    measurement of what that costs, and why a hook may read the ref: somebody fetched it
    moments before, and the trunk gate refuses the advance rather than trusting it.
    """
    fork.add_upstream_migration("0002", "second")

    stale = fork.gate("check", "refs/heads/main", "--no-fetch")
    fresh = fork.gate("check", "refs/heads/main")

    assert stale.returncode == 0 and "TRUE" in stale.stdout, stale.stdout
    assert fresh.returncode == 1 and "FALSE" in fresh.stdout, fresh.stdout + fresh.stderr


def test_upstream_sha_never_comes_from_fetch_head(fork: MiniFork) -> None:
    """`.git/FETCH_HEAD` belongs to whichever fetch wrote it, so it is not a truth source."""
    fork.gate("check", "refs/heads/main")
    decoy = fork.add_upstream_migration("0002", "second")
    (fork.downstream / ".git" / "FETCH_HEAD").write_text(
        f"{decoy}\tbranch 'main' of {fork.upstream}\n"
    )

    result = fork.gate("check", "refs/heads/main", "--no-fetch")

    assert result.returncode == 0, result.stdout + result.stderr
    reported = fork.head("refs/remotes/origin/main")
    assert f"origin_main={reported}" in result.stdout, result.stdout
    assert decoy not in result.stdout, result.stdout


def test_every_ref_gets_its_own_line_and_one_false_ref_fails_the_run(fork: MiniFork) -> None:
    """Absorbing upstream rescues the trunk, never a lane that is already cut.

    A lane branch made before the absorb keeps a base that predates upstream's tip, so it
    stays FALSE after main moves. That is the whole reason the engine rebases a lane onto
    local `main` before merging it (`worktreeRebaseBeforeMerge: true`): the trunk being
    anchored makes a *fresh* lane anchored, and nothing else.
    """
    fork.add_upstream_migration("0002", "second")
    git("checkout", "-q", "-b", "fusion/stas-9001", cwd=fork.downstream)
    lane_commit = fork.commit_file(fork.downstream, "lane.txt", "lane work\n", "lane commit")
    git("checkout", "-q", "main", cwd=fork.downstream)

    assert fork.gate("absorb").returncode == 0
    result = fork.gate("check", "refs/heads/main", "refs/heads/fusion/stas-9001")

    assert result.returncode == 1, result.stdout
    assert "TRUE refs/heads/main" in result.stdout, result.stdout
    assert "FALSE refs/heads/fusion/stas-9001" in result.stdout, result.stdout
    assert f"refs/heads/fusion/stas-9001={lane_commit}" in result.stdout, result.stdout


def test_an_unresolvable_ref_is_a_named_failure_not_a_substitute(fork: MiniFork) -> None:
    """A missing ref exits 1 with its name; no default value stands in for it."""
    result = fork.gate("check", "refs/heads/no-such-branch")

    assert result.returncode == 1, result.stdout
    assert "refs/heads/no-such-branch" in result.stderr, result.stderr
    assert "TRUE" not in result.stdout and "FALSE" not in result.stdout, result.stdout


def test_absorb_refuses_a_conflict_and_changes_nothing(fork: MiniFork) -> None:
    """The job never picks a side: a conflicted merge aborts and the trunk does not move.

    Choosing `--theirs` here would silently revert whichever side the job guessed wrong,
    which is the recorded revert class on this box, so a refusal naming the paths is the
    only correct output and conflict resolution belongs to a lane with a reviewer.
    """
    fork.commit_file(fork.downstream, "shared.txt", "ours\n", "our edit")
    fork.commit_file(fork.upstream, "shared.txt", "theirs\n", "their edit")

    before = fork.head("refs/heads/main")
    result = fork.gate("absorb")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "shared.txt" in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "a refused absorb must not move the trunk"
    assert fork.worktree_count() == 1, "the scratch worktree must not survive a refusal"


def test_absorb_refuses_a_red_gate_and_publishes_nothing(fork: MiniFork) -> None:
    """A clean-merging but broken trunk is refused too: the gates run before the publish.

    This is the common case, not the exotic one — an upstream release bump merges cleanly
    and can still be semantically broken, and fork CI can never see it because this trunk
    is not pushed (`pushAfterMerge: false`).
    """
    fork.commit_file(
        fork.downstream,
        "backend/migrations/check_heads.py",
        RED_GUARD_STUB,
        "break the graph guard",
    )
    fork.add_upstream_migration("0002", "second")

    before = fork.head("refs/heads/main")
    result = fork.gate("absorb")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "backend/migrations/check_heads.py" in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "a red gate must not advance the trunk"
    assert fork.worktree_count() == 1, "the scratch worktree must not survive a refusal"


def test_absorb_names_a_red_ruff_gate_and_publishes_nothing(fork: MiniFork) -> None:
    """A merge that is clean but unlintable is refused by the gate that can say which rule fired.

    Kept apart from the case of a ruff that is not installed at all, on purpose: "the code is
    wrong" and "the tool is missing" are different answers for whoever gets paged, and the
    fixture can produce both, so both are measured.
    """
    fork.commit_file(fork.downstream, "backend/loose_end.py", "import json\n", "unlintable file")
    fork.add_upstream_migration("0002", "second")

    before = fork.head("refs/heads/main")
    result = fork.gate("absorb")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "-m ruff check backend/ cli/" in result.stderr, result.stderr
    assert "F401" in result.stderr, result.stderr
    assert "No module named ruff" not in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "a red gate must not advance the trunk"
    assert fork.worktree_count() == 1, "the scratch worktree must not survive the refusal"


def test_absorb_refuses_by_name_when_ruff_is_not_installed(fork: MiniFork) -> None:
    """The unattended case: a missing tool is a named refusal, never a traceback mid-merge.

    The daily job starts from a scheduler agent's heartbeat, whose environment need not carry the
    dev console script. Spelled `python -m ruff` and probed before anything is fetched, the answer
    is one line naming the tool and stating that nothing was published. Spelled as a bare `ruff`,
    the same day died with a FileNotFoundError inside the merge, past the point where the job had
    already built a scratch worktree.
    """
    python = interpreter_without_ruff(fork.root)
    fork.add_upstream_migration("0002", "second")

    before = fork.head("refs/heads/main")
    result = fork.gate("absorb", interpreter=python)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "cannot run its pre-publish gates" in result.stderr, result.stderr
    assert "-m ruff" in result.stderr, result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "a missing tool must not advance the trunk"
    assert fork.worktree_count() == 1, "and must not leave a scratch worktree behind"


def test_absorb_refuses_to_move_a_trunk_somebody_is_editing(fork: MiniFork) -> None:
    """`update-ref` cannot tell a checkout that its worktree now describes another commit.

    The absorb job runs unattended while the trunk checkout is a working directory like
    any other. Moving the ref out from under uncommitted edits would leave that index
    reading one commit and HEAD naming another, so the job refuses and says where the work
    is. Untracked files are not a reason to refuse: they belong to no commit on either side.
    """
    fork.add_upstream_migration("0002", "second")
    (fork.downstream / "README.md").write_text("trunk\nand my uncommitted edit\n")
    (fork.downstream / "scratch-not-in-git.txt").write_text("untracked\n")

    before = fork.head("refs/heads/main")
    result = fork.gate("absorb")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "uncommitted changes" in result.stderr, result.stderr
    assert "README.md" not in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "a refused absorb must not move the trunk"


def test_absorb_into_a_lane_lands_the_trunk_without_moving_the_trunk(fork: MiniFork) -> None:
    """A lane stranded behind a trunk change absorbs it, instead of losing its own work.

    Measured on the real board: a lane whose predicate stayed FALSE through three trunk
    advances had two paths — `merge --squash` plus renumbering 109 lines, which the reviewer
    called a defect, or absorb the trunk into the lane. This is the second path, and the
    claim it has to hold is that the lane ends up carrying both the trunk and upstream while
    the lane's own commits survive and the trunk ref does not move.
    """
    git("checkout", "-q", "-b", "fusion/step-1-demo", cwd=fork.downstream)
    lane_commit = fork.commit_file(
        fork.downstream, "lane-work.txt", "the lane's own edit\n", "work on the lane"
    )
    git("checkout", "-q", "main", cwd=fork.downstream)
    fork.commit_file(fork.downstream, "trunk-work.txt", "a trunk change\n", "trunk advances")
    upstream_commit = fork.add_upstream_migration("0002", "second")

    trunk_before = fork.head("refs/heads/main")
    result = fork.gate("absorb", "--into", "fusion/step-1-demo")

    assert result.returncode == 0, result.stdout + result.stderr
    lane = "refs/heads/fusion/step-1-demo"
    assert fork.head("refs/heads/main") == trunk_before, (
        "absorbing into a lane cannot move the trunk"
    )
    assert fork.ancestor(trunk_before, lane), "the lane must carry the trunk it was waiting for"
    assert fork.ancestor(upstream_commit, lane), "the lane must carry upstream too"
    assert git("show", f"{lane}:lane-work.txt", cwd=fork.downstream) == "the lane's own edit\n"
    assert fork.head(lane) != lane_commit, "the lane advances rather than being rewritten"
    assert fork.worktree_count() == 1, "the scratch worktree must not survive"


def test_absorb_into_a_ref_that_is_not_a_lane_is_refused(fork: MiniFork) -> None:
    """This command writes the trunk and engine lanes, which are the refs it enforces."""
    result = fork.gate("absorb", "--into", "main")

    assert result.returncode == 1, result.stdout
    assert "not an engine lane" in result.stderr, result.stderr
    assert fork.worktree_count() == 1, "the refusal must not leave a scratch worktree"


def test_absorb_discards_its_result_when_someone_else_advances_the_target(fork: MiniFork) -> None:
    """The publish is a compare-and-swap, so a concurrent advance is never overwritten.

    The engine squash-merges into the trunk, the absorb job publishes to it, and the
    operator merges by hand — all against the same ref. Whichever of them read the tip
    first must still lose the race rather than force, so the loser exits 1 and the winner's
    commit stays. The detached checkout here is what a lane-side publish looks like: no
    checkout holds the branch, so the compare-and-swap is the only thing standing between
    the two writers.
    """
    fork.commit_file(
        fork.upstream, "backend/migrations/check_heads.py", INTRUDER_GUARD_STUB, "intruder"
    )
    git("checkout", "-q", "--detach", cwd=fork.downstream)

    result = fork.gate("absorb")
    intruder_tip = fork.head("refs/remotes/origin/main")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "moved while this absorb was running" in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == intruder_tip, (
        "the concurrent advance must survive the loser's discard"
    )
    assert fork.worktree_count() == 1, "the scratch worktree must not survive the refusal"


def test_bookings_refuses_a_manifest_without_an_upstream_map(fork: MiniFork) -> None:
    """The booking guard cannot know upstream's ids from a manifest that lacks them."""
    result = fork.gate("bookings")

    assert result.returncode == 1, result.stdout
    assert '"upstream" map' in result.stderr, result.stderr


def test_bookings_live_writes_the_upstream_set_and_then_reads_current(fork: MiniFork) -> None:
    """The refresh is generated, never hand-typed, and it converges when re-run."""
    fork.add_upstream_migration("0002", "second")

    refreshed = fork.gate("bookings", "--live")
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    booked = json.loads((fork.downstream / "backend/migrations/shipped_revisions.json").read_text())
    assert booked["upstream"] == {"0001": "first", "0002": "second"}, booked.get("upstream")

    again = fork.gate("bookings")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "bookings current" in again.stdout, again.stdout


def test_bookings_is_red_when_this_tree_rebooks_an_upstream_id(fork: MiniFork) -> None:
    """The STAS-371 class at the command's own surface: same id, different migration.

    Our `0002_other_thing` and upstream's `0002_second` merge cleanly in git and leave
    every database stamped `0002` skipping one of the two, so this is a refusal, not a
    warning, and it names both slugs so the renumber lane has something to work from.
    """
    fork.add_upstream_migration("0002", "second")
    assert fork.gate("bookings", "--live").returncode == 0
    fork.commit_file(
        fork.downstream,
        "backend/migrations/versions/0002_other_thing.py",
        MIGRATION_TEMPLATE.format(revision="0002"),
        "book 0002 again under a different slug",
    )

    result = fork.gate("bookings")

    assert result.returncode == 1, result.stdout
    assert "0002" in result.stderr and '"second"' in result.stderr, result.stderr
    assert "other_thing" in result.stderr, result.stderr


def test_an_upstream_that_lists_no_migrations_is_a_refusal_and_not_a_crash(
    fork: MiniFork,
) -> None:
    """An empty listing does not mean "upstream has booked nothing", and it never did.

    The defect this closes was measured in the fixture while writing the ceiling rule: `max()` over
    an empty id set raised `ValueError` out of `absorb` after the merge had already run, traceback
    and all, in the job nobody watches. A ceiling read off a listing nobody checked is the
    STAS-371 incident, so both verbs say the listing was empty and stop.
    """
    git("rm", "-r", "-q", "backend/migrations/versions", cwd=fork.upstream)
    git("commit", "-q", "-m", "drop the versions directory", cwd=fork.upstream)
    fork.git_try("fetch", "origin")

    checked = fork.gate("bookings")
    trunk_before = fork.head("refs/heads/main")
    absorbed = fork.gate("absorb")

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "no revision files" in checked.stderr, checked.stderr
    assert absorbed.returncode == 1, absorbed.stdout + absorbed.stderr
    assert "no revision files" in absorbed.stderr, absorbed.stderr
    assert "Traceback" not in absorbed.stderr, absorbed.stderr
    assert fork.head("refs/heads/main") == trunk_before
    assert fork.worktree_count() == 1, "the scratch worktree must not survive the refusal"


# ---------------------------------------------------------------------------- git hook
#
# Everything below installs the shipped hook file by hand and drives real git commands,
# because the claim under test is "git refuses this write", which only git can answer.


def test_a_lane_cut_while_the_trunk_is_unanchored_is_reported_and_created(
    fork: MiniFork,
) -> None:
    """While the trunk is behind upstream no lane can be anchored, so refusing one is a lockout.

    This is the ordinary state while the absorb debt stands: the job fetches before it merges, so
    `origin/main` moves even on the days its merge is refused. A refusal on lane creation there
    prevents no fork — it forbids the one lane whose job is to clear the conflict, and a
    `reference-transaction` hook has no `--no-verify`, so the only way out would be uninstalling
    the gate. The seam measured is the one the board itself runs, `git worktree add -b`, and not
    only `git checkout -b`.
    """
    fork.link_gate_hook()
    missing = fork.add_upstream_migration("0002", "second")
    fork.git_try("fetch", "origin")
    lane = fork.root / "repair-lane"

    created = fork.git_try("worktree", "add", "-b", "fusion/step-1-demo", str(lane))
    checkout = fork.git_try("checkout", "-b", "fusion/step-2-demo")

    assert created.returncode == 0, created.stdout + created.stderr
    assert checkout.returncode == 0, checkout.stdout + checkout.stderr
    assert "upstream-gate: warning" in created.stderr, created.stderr
    assert f"origin_main={missing}" in created.stderr, created.stderr
    assert "refused" not in created.stderr, created.stderr
    assert "refused" not in checkout.stderr, checkout.stderr
    assert fork.lanes() == [
        "refs/heads/fusion/step-1-demo",
        "refs/heads/fusion/step-2-demo",
    ], created.stderr
    git("worktree", "remove", "--force", str(lane), cwd=fork.downstream)

    # Being able to create the lane is worth nothing if the lane cannot do its job: the repair
    # lane absorbs upstream and the trunk, and the trunk stays where the refusal left it.
    trunk_before = fork.head("refs/heads/main")
    absorbed = fork.gate("absorb", "--into", "fusion/step-1-demo")

    assert absorbed.returncode == 0, absorbed.stdout + absorbed.stderr
    assert fork.head("refs/heads/main") == trunk_before
    assert fork.ancestor("refs/remotes/origin/main", "refs/heads/fusion/step-1-demo")


def test_a_lane_cut_below_an_anchored_trunk_is_refused_and_leaves_nothing_behind(
    fork: MiniFork,
) -> None:
    """The other ordering, and the actual mistake: the trunk carries upstream, the base does not.

    Once `main` holds `origin/main`, a lane that does not was cut below the trunk, which is the
    fork this gate exists to stop. A refusal is only worth having if it leaves nothing: a branch
    that exists anyway, or a `.git/worktrees/` entry that keeps `git worktree list` naming a
    checkout nobody can enter, would both be worse than the warning given in the state above.
    """
    fork.link_gate_hook()
    fork.add_upstream_migration("0002", "second")
    fork.git_try("fetch", "origin")
    stale_base = fork.head("refs/heads/main")
    assert fork.gate("absorb").returncode == 0, "the fixture needs an anchored trunk"
    assert not fork.ancestor("refs/remotes/origin/main", stale_base), (
        "the premise: this base must really sit below upstream"
    )
    lane = fork.root / "stale-lane"

    result = fork.git_try("worktree", "add", "-b", "fusion/stale", str(lane), stale_base)
    branch = fork.git_try("branch", "fusion/stale2", stale_base)

    assert result.returncode != 0, "a lane cut below an anchored trunk must not be creatable"
    assert "upstream-gate" in result.stderr, result.stderr
    assert "FALSE" in result.stderr, result.stderr
    assert "chosen below it" in result.stderr, result.stderr
    assert branch.returncode != 0, result.stderr
    assert fork.lanes() == [], "a refused creation must not leave the branch behind"
    assert fork.admin_entries() == [], "a refused worktree add must not leave an admin entry"
    assert fork.worktree_count() == 1, "and must not leave a checkout either"


def test_the_hook_refuses_the_trunk_advancing_onto_an_unanchored_commit(fork: MiniFork) -> None:
    """The engine's squash-merge advance is a ref write, and that write is the gate's target.

    `git update-ref` is what the engine, the absorb job, and a hand-run merge all end up
    doing to `refs/heads/main`, so it is the primitive worth measuring directly rather than
    trusting whichever command happens to reach for it. The commit being pointed at is made
    before the gate is linked, because the gate refuses that commit as well.
    """
    fork.commit_file(fork.downstream, "our-work.txt", "ours\n", "a commit made while unanchored")
    unanchored_tip = fork.head("HEAD")
    fork.git_try("reset", "--hard", "HEAD~1")
    fork.link_gate_hook()
    fork.add_upstream_migration("0002", "second")
    fork.git_try("fetch", "origin")
    before = fork.head("refs/heads/main")

    result = fork.git_try("update-ref", "refs/heads/main", unanchored_tip, before)

    assert result.returncode != 0, result.stderr
    assert "upstream-gate" in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "the refusal must leave the trunk where it was"

    # The refusal covers the trunk ref in both directions, including an operator's undo: `main^`
    # is an ancestor of an unanchored tip, so it is unanchored too, and a hook on
    # `reference-transaction` has no `--no-verify` to step around it. The way through an undo is
    # `install_hooks.py --uninstall`, the ref move, then install again. Asserted so the symmetry
    # is a decision here rather than something a later change quietly gates one way.
    one_back = fork.git_try("rev-parse", "refs/heads/main^").stdout.strip()
    rewind = fork.git_try("update-ref", "refs/heads/main", one_back, before)

    assert rewind.returncode != 0, rewind.stderr
    assert fork.head("refs/heads/main") == before, "a trunk rewind is refused while unanchored too"


def test_the_hook_refuses_a_squash_merge_onto_an_unanchored_trunk(fork: MiniFork) -> None:
    """The engine merges with `merge --squash` plus a commit; the commit is a trunk write.

    This is the exact sequence that lost ten files on STAS-369, and while the trunk itself is
    unanchored the hook stops it. The measured residual: with an *anchored* trunk and an
    unanchored lane the squash result stays anchored, so no trunk-side hook can see the loss —
    what covers that case is the lane refusal above plus the engine's own rebase-before-merge.
    """
    fork.link_gate_hook()
    fork.git_try("checkout", "-q", "-b", "fusion/step-2-demo")
    fork.commit_file(fork.downstream, "lane.txt", "lane work\n", "work on the lane")
    fork.git_try("checkout", "-q", "main")
    fork.add_upstream_migration("0002", "second")
    fork.git_try("fetch", "origin")
    before = fork.head("refs/heads/main")

    merge = fork.git_try("merge", "--squash", "fusion/step-2-demo")
    assert merge.returncode == 0, merge.stdout + merge.stderr
    result = fork.git_try("commit", "-m", "squashed lane into the trunk")

    assert result.returncode != 0, "the squash-merge advance must be refused"
    assert "upstream-gate" in result.stderr, result.stderr
    assert fork.head("refs/heads/main") == before, "the refusal must leave the trunk where it was"


def test_the_hook_stays_out_of_the_way_of_ordinary_work(fork: MiniFork) -> None:
    """A gate that chatters on every commit gets uninstalled inside a week.

    Ordinary work on a lane and an ordinary fetch must each exit zero and print nothing, even
    while the trunk is unanchored — which is the state every existing lane is in during the
    transition, and the reason this hook judges new lanes and the trunk but never a commit
    already sitting on a lane.
    """
    fork.git_try("checkout", "-q", "-b", "fusion/step-3-demo")
    fork.link_gate_hook()
    fork.add_upstream_migration("0002", "second")

    commit = fork.git_try("commit", "--allow-empty", "-m", "ordinary lane commit")
    fetch = fork.git_try("fetch", "origin")

    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert commit.stderr == "", "the hook must say nothing about a write it does not refuse"
    assert "upstream-gate" not in commit.stdout, commit.stdout
    assert fetch.returncode == 0, fetch.stderr
    assert "upstream-gate" not in fetch.stderr, fetch.stderr


def test_the_hook_lets_the_same_writes_pass_once_the_trunk_is_anchored(fork: MiniFork) -> None:
    """The positive control: after an absorb, the work the refusals above block must be possible.

    Without this the gate could be "passing" by blocking everything, which is how a gate gets
    uninstalled. The fixture absorbs upstream into the trunk, and then a lane is cut and a lane
    is squash-merged into the trunk exactly as the engine does them.
    """
    fork.link_gate_hook()
    assert fork.gate("absorb").returncode == 0, "the fixture needs an anchored trunk"

    lane = fork.git_try("checkout", "-q", "-b", "fusion/step-4-demo")
    assert lane.returncode == 0, lane.stdout + lane.stderr
    fork.commit_file(fork.downstream, "lane.txt", "lane work\n", "work on the lane")
    fork.git_try("checkout", "-q", "main")

    merge = fork.git_try("merge", "--squash", "fusion/step-4-demo")
    assert merge.returncode == 0, merge.stdout + merge.stderr
    advance = fork.git_try("commit", "-m", "squashed lane into the trunk")

    assert advance.returncode == 0, advance.stdout + advance.stderr
    assert fork.git_try("rev-parse", "--verify", "refs/heads/fusion/step-4-demo").returncode == 0
    assert "lane.txt" in fork.git_try("ls-tree", "--name-only", "refs/heads/main").stdout


# ------------------------------------------------------------------------ the installer


def test_installer_refuses_to_install_while_the_trunk_is_unanchored(fork: MiniFork) -> None:
    """Installing the gate while the trunk fails it would freeze every write on the box.

    Measured before claimed: with `refs/heads/main` unabsorbed, the hook refuses the engine's
    squash-merge advance, so installing here would stop every merge in flight — including this
    card's own. Activation belongs after the first absorb, and the installer says so.
    """
    fork.ship_engine_hooks()
    fork.add_upstream_migration("0002", "second")

    result = fork.installer()

    assert result.returncode == 1, result.stdout + result.stderr
    assert "does not satisfy the predicate" in result.stderr, result.stderr
    assert not (fork.git_hooks / "reference-transaction").exists(), "refusing must not half-install"


def test_installer_installs_once_the_trunk_is_anchored_and_touches_nothing_else(
    fork: MiniFork,
) -> None:
    """What activation changes: one symlink, and nothing else about this repository.

    The engine's three hook files are byte-identical across the install, `core.hooksPath` is
    still unset afterwards, and `.git/config` comes back unchanged — the shared-state claim
    this card gets graded on.
    """
    digests_before = fork.ship_engine_hooks()
    config_before = fork.config_text()
    assert fork.gate("absorb").returncode == 0, (
        "the fixture needs an anchored trunk to install into"
    )

    result = fork.installer()
    status = fork.installer("--status")

    assert result.returncode == 0, result.stdout + result.stderr
    entry = fork.git_hooks / "reference-transaction"
    assert entry.is_symlink(), "the hook is installed as a link into the tracked source"
    assert entry.resolve().is_file() and entry.stat().st_mode & 0o111, "and git can execute it"
    assert "installed and executable" in status.stdout, status.stdout
    for name, digest in digests_before.items():
        after = hashlib.sha256((fork.git_hooks / name).read_bytes()).hexdigest()
        assert after == digest, f"the engine's {name} changed"
    assert fork.config_text() == config_before, "installing a hook must not edit .git/config"

    removed = fork.installer("--uninstall")
    assert removed.returncode == 0, removed.stderr
    assert not entry.exists() and not entry.is_symlink(), "--uninstall has to undo the install"


def test_installer_refuses_to_move_core_hooks_path(fork: MiniFork) -> None:
    """`core.hooksPath` would relocate the engine's hooks out of the path git reads them from."""
    fork.ship_engine_hooks()
    assert fork.gate("absorb").returncode == 0
    git("config", "core.hooksPath", ".githooks", cwd=fork.downstream)

    result = fork.installer()

    assert result.returncode == 1, result.stdout + result.stderr
    assert "core.hooksPath" in result.stderr, result.stderr
    assert (fork.git_hooks / "pre-commit").exists(), "the engine hooks stay where git reads them"


def test_installer_refuses_to_replace_a_hook_file_it_did_write(fork: MiniFork) -> None:
    """Something else already owns that filename; the installer reports it instead of winning."""
    fork.ship_engine_hooks()
    assert fork.gate("absorb").returncode == 0
    (fork.git_hooks / "reference-transaction").write_text("#!/usr/bin/env bash\nexit 0\n")

    result = fork.installer()

    assert result.returncode == 1, result.stdout + result.stderr
    assert "was not written by this installer" in result.stderr, result.stderr


def test_a_pruned_lane_worktree_cannot_take_the_installed_gate_down_with_it(
    fork: MiniFork,
) -> None:
    """`.git/hooks` outlives every worktree; a link into one that is pruned is a vanished gate.

    The defect measured here is silent: the link was written against whichever checkout ran the
    installer, so installing from a lane and letting the board prune it on landing left the shared
    hook pointing at a deleted directory. git finds no executable file at the end of a dangling
    link and skips the hook without a word, which is a trunk that advances unanchored while every
    surface still looks configured. So the assertion is not about the symlink, it is about git
    still refusing the write after the pruning.
    """
    fork.ship_engine_hooks()
    assert fork.gate("absorb").returncode == 0, "the fixture needs an anchored trunk to install"
    lane = fork.root / "installing-lane"
    git("worktree", "add", "-q", "-b", "fusion/installer", str(lane), cwd=fork.downstream)

    installed = fork.installer(cwd=lane)
    entry = fork.git_hooks / "reference-transaction"

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (
        entry.resolve() == (fork.downstream / ".githooks" / "reference-transaction.sh").resolve()
    ), "the shared hook must not point into the worktree that happened to install it"

    fork.prune_worktree(lane)
    assert entry.is_symlink() and entry.resolve().is_file(), "the gate has to survive the pruning"

    fork.add_upstream_migration("0002", "second")
    fork.git_try("fetch", "origin")
    before = fork.head("refs/heads/main")
    advance = fork.git_try(
        "commit", "--allow-empty", "-m", "trunk advance over unabsorbed upstream"
    )

    assert advance.returncode != 0, (
        "the gate must still fire once the worktree that installed it is gone: "
        + advance.stdout
        + advance.stderr
    )
    assert "upstream-gate" in advance.stderr, advance.stderr
    assert fork.head("refs/heads/main") == before


def test_installer_repairs_a_link_left_behind_by_a_pruned_worktree(fork: MiniFork) -> None:
    """A dangling link is the gate disappearing quietly, and the next install is what fixes it.

    Made by hand because it is exactly what an install that linked into a worktree leaves behind.
    The repair is deliberately not held behind `--force`: the fix is one symlink, and demanding
    that the operator first notice the drift is how a gate stays dead.
    """
    fork.ship_engine_hooks()
    assert fork.gate("absorb").returncode == 0
    entry = fork.git_hooks / "reference-transaction"
    entry.symlink_to(fork.root / "already-pruned" / ".githooks" / "reference-transaction.sh")

    result = fork.installer()
    status = fork.installer("--status")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "re-pointed" in result.stdout, result.stdout
    assert entry.resolve().is_file() and entry.stat().st_mode & 0o111
    assert "installed and executable" in status.stdout, status.stdout
    assert fork.installer().stdout.count("already installed") == 1, "installing is idempotent"
