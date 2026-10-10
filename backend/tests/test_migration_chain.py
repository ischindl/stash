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
from alembic.util.exc import CommandError

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
    `0214`–`0220`. A branch that takes an already-shipped id therefore gets its
    migration treated as applied — `upgrade head` skips its schema on every
    stamped DB, permanently, while CI stays green because the branch alone is
    self-consistent. That is how the founder DB ended up missing trunk's
    `0203`–`0207` (STAS-232).

    Keyed by `(id, slug)`: an id the manifest holds only passes with the very
    slug that was shipped. Ids a build stamped but this tree never shipped live
    in `stamp_only`, and each one has to end up with a node: an absorb node
    registered in `absorbed`, whose content trunk already ships. The stamp does not
    care how the tree chose to answer it — a stamp naming nothing is the same boot
    death whether `absorbed` forgot the id or the file went missing after it was
    declared — so both directions are policed. (`stamp_only` is empty today: the
    founder stamp this rule was written for was renumbered away in `3670aa97` and no
    database holding it survives, measured 2026-10-03. The rule stays because the
    next stranded stamp is a merge away, not because one is open now.) Ids
    newer than the highest booked one must form one unbroken run that ends at the head, so
    a branch cannot start a parallel series either. And `captured_at.head` has to name
    the head the graph actually has: a manifest silently left behind by the last
    landed migration stops describing the tree it guards. Those rules
    are imported from `check_heads` rather than restated here: the two guards have
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


def test_the_real_manifest_passes_every_guard_ci_applies():
    """The suite must not be greener than the CI check it mirrors.

    `check_heads.py` is what a PR gate runs, so the real tree and the real manifest
    have to pass *that* function, not a hand-restated approximation of it. Asserting
    through the same entry point is what keeps the two guards from drifting: the
    stale-`captured_at` rot this card exists to close sat on `main` unnoticed
    precisely because nothing here called the guard on the real tree.
    """
    errors = check_heads.manifest_collisions(
        check_heads.revision_files(), list(_script().get_heads())
    )
    assert not errors, "the real manifest fails its own guard:\n" + "\n".join(errors)


def _booked_ceiling(manifest: dict) -> int:
    """The highest revision id a deployed build already holds a claim on."""
    booked = {int(revision) for revision in (*manifest["revisions"], *manifest["stamp_only"])}
    return max(booked)


def test_each_manifest_rule_fires_on_the_mistake_it_exists_for(tmp_path, monkeypatch):
    """A guard nobody has seen red is decoration.

    Each rule above is pointed at a synthetic tree that makes exactly the mistake it
    exists to catch: an id re-booked from a stamped build (the STAS-232 collision
    class), an absorb node that was never registered, a mis-named absorb node, an
    absorb node the tree does not actually ship, a stamped id the tree never absorbs
    at all, a manifest entry whose file is gone, a manifest whose `captured_at.head`
    names something other than the graph's head,
    and numbering that leaves a gap, forks into a
    parallel series, or strands its head. The numbers come from the real manifest so
    they stay honest as it grows; two chained migrations must stay green, because a
    branch is allowed to add more than one. The stamped-but-unshipped id is invented
    rather than read from `stamp_only`, which is legitimately empty now that no
    database holds such a stamp: a rule that can only be shown red while an
    outstanding incident happens to be open is not a tested rule.
    """
    manifest = _shipped_manifest()
    shipped, stamp_only = manifest["revisions"], manifest["stamp_only"]
    ceiling = _booked_ceiling(manifest)
    first, second, forked = (f"{ceiling + offset:04d}" for offset in (1, 2, 3))
    stamped_id, stamped_slug = f"{ceiling + 4:04d}", "a_migration_no_build_shipped"

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

    use_manifest(stamp_only={stamped_id: stamped_slug}, absorbed=[])
    unregistered = collide({stamped_id: [Path(f"{stamped_id}_anything.py")]})
    assert any("never" in error and "shipped" in error for error in unregistered), unregistered

    use_manifest(stamp_only={stamped_id: stamped_slug}, absorbed=[stamped_id])
    misnamed = collide({stamped_id: [Path(f"{stamped_id}_renamed.py")]})
    assert any("absorbed node" in error for error in misnamed), misnamed

    declared_but_absent = [error for error in collide({}) if stamped_id in error]
    assert any(
        "absorbed node" in error and "no file for it" in error for error in declared_but_absent
    ), declared_but_absent

    declared_and_shipped = collide({stamped_id: [Path(f"{stamped_id}_{stamped_slug}.py")]})
    assert not [error for error in declared_and_shipped if stamped_id in error], (
        declared_and_shipped
    )

    # The same stranded stamp with the declaration missing entirely: the database
    # dies at boot exactly as loudly, so the guard has to be as loud.
    use_manifest(stamp_only={stamped_id: stamped_slug}, absorbed=[])
    stranded_stamp = collide({})
    assert any(
        "Can't locate revision" in error for error in stranded_stamp if stamped_id in error
    ), stranded_stamp

    use_manifest(stamp_only=stamp_only, captured_at={**manifest["captured_at"], "head": "0001"})
    stale_capture = collide({first: [Path(f"{first}_one.py")]}, [first])
    assert any(
        "captured_at" in error and "0001" in error and first in error for error in stale_capture
    ), stale_capture

    use_manifest(stamp_only=stamp_only)
    named_head = manifest["captured_at"]["head"]
    correct_capture = collide({first: [Path(f"{first}_one.py")]}, [named_head])
    assert not any("captured_at" in error for error in correct_capture), correct_capture

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


def test_a_stamp_only_id_with_no_node_is_red(tmp_path):
    """Every id recorded as stamped has to resolve, whether or not it was declared.

    A database whose `alembic_version` holds an id this tree ships no file for dies on
    every boot with `Can't locate revision identified by '<id>'` — the STAS-232
    outage. `stamp_only` is exactly the list of such ids, so an entry there is a
    promise the tree answers the stamp with a node. The original rule only reddened a
    missing node for ids already registered in `absorbed`, which is the shape a
    *forgotten* declaration never arrives in: a stranded stamp is recorded in
    `stamp_only` first and registered later, if at all, so the undeclared case is the
    one that walks through the blind spot. The two repairs are different, so the two
    messages stay textually distinct.
    """
    stamped = f"{_booked_ceiling(_shipped_manifest()) + 1:04d}"
    stamp_only = {stamped: "a_migration_no_build_shipped"}
    node = tmp_path / f"{stamped}_{stamp_only[stamped]}.py"

    unregistered = check_heads.stamp_only_node_errors(stamp_only, [], {})
    assert any("no node" in error for error in unregistered), unregistered
    assert any(
        f"Can't locate revision identified by '{stamped}'" in error for error in unregistered
    ), unregistered

    registered = check_heads.stamp_only_node_errors(stamp_only, [stamped], {})
    assert any("absorbed node" in error and "no file for it" in error for error in registered), (
        registered
    )
    assert not any("no node" in error for error in registered), registered

    assert not check_heads.stamp_only_node_errors(stamp_only, [], {stamped: [node]})
    assert not check_heads.stamp_only_node_errors(stamp_only, [stamped], {stamped: [node]})


def test_captured_at_head_must_be_the_tree_head():
    """The manifest cannot be allowed to describe an older tree and read as current.

    Every other rule asks a question about ids, so a manifest left behind by the last
    landed migration passes them all while claiming a head that is not the graph's:
    that is how `main` sat with `captured_at` naming a head five revisions back, and
    nothing in CI or the suite noticed. The repair is stated in the error because the
    fix is a booking update, not a renumber, so the guard's usual `FIX` prose would
    send a reviewer to the wrong file.

    The carve-outs are load-bearing: when the graph itself has no head or more than
    one, this rule has no opinion, because `migration_heads` and `main`'s head count
    already own that failure and every synthetic tree in the rule-fires test calls in
    with no heads at all.
    """
    manifest = _shipped_manifest()
    (head,) = _script().get_heads()
    stale = f"{int(head) - 5:04d}"

    def captured_as(value):
        captured = {**manifest["captured_at"], **value}
        return {**manifest, "captured_at": captured}

    mismatch = check_heads.captured_at_errors(captured_as({"head": stale}), [head])
    assert any(stale in error and head in error for error in mismatch), mismatch

    assert not check_heads.captured_at_errors(captured_as({"head": head}), [head])

    for label, broken in (
        ("absent", {key: value for key, value in manifest.items() if key != "captured_at"}),
        ("empty", captured_as({"head": ""})),
    ):
        errors = check_heads.captured_at_errors(broken, [head])
        assert any("captured_at" in error for error in errors), f"{label}: {errors}"

    stale_manifest = captured_as({"head": stale})
    assert not check_heads.captured_at_errors(stale_manifest, []), (
        "an unresolvable graph is the graph rule's to report, not this one's"
    )
    forked = [head, f"{int(head) + 1:04d}"]
    assert not check_heads.captured_at_errors(stale_manifest, forked), (
        "a forked graph is main's head-count check to report, not this one's"
    )


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


def test_every_booked_revision_resolves_as_an_ancestor_of_head():
    """A database's stamp must resolve through alembic, not just through a regex.

    The founder build left its database stamped with an id this tree shipped no file
    for, and every boot of the new chain died in `alembic upgrade head` with
    `Can't locate revision identified by '0209'` — STAS-232. So every revision id the
    manifest books has to resolve through alembic and sit on the path to `head`,
    which is exactly what alembic itself demands at boot. The ids are read from the
    manifest rather than named here: hardcoding them is how this test went stale at
    the `3670aa97` renumber, when `0209`/`0217`/`0218` became three unrelated
    migrations while the story in its docstring stayed true of different numbers.

    An id alembic cannot resolve is reported as the boot failure it causes rather than
    as a traceback, because that traceback is what the operator would see and this test
    exists to spare them it.
    """
    script = _script()
    (head,) = script.get_heads()
    ancestors = {revision.revision for revision in script.iterate_revisions(head, "base")}
    manifest = _shipped_manifest()
    booked = sorted({*manifest["revisions"], *manifest["stamp_only"], *manifest["absorbed"]})
    assert booked, f"{MANIFEST.name} books no revision at all, which is not a guard"

    unresolvable = []
    for revision in booked:
        try:
            resolved = script.get_revision(revision)
        except (CommandError, KeyError) as exc:
            # Two shapes, one outcome: a never-shipped id raises alembic's own boot
            # error, while an id whose file vanished but is still somebody's parent
            # raises a raw KeyError out of the revision map.
            unresolvable.append(
                f"{revision} is booked in {MANIFEST.name} but alembic cannot resolve it ({exc}), "
                f"so a database stamped {revision} dies on every boot with \"Can't locate "
                f"revision identified by '{revision}'\""
            )
            continue
        assert resolved.revision == revision
        if revision not in ancestors:
            unresolvable.append(f"{revision} is booked but is not an ancestor of head {head}")
    assert not unresolvable, "\n".join(unresolvable)
