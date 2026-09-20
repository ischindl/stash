"""Fail unless every migration has a unique revision id and the graph has one head.

Two PRs that each add a migration on top of the same parent merge cleanly in
git but leave alembic with two heads (or two files claiming the same revision
id) — and every `alembic upgrade head` after that refuses to run: deploys,
fresh test databases, local setups. That race shipped as #712 + #713 (both
"0125") and again as #965 + #1040 (both "0185"); this check turns it into a
red PR check instead of a broken deploy. The workflow merges current main
before running it, because PR CI otherwise checks a merge ref built when the
branch last pushed — #965 passed this check against a base from ten days
earlier that did not yet contain the migration it collided with.

The same check also forbids *re-booking* a revision id that a deployed build
already stamped (see `manifest_collisions`): that race is invisible to the two
rules above because the branch never adds a duplicate id to this tree.

Run from the repo root: `python backend/migrations/check_heads.py`.
Needs only alembic installed — no database, no backend settings.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

FIX = (
    "Fix: renumber this branch's migration to sit on main's current head — "
    "bump its filename/revision to the next number and set down_revision to "
    "the current head, then rebase."
)

VERSIONS = Path(__file__).parent / "versions"
MANIFEST = Path(__file__).parent / "shipped_revisions.json"
REVISION_LINE = re.compile(r'^revision = "([^"]+)"', re.MULTILINE)


def revision_files() -> dict[str, list[Path]]:
    """Every revision id in `versions/`, mapped to the files claiming it.

    Exits on a file with no `revision = "..."` line: without an id there is
    nothing left to check, and guessing one would hide the broken file.
    """
    files_by_id: dict[str, list[Path]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        match = REVISION_LINE.search(path.read_text())
        if not match:
            print(f'{path.name} has no `revision = "..."` line', file=sys.stderr)
            sys.exit(1)
        files_by_id.setdefault(match.group(1), []).append(path)
    return files_by_id


def duplicate_revision_ids(files_by_id: dict[str, list[Path]]) -> dict[str, list[str]]:
    """Revision ids claimed by more than one file, mapped to those filenames.

    Alembic keys its script map by revision id, so a duplicate silently drops
    one of the two files instead of erroring — worth naming explicitly, since
    the head count alone does not say which files collided.
    """
    return {rev: [p.name for p in paths] for rev, paths in files_by_id.items() if len(paths) > 1}


def duplicate_errors(duplicates: dict[str, list[str]]) -> list[str]:
    return [
        f"revision id {revision} is claimed by {' and '.join(files)}; "
        f"alembic keeps only one of them.\n{FIX}"
        for revision, files in sorted(duplicates.items())
    ]


def revision_slug(path: Path) -> str:
    """The `dogfood_lineage_reconcile` half of `0213_dogfood_lineage_reconcile.py`.

    Bare ids are ambiguous in this repo: the founder (dogfood) build shipped
    `0206` as `dogfood_lineage_reconcile` while trunk's `0206` is
    `source_sync_claims`. Every shipped revision is therefore keyed by
    `(id, slug)`, never by id alone.
    """
    return path.stem.split("_", 1)[1]


def manifest_collisions(files_by_id: dict[str, list[Path]], heads: list[str]) -> list[str]:
    """Errors for files that book a revision id a deployed build already stamped.

    A database stamped `0206` cannot say *which* `0206` it ran, so if a branch
    takes an already-shipped id, `alembic upgrade head` considers the new
    migration applied and skips its schema on every stamped DB — permanently, and
    green in CI because each branch still looks self-consistent. The founder
    image's chain is the live example: its `0203`–`0209` are re-numbered copies
    of trunk's `0210`–`0216`, so trunk `0203`–`0207` were never applied there.

    `shipped_revisions.json` freezes the `(id, slug)` pairs that any deployed
    build shipped, which turns the collision red at PR time. An id that a build
    stamped but this tree never shipped lives in `stamp_only`; it may only gain a
    file once it is registered as an absorbed node in `absorbed` — a no-op
    revision whose content trunk already ships (STAS-232 registers `0209` that
    way). The declaration is checked both ways: the node has to exist, because a
    stamped database that resolves to nothing is the outage the node was added to
    close. Ids newer than the highest booked one have to form one unbroken run
    that ends at the graph head, so a branch cannot quietly start a parallel
    numbering series either — see `new_revision_run_errors`.
    """
    if not MANIFEST.exists():
        return [
            f"{MANIFEST.name} is missing from {MANIFEST.parent.parent.name}/{MANIFEST.parent.name}/. "
            "It must list every revision id any deployed build shipped; a missing "
            "manifest is a broken guard, not a passing one. Recreate it by "
            "snapshotting the shipped tree's (id, slug) pairs — see the `note` "
            "field in a previous copy or `git log --diff-filter=D -- "
            "backend/migrations/shipped_revisions.json`."
        ]
    try:
        manifest = json.loads(MANIFEST.read_text())
        shipped: dict[str, str] = manifest["revisions"]
        stamp_only: dict[str, str] = manifest["stamp_only"]
        absorbed: list[str] = manifest["absorbed"]
    except (OSError, ValueError, KeyError) as e:
        return [f"{MANIFEST.name} is unreadable or malformed ({e!r}); refusing to guess. "]

    errors: list[str] = []

    for revision, path in _files_by_revision(files_by_id):
        slug = revision_slug(path)
        if revision in shipped:
            if shipped[revision] != slug:
                errors.append(
                    f"{path.name} books revision id {revision}, which deployed builds "
                    f'shipped as "{shipped[revision]}" — this file calls it "{slug}". '
                    f"Any DB already stamped {revision} skips these statements forever.\n{FIX}"
                )
            continue
        if revision in stamp_only:
            if revision not in absorbed:
                errors.append(
                    f"{path.name} books revision id {revision}, which this tree never "
                    f'shipped (a deployed build stamped it as "{stamp_only[revision]}"). '
                    f"Every DB stamped {revision} would skip this file. Only a revision "
                    f"whose content trunk already ships may be registered as an absorbed "
                    f'no-op node, in "{MANIFEST.name}" -> "absorbed".\n{FIX}'
                )
            elif stamp_only[revision] != slug:
                errors.append(
                    f"{path.name} is registered as the absorbed node {revision} but its "
                    f'slug is "{slug}" while the stamped build shipped "{stamp_only[revision]}"; '
                    f"an absorbed node must name the same migration.\n{FIX}"
                )

    for revision, slug in sorted(shipped.items()):
        if revision not in files_by_id:
            errors.append(
                f'{MANIFEST.name} says {revision} ("{slug}") shipped, but versions/ has no '
                f"file for it: a shipped migration was deleted or renamed, which leaves "
                f"every DB stamped {revision} unresolvable at boot.\n{FIX}"
            )

    for revision, slug in sorted(stamp_only.items()):
        if revision in absorbed and revision not in files_by_id:
            errors.append(
                f'{MANIFEST.name} registers {revision} ("{slug}") as an absorbed node, but '
                f"versions/ has no file for it: a database stamped {revision} still dies at "
                f"boot with \"Can't locate revision identified by '{revision}'\", which is "
                f"the outage the declaration exists to close. Re-add the no-op node or "
                f'drop it from "absorbed".\n{FIX}'
            )

    errors.extend(new_revision_run_errors(files_by_id, shipped, stamp_only, heads))
    return errors


def _files_by_revision(files_by_id: dict[str, list[Path]]):
    """Yield (id, path) for every file, so a duplicate id is checked per file."""
    return [(revision, path) for revision, paths in sorted(files_by_id.items()) for path in paths]


def new_revision_run_errors(
    files_by_id: dict[str, list[Path]],
    shipped: dict[str, str],
    stamp_only: dict[str, str],
    heads: list[str],
) -> list[str]:
    """Ids newer than the highest booked one must be one unbroken run, topped by the head.

    The booked ids are the ones `shipped_revisions.json` says a deployed build
    stamped; everything else in `versions/` is this tree's own new run. A branch
    that adds two migrations therefore books exactly the two ids above the highest
    booked one, chained, and leaves the higher of the two as the head — which is
    satisfiable for any number of migrations, unlike a rule that demands the
    single highest id for each file. A gap, a parallel series, or a run whose head
    is not its own top id means some database in the world already holds a
    different meaning for one of those ids.
    """
    booked = {int(revision) for revision in (*shipped, *stamp_only) if revision.isdigit()}
    new = {
        revision: paths[0]
        for revision, paths in files_by_id.items()
        if revision not in shipped and revision not in stamp_only
    }
    if not new:
        return []

    errors: list[str] = []
    ceiling = max(booked) if booked else 0
    for revision, path in sorted((r, p) for r, p in new.items() if not r.isdigit()):
        errors.append(
            f'{path.name} books "{revision}", which is not the numeric id this series uses '
            f"(the highest booked one is {ceiling:04d}).\n{FIX}"
        )
    run = sorted(int(revision) for revision in new if revision.isdigit())
    expected = list(range(ceiling + 1, ceiling + 1 + len(run)))
    if run != expected:
        free = ", ".join(f"{number:04d}" for number in expected if number not in run)
        errors.append(
            f"the ids newer than the highest booked one ({ceiling:04d}) must be the unbroken "
            f"run {_ids(expected)}, got {_ids(run)} — {free} would stay free for another branch "
            f"to book under the same number.\n{FIX}"
        )
    elif heads and heads != [f"{run[-1]:04d}"]:
        errors.append(
            f"the new revision run {_ids(run)} has to leave its highest id {run[-1]:04d} as "
            f"the only head (the graph has {', '.join(sorted(heads))}).\n{FIX}"
        )
    return errors


def _ids(numbers: list[int]) -> str:
    return ", ".join(f"{number:04d}" for number in numbers)


def migration_heads(config: Config | None = None) -> tuple[list[str], list[str]]:
    """The graph's heads, or the errors that stopped alembic computing them.

    `config` defaults to the repo's own `alembic.ini`, because the check has to
    resolve the same tree the boot-time upgrade does.
    """
    script = ScriptDirectory.from_config(config or Config("alembic.ini"))
    try:
        return list(script.get_heads()), []
    except Exception as e:
        # `get_heads` resolves the whole graph before it can answer, so anything
        # unresolvable lands here: a `down_revision` naming a revision no file
        # defines (`CommandError`), or two files claiming one id, which alembic
        # reports as `CycleDetected` after warning about the duplicate. Both have
        # to come back as a named error with the fix, not as a traceback — a
        # guard that crashes on its own headline case is not a guard.
        return [], [f"migration graph is broken: {e}\n{FIX}"]


def main() -> int:
    files_by_id = revision_files()
    heads, graph_errors = migration_heads()
    errors = (
        duplicate_errors(duplicate_revision_ids(files_by_id))
        + graph_errors
        + manifest_collisions(files_by_id, heads)
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    if len(heads) != 1:
        print(
            f"migration graph has {len(heads)} heads ({', '.join(sorted(heads))}); "
            f"every alembic upgrade will refuse to run.\n{FIX}",
            file=sys.stderr,
        )
        return 1
    print(f"migration graph ok: single head {heads[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
