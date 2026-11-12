"""Install the upstream gate's git hooks into this repository's shared hook directory.

The gate itself lives in `backend/scripts/upstream_gate.py`; a gate an agent can skip is not
a gate, so the enforcement has to sit in git. It goes into `.git/hooks/`, which is shared by
every worktree on this machine — the one place a write to `refs/heads/main` can be stopped no
matter which checkout, agent, or hand runs it.

Installed entries are symlinks into the tracked `.githooks/` directory, so updating the rule
means editing the tracked file, and no copy can go stale. They link into the *durable* checkout
— the one the shared `.git` directory belongs to — and not into whichever worktree happened to
run this installer, because linked worktrees are pruned routinely and a dangling link is a hook
git skips without a word.

Nothing here is installed silently:

  * `core.hooksPath` must be unset. Redirecting it would move the engine's three hooks
    (`pre-commit`, `prepare-commit-msg`, `commit-msg`) out of the path they are run from,
    which stops the engine's own identity guard from running.
  * A hook file already at one of our names is left alone and the install refuses, unless it
    is a link this installer made. `--force` replaces a foreign file; it never replaces an
    engine-owned one, because those names are not ours to write.
  * The trunk must already satisfy the predicate. Installing while `refs/heads/main` is not
    built on `origin/main` refuses *every* trunk write on this machine — including this
    card's own merge and every lane merge — so activation is a decision made after the first
    absorb, not before it. `check` first, then install.

Run from the repo root:
  `python backend/scripts/install_hooks.py`          # install
  `python backend/scripts/install_hooks.py --status` # what is installed, and does it resolve
  `python backend/scripts/install_hooks.py --uninstall`
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HOOK_SOURCES: dict[str, str] = {
    "reference-transaction": ".githooks/reference-transaction.sh",
}

# Written by the Fusion engine, in the directory this installer also writes to. Never touched,
# never compared for "drift", never replaced — even with --force.
ENGINE_OWNED = ("pre-commit", "prepare-commit-msg", "commit-msg")

OWNER = "installed by backend/scripts/install_hooks.py"


class InstallError(Exception):
    """A precondition that stops the install rather than half-applying it."""


def run_git(*args: str) -> str:
    """Run git and return stdout, or raise naming the command and its stderr.

    `git config --get` is not routed through here: it exits 1 to report "this setting is not
    set", which is the answer the installer wanted rather than a failure to report.
    """
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        raise InstallError(
            f"`git {' '.join(args)}` exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def configured_hooks_path() -> str:
    return subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    ).stdout.strip()


REPO_ROOT = Path(__file__).resolve().parents[2]


def hooks_dir() -> Path:
    """The hook directory git actually reads, which is the shared one in a worktree setup."""
    shown = run_git("rev-parse", "--git-path", "hooks").strip()
    path = Path(shown)
    return path if path.is_absolute() else REPO_ROOT / path


def durable_checkout() -> Path:
    """The checkout that will still be here after this worktree is pruned.

    `.git/hooks` is shared by every worktree and outlives all of them, while a linked worktree
    is routine collateral when a card lands. A hook linked from the worktree that happened to
    run the installer therefore turns into a dangling path: git finds no executable file at the
    end of it and skips the hook in silence, which leaves the trunk gate gone and nothing in the
    flow saying so. The parent of the common git directory is the checkout that is not pruned.
    """
    shown = run_git("rev-parse", "--git-common-dir").strip()
    common = Path(shown)
    if not common.is_absolute():
        common = REPO_ROOT / common
    return common.resolve().parent


def gate_blocks_trunk() -> str:
    """Ask the shipped predicate whether `refs/heads/main` may hold what it holds today.

    Returns "" when the trunk is anchored, or the reason installation would freeze the board.

    This asks with a fetch, because installing is a deliberate act and has to be judged against
    upstream as it is now rather than as the last fetch left it. The hook it installs is the
    opposite by design: it judges only what the repository already holds, because a hook that
    touches the network can hang the very merge it is meant to police.
    """
    result = subprocess.run(
        [
            sys.executable,
            "backend/scripts/upstream_gate.py",
            "check",
            "refs/heads/main",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode == 0:
        return ""
    return (result.stdout + result.stderr).strip()


def describe(hook_dir: Path) -> list[str]:
    """One line per name this installer owns, plus the engine's, so the change is visible."""
    lines: list[str] = []
    for name, source in sorted(HOOK_SOURCES.items()):
        entry = hook_dir / name
        if not entry.exists() and not entry.is_symlink():
            lines.append(f"  {name}: not installed")
        elif entry.is_symlink():
            target = os.readlink(entry)
            if not entry.resolve().is_file():
                state = f"DANGLING: {target} is missing at this checkout"
            elif not entry.resolve().stat().st_mode & 0o111:
                # git follows the link, finds no executable bit, and skips the hook in silence.
                # A gate dead where nobody can see it is worse than a gate that was never installed.
                state = f"NOT EXECUTABLE: git ignores {target} as it stands"
            else:
                state = "installed and executable"
            lines.append(f"  {name}: symlink -> {target} ({state})")
        else:
            ours = OWNER in entry.read_text(errors="replace")
            lines.append(
                f"  {name}: plain file written by {'this installer' if ours else 'SOMEONE ELSE'}"
            )
    for name in ENGINE_OWNED:
        entry = hook_dir / name
        lines.append(
            f"  {name}: engine-owned, present"
            if entry.exists()
            else f"  {name}: engine-owned, absent"
        )
    lines.append(
        f"  core.hooksPath: {configured_hooks_path() or 'unset (hooks read from .git/hooks)'}"
    )
    return lines


def install(hook_dir: Path, force: bool) -> list[str]:
    changed: list[str] = []
    reason = gate_blocks_trunk()
    if reason and not force:
        raise InstallError(
            "refused to install: the trunk does not satisfy the predicate this hook enforces,\n"
            f"{reason}\n"
            "Installing now would refuse every write to refs/heads/main on this machine, including "
            "the merges already in flight. Absorb upstream first, then install."
        )
    for name, source in sorted(HOOK_SOURCES.items()):
        checkout = durable_checkout()
        target = checkout / source
        if not target.is_file():
            raise InstallError(
                f"{source} is not in {checkout}, the checkout the shared hooks directory outlives. "
                "Sync that checkout to a commit carrying the hook and install from it; linking into "
                "a worktree instead leaves the gate uninstalled the day that worktree is pruned, and "
                "git skips a hook it cannot read without saying so."
            )
        entry = hook_dir / name
        if entry.exists() or entry.is_symlink():
            if name in ENGINE_OWNED:
                raise InstallError(
                    f"{name} is engine-owned and is not this installer's to write; refusing to touch it."
                )
            if entry.is_symlink() and entry.resolve() == target:
                changed.append(f"  {name}: already installed")
                continue
            if entry.is_symlink() and os.readlink(entry).endswith(source):
                # Written by this installer, aimed at another checkout — usually one that has since
                # been pruned, which is the state where git silently stops running the hook.
                # Re-pointing is the whole repair, so it is not held behind --force.
                entry.unlink()
                changed.append(f"  {name}: link pointed at a checkout that is gone; re-pointed")
            elif not force:
                raise InstallError(
                    f"{entry} exists and was not written by this installer; refusing to overwrite it. "
                    "Remove it yourself if it is yours to remove, or pass --force."
                )
            else:
                entry.unlink()
                changed.append(f"  {name}: replaced a foreign hook file (--force)")
        target.chmod(0o755)
        entry.symlink_to(target)
        changed.append(f"  {name}: -> {source}")
    return changed


def uninstall(hook_dir: Path) -> list[str]:
    changed: list[str] = []
    for name, source in sorted(HOOK_SOURCES.items()):
        entry = hook_dir / name
        if not entry.exists() and not entry.is_symlink():
            changed.append(f"  {name}: not installed")
            continue
        if entry.is_symlink() and os.readlink(entry).endswith(source):
            entry.unlink()
            changed.append(f"  {name}: removed")
            continue
        raise InstallError(
            f"{entry} is not a link into {source}; this installer will not remove a file it did not write."
        )
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install_hooks", description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--status", action="store_true", help="report what is installed, change nothing"
    )
    action.add_argument(
        "--uninstall", action="store_true", help="remove exactly the entries this installer wrote"
    )
    parser.add_argument(
        "--force", action="store_true", help="replace a foreign hook file and an unanchored trunk"
    )
    args = parser.parse_args(argv)

    try:
        hook_dir = hooks_dir()
        if configured_hooks_path():
            raise InstallError(
                "core.hooksPath is set, so git is not reading .git/hooks at all. Pointing the gate "
                "there would move the engine's three hooks out of the path they run from, which stops "
                "the engine's identity guard. Unset core.hooksPath first."
            )
        print(f"hooks directory: {hook_dir}")
        if args.status:
            print("\n".join(describe(hook_dir)))
            return 0
        changed = uninstall(hook_dir) if args.uninstall else install(hook_dir, force=args.force)
        print("\n".join(changed))
        print("after:")
        print("\n".join(describe(hook_dir)))
        if not args.uninstall:
            print(
                "\nThe gate is live for every checkout on this machine. To undo: "
                "`python backend/scripts/install_hooks.py --uninstall`."
            )
        return 0
    except InstallError as error:
        print(f"install_hooks: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
