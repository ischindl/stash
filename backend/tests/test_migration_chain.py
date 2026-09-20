"""The migration graph must have exactly one head.

Two PRs merged the same day each added a `0174`, both parented on `0173`.
Alembic could not resolve the graph, so `alembic upgrade head` — which
`database.py` runs at boot — failed, and the backend exited with status 3 on
every deploy. Prod sat on the previous release until someone noticed.

Nothing in the test suite caught it because each PR's migrations were fine in
isolation; only the merge was broken. So this asserts a property of the
directory rather than of any one file.
"""

import json
import re
from collections import Counter
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.migrations import check_heads

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
VERSIONS = MIGRATIONS / "versions"
MANIFEST = MIGRATIONS / "shipped_revisions.json"
_REVISION = re.compile(r'^revision = "([^"]+)"', re.M)
_DOWN = re.compile(r'^down_revision = (?:"([^"]+)"|None)', re.M)


def _script() -> ScriptDirectory:
    """The migration graph as alembic itself resolves it, from any working directory.

    `alembic.ini` keeps `script_location` relative, so it is set absolutely here —
    otherwise this reads a different tree than the boot-time upgrade does.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return ScriptDirectory.from_config(config)


def _graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        text = path.read_text()
        rev = _REVISION.search(text)
        if not rev:
            continue
        down = _DOWN.search(text)
        graph[rev.group(1)] = down.group(1) if down else None
    return graph


def test_no_duplicate_revision_ids():
    """Two files claiming the same revision is the failure mode that took
    prod down; alembic only warns about it, then errors later."""
    revisions = []
    for path in VERSIONS.glob("*.py"):
        match = _REVISION.search(path.read_text())
        if match:
            revisions.append(match.group(1))

    duplicates = [rev for rev, count in Counter(revisions).items() if count > 1]
    assert not duplicates, (
        f"revision id(s) used by more than one migration: {duplicates}. "
        "Two branches picked the same number — renumber the later one and "
        "re-chain its down_revision."
    )


def test_exactly_one_head():
    """A head is a revision nothing points at. More than one means the graph
    forked and `upgrade head` is ambiguous."""
    graph = _graph()
    parents = {down for down in graph.values() if down}
    heads = sorted(set(graph) - parents)
    assert len(heads) == 1, f"expected one head, found {heads}"


def test_every_parent_exists():
    """A down_revision pointing at a deleted or renamed migration breaks the
    chain just as hard, and is the easy mistake when renumbering."""
    graph = _graph()
    missing = {rev: down for rev, down in graph.items() if down is not None and down not in graph}
    assert not missing, f"down_revision points at a revision that doesn't exist: {missing}"


def _shipped_manifest() -> dict:
    """The frozen (id, slug) manifest of every revision a deployed build stamped.

    Deliberately not a skip: a missing or malformed manifest means the guard
    below cannot answer the question it exists to answer, so it must be red.
    """
    assert MANIFEST.exists(), (
        f"{MANIFEST.name} is missing from backend/migrations/. It freezes the "
        "revision ids deployed builds already stamped, which is the only thing "
        "that can catch a new migration re-booking one of them (see "
        "test_migration_ids_disjoint_from_stamped_builds)."
    )
    return json.loads(MANIFEST.read_text())


def test_migration_ids_disjoint_from_stamped_builds():
    """No file in `versions/` may book an id a deployed build already stamped.

    A DB stamped `0206` cannot say *which* `0206` it ran: the founder (dogfood)
    image shipped `0206` as `dogfood_lineage_reconcile` while trunk's `0206` is
    `source_sync_claims`, and its `0203`–`0209` are re-numbered copies of trunk's
    `0210`–`0216`. A branch that takes an already-shipped id therefore gets its
    migration treated as applied — `upgrade head` skips its schema on every
    stamped DB, permanently, while CI stays green because the branch alone is
    self-consistent. That is how the founder DB ended up missing trunk's
    `0203`–`0207` (STAS-232).

    Keyed by `(id, slug)`: an id the manifest holds only passes with the very
    slug that was shipped. Ids a build stamped but this tree never shipped live
    in `stamp_only` and may gain a file only once registered as an absorbed
    no-op node in `absorbed` — `0209`, whose content trunk already ships as
    `0216`. The declaration is checked in both directions: a registered absorb node
    whose file is gone is red too, because that stamp still resolves to nothing. Ids
    newer than the highest booked one must form one unbroken run that ends at the head, so
    a branch cannot start a parallel series either. That rule
    is imported from `check_heads` rather than restated here: the two guards have
    to say the same thing in the same words, or CI and the suite drift apart.
    """
    manifest = _shipped_manifest()
    shipped = manifest["revisions"]
    stamp_only = manifest["stamp_only"]
    absorbed = set(manifest["absorbed"])

    rebooked, unregistered, misnamed, stranded, stranded_nodes = [], [], [], [], []
    for path in sorted(VERSIONS.glob("*.py")):
        match = _REVISION.search(path.read_text())
        if not match:
            continue
        revision, slug = match.group(1), path.stem.split("_", 1)[1]
        if revision in shipped:
            if shipped[revision] != slug:
                rebooked.append(f'{path.name}: {revision} was shipped as "{shipped[revision]}"')
        elif revision in stamp_only:
            if revision not in absorbed:
                unregistered.append(f'{path.name}: {revision} ("{stamp_only[revision]}")')
            elif stamp_only[revision] != slug:
                misnamed.append(
                    f'{path.name}: {revision} is "{slug}", stamped as "{stamp_only[revision]}"'
                )

    for revision, slug in sorted(shipped.items()):
        if revision not in _graph():
            stranded.append(f'{revision} ("{slug}")')

    for revision in sorted(absorbed):
        if revision in stamp_only and revision not in _graph():
            stranded_nodes.append(f'{revision} ("{stamp_only[revision]}")')

    assert not rebooked, (
        "migration file(s) re-book a revision id a deployed build already shipped, "
        "so stamped DBs would skip them forever:\n" + "\n".join(rebooked)
    )
    assert not unregistered, (
        "migration file(s) book an id a deployed build stamped but this tree never "
        "shipped; register it as an absorbed no-op node in "
        f'{MANIFEST.name} -> "absorbed" only if trunk already ships its content:\n'
        + "\n".join(unregistered)
    )
    assert not misnamed, (
        "absorbed node(s) must name the migration the build shipped:\n" + "\n".join(misnamed)
    )
    assert not stranded, (
        f"{MANIFEST.name} holds revision id(s) with no migration file — a shipped "
        "migration was deleted or renamed, which strands every DB stamped at it:\n"
        + "\n".join(stranded)
    )
    assert not stranded_nodes, (
        f"{MANIFEST.name} declares absorbed node(s) that versions/ does not ship, so a "
        "database stamped at them still cannot resolve its own stamp at boot:\n"
        + "\n".join(stranded_nodes)
    )

    run_errors = check_heads.new_revision_run_errors(
        check_heads.revision_files(), shipped, stamp_only, _script().get_heads()
    )
    assert not run_errors, "revision numbering is not one contiguous run:\n" + "\n".join(run_errors)


def _booked_ceiling(manifest: dict) -> int:
    """The highest revision id a deployed build already holds a claim on."""
    booked = {int(revision) for revision in (*manifest["revisions"], *manifest["stamp_only"])}
    return max(booked)


def test_each_manifest_rule_fires_on_the_mistake_it_exists_for(tmp_path, monkeypatch):
    """A guard nobody has seen red is decoration.

    Each rule above is pointed at a synthetic tree that makes exactly the mistake it
    exists to catch: an id re-booked from a stamped build (the STAS-232 collision
    class), an absorb node that was never registered, a mis-named absorb node, an
    absorb node the tree does not actually ship, a manifest entry whose file is gone,
    and numbering that leaves a gap, forks into a
    parallel series, or strands its head. The numbers come from the real manifest so
    they stay honest as it grows; two chained migrations must stay green, because a
    branch is allowed to add more than one.
    """
    manifest = _shipped_manifest()
    shipped, stamp_only = manifest["revisions"], manifest["stamp_only"]
    ceiling = _booked_ceiling(manifest)
    first, second, forked = (f"{ceiling + offset:04d}" for offset in (1, 2, 3))

    def use_manifest(**overrides):
        path = tmp_path / "shipped_revisions.json"
        path.write_text(json.dumps({**manifest, **overrides}))
        monkeypatch.setattr(check_heads, "MANIFEST", path)

    def collide(tree, heads=()):
        return check_heads.manifest_collisions(tree, list(heads))

    def run(tree, heads):
        return check_heads.new_revision_run_errors(tree, shipped, stamp_only, heads)

    use_manifest()
    a_shipped_id, shipped_slug = sorted(shipped.items())[0]
    rebooked = collide({a_shipped_id: [Path(f"{a_shipped_id}_other_thing.py")]})
    assert any(f'shipped as "{shipped_slug}"' in error for error in rebooked), rebooked

    use_manifest(absorbed=[])
    stamped_id = sorted(stamp_only)[0]
    unregistered = collide({stamped_id: [Path(f"{stamped_id}_anything.py")]})
    assert any("never" in error and "shipped" in error for error in unregistered), unregistered

    use_manifest(absorbed=[stamped_id])
    misnamed = collide({stamped_id: [Path(f"{stamped_id}_renamed.py")]})
    assert any("absorbed node" in error for error in misnamed), misnamed

    declared_but_absent = [error for error in collide({}) if stamped_id in error]
    assert any(
        "absorbed node" in error and "no file for it" in error for error in declared_but_absent
    ), declared_but_absent

    use_manifest(revisions={**shipped, "9999": "a_deleted_migration"})
    stranded = collide({})
    assert any("no" in error and "file for it" in error for error in stranded), stranded

    use_manifest()
    gapped = run({second: [Path(f"{second}_skips_the_free_id.py")]}, [second])
    assert any("unbroken" in error for error in gapped), gapped
    assert not run({first: [Path(f"{first}_only.py")]}, [first]), "one migration is the common case"
    fork = run(
        {first: [Path(f"{first}_a.py")], forked: [Path(f"{forked}_b.py")]},
        [forked],
    )
    assert any("unbroken" in error for error in fork), fork
    chained = {first: [Path(f"{first}_a.py")], second: [Path(f"{second}_b.py")]}
    assert not run(chained, [second]), "two chained migrations on one branch must be legal"
    stranded_head = run(chained, [first])
    assert any("only head" in error for error in stranded_head), stranded_head

    duplicates = check_heads.duplicate_errors(
        check_heads.duplicate_revision_ids(
            {first: [Path(f"{first}_one.py"), Path(f"{first}_two.py")]}
        )
    )
    assert len(duplicates) == 1 and first in duplicates[0], duplicates


def test_an_unresolvable_graph_is_reported_rather_than_raised(tmp_path):
    """A graph alembic cannot resolve has to come back as an error, not a traceback.

    `get_heads` builds the whole revision map before it can answer, so a cycle — which
    is what two files claiming one id resolves into, the near-miss STAS-232 produced
    when its convergence node and main's `0217` both booked `0217` — and a
    `down_revision` naming a revision no file defines both surface here. The CI check
    used to catch only one of them, so its headline case died with a stack trace that
    tells a reviewer nothing about what to renumber.
    """

    def broken_graph(name: str, *files: tuple[str, str, str]) -> Config:
        """A throwaway `versions/` whose down_revision is given as python source."""
        root = tmp_path / name
        versions = root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        for filename, revision, down in files:
            (versions / filename).write_text(f'revision = "{revision}"\ndown_revision = {down}\n')
        config = Config()
        config.set_main_option("script_location", str(root))
        return config

    cycle = broken_graph("cycle", ("0002_a.py", "0002", '"0003"'), ("0003_b.py", "0003", '"0002"'))
    dangling = broken_graph("dangling", ("0002_second.py", "0002", '"0001"'))

    for label, config in (("cycle", cycle), ("missing parent", dangling)):
        heads, errors = check_heads.migration_heads(config)
        assert heads == [], f"{label}: expected no computable head"
        assert len(errors) == 1, f"{label}: expected one clean error, got {errors}"
        assert check_heads.FIX in errors[0], f"{label}: error has to carry the fix: {errors[0]}"


def test_every_stamped_revision_resolves_as_an_ancestor_of_head():
    """A database's stamp must resolve through alembic, not just through a regex.

    The founder build left its database stamped `0209`, an id this tree shipped no
    file for, and every boot of the new chain died in `alembic upgrade head` with
    `Can't locate revision identified by '0209'` — STAS-232. The ids that build's
    stamp and its absorb node can name therefore have to be resolvable and on the
    path to `head`, which is what alembic itself checks at boot: `0209` only
    converges if `0217` (its parent) and `0218` (the node that owes it trunk's
    `0203`-`0207`) both resolve too.
    """
    script = _script()
    (head,) = script.get_heads()
    ancestors = {revision.revision for revision in script.iterate_revisions(head, "base")}

    for revision in ("0209", "0217", "0218"):
        assert script.get_revision(revision).revision == revision
        assert revision in ancestors, f"{revision} is not an ancestor of head {head}"
