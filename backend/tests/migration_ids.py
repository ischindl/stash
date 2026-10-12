"""Resolve a migration by what it does, not by the number it happens to carry.

A test that names a migration by its id breaks the moment the chain is renumbered,
and the breakage is not uniform: an import of a vanished module fails loudly, while
a revision id used as an `alembic upgrade` stamp keeps resolving against whatever
file now holds that number, so the test silently migrates to the wrong place and
still passes. Both had happened here — two tests imported modules under ids the
renumber moved, and a third pinned the stamp before its migration by a number that
now names an unrelated revision.

So the slug is the identity a test should state, and the id is looked up. The
lookup goes through alembic's own `ScriptDirectory` rather than a filename glob, so
it sees the graph the boot-time upgrade sees, and it refuses a slug matching zero
or more than one migration: a renumbered or mistyped slug and a slug collision are
both failures of the test's premise, and guessing which file was meant would put
the wrong data in front of the assertion.
"""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MODULE_ROOT = "backend.migrations.versions"


@lru_cache(maxsize=1)
def _scripts_by_slug() -> dict[str, list]:
    """Every migration in the graph, bucketed by the descriptive half of its filename."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    script = ScriptDirectory.from_config(config)
    bucketed: dict[str, list] = {}
    for revision in script.walk_revisions():
        slug = Path(revision.path).stem.split("_", 1)[1]
        bucketed.setdefault(slug, []).append(revision)
    return bucketed


def _one_script(slug: str):
    matches = _scripts_by_slug().get(slug, [])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(
            f"versions/ has no migration whose slug is {slug!r}. Either the file was "
            f"renamed or the chain was renumbered since this test was written, or the "
            f"graph does not reach it at all — check `python -m alembic heads` and the "
            f"filenames in backend/migrations/versions/."
        )
    ids = ", ".join(sorted(revision.revision for revision in matches))
    raise ValueError(
        f"two migrations share the slug {slug!r} (ids {ids}), so the slug does not "
        f"identify one migration. A renumbered chain copied in under a name trunk "
        f"already uses is the collision the shipped-revision guard exists to stop — "
        f"rename one of the two files."
    )


def down_revision_of_slug(slug: str) -> str:
    """The revision id one migration is parented on — the state before it ran.

    A data-migration test needs exactly this to build the precondition: upgrade to
    the parent, seed the old shape, then run the migration under test.
    """
    down = _one_script(slug).down_revision
    if not isinstance(down, str):
        raise ValueError(
            f"{_one_script(slug).revision} is not parented on a single revision "
            f"(it is a base or a merge point), so there is no one stamp to seed against."
        )
    return down


def module_for_slug(slug: str) -> str:
    """The importable dotted path of the one migration whose filename ends in `slug`."""
    return f"{MODULE_ROOT}.{Path(_one_script(slug).path).stem}"
