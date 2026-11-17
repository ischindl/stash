# Trunk publish and founder deploy

How this repository publishes its trunk and how our own stack gets a build of it.
Nothing here changes the hosted path: joinstash.ai still deploys from `main` on
Render, described in the Deployment section of `CLAUDE.md`. This document covers the
two things that path does not cover — keeping the fork a mirror, and moving the
founder stack.

## The three refs

| Ref | What it is | Who moves it |
| --- | --- | --- |
| `refs/heads/main` | The line. Every merged card lands here. | The card's own review-and-merge flow |
| `refs/heads/deploy/main` | The commit whose image pair is live on the founder stack. Always an ancestor of `main`. | Only after the pair built from it is proven and verified live |
| `fork/main`, `fork/deploy/main` | Mirrors of the two above. | `scripts/publish-trunk.sh`, nothing else |

The fork is a mirror and the source of pull requests. It is not a line of its own.
`deploy/main` is not a branch you can deploy from directly: it names a commit that is
already in `main`, so a deploy is always a deploy of trunk content, never of a lane.

## Publish: run the script, it is not a ritual

After any merge to `main`:

```bash
scripts/publish-trunk.sh
```

It fast-forwards `refs/heads/main` and `refs/heads/deploy/main` to the `fork` remote
by literal refspec, reads both back, and exits non-zero unless the read-back equals the
local sha. Its last line pastes straight into a task log:

```
measured: main local=<sha> fork=<sha> deploy/main local=<sha> fork=<sha> status=0
```

Three rules the script enforces by construction:

- **Upstream is never pushed to.** `ischindl` holds `pull: true, push: false` on
  `Fergana-Labs/stash`, so a branch could not reach upstream even if we wanted it to,
  and a pull request is the only route. Pull requests are opened by the operator on
  request, per the operator-gated PR clause in `CLAUDE.md`.
- **Tags never leave this machine.** `publish.yml` fires on a push to `main` and
  publishes `stashai` to PyPI when `pyproject.toml` carries an untagged version, and
  `docker-publish.yml` builds the release images from a `v*` tag. The hosted release
  line owns tags; the mirror script has no `--tags`, no `--all`, and no force. After a
  mirror push, check that the fork produced no release event:
  `gh run list -R ischindl/stash --limit 5`.
- **A divergence stops instead of resolving.** If the fork's `main` holds commits this
  machine does not, the script prints exactly what a non-fast-forward would drop,
  prints `OPERATOR ACTION REQUIRED`, and pushes nothing. Mirroring over someone else's
  work is not a flag.

A bare `git push` on `main` targets **upstream**: `.git/config` maps
`branch.main.remote = origin` with `rebase = true`, while several task branches map to
`fork`. Always name the remote and the refspec.

## Prove the artifact, not the card

A release is proven by counting paths inside the two files a user can actually download, not by
what a card's merge state asserts. Read the version PyPI itself reports, then fetch that release
**twice**: `pip download` saves only the wheel unless you ask for the other one, so the sdist
needs its own run with `--no-binary :all:`. Count the payload you meant to ship, and count a
second agent tree beside it as a control — zero for one agent beside a nonzero for another says
that agent's payload is missing, while zero beside zero says no assets ship at all, and those two
need different fixes. Every count below reads a listing file that a successful listing renamed
into place, and `ls -l` names both downloaded artifacts before anything is counted — a `0`
printed from a pipeline whose `tar` or `zipfile` died reading its file looks exactly like a
measurement and is not one.

```bash
V="$(python3 -c "import json,urllib.request as u; print(json.load(u.urlopen('https://pypi.org/pypi/stashai/json', timeout=30))['info']['version'])")"
C=/tmp/census
mkdir -p "$C"
python3 -m pip download "stashai==$V" --no-deps --no-cache-dir -d "$C"
python3 -m pip download "stashai==$V" --no-deps --no-cache-dir --no-binary :all: -d "$C"
ls -l "$C/stashai-$V-py3-none-any.whl" "$C/stashai-$V.tar.gz"
python3 -m zipfile -l "$C/stashai-$V-py3-none-any.whl" > "$C/wheel.part" && mv "$C/wheel.part" "$C/wheel.txt"
tar -tzf "$C/stashai-$V.tar.gz" > "$C/sdist.part" && mv "$C/sdist.part" "$C/sdist.txt"
grep -c "stashai/plugin/assets/pi/" "$C/wheel.txt" "$C/sdist.txt"
grep -c "stashai/plugin/assets/claude/" "$C/wheel.txt" "$C/sdist.txt"
```

Two rules, and only two:

- **A release is proven by a path count inside the downloaded wheel and sdist.** A build of
  your own tree proves only your tree, `tar` lists directory members as well so an sdist can
  out-count a wheel that carries the same payload, and a number printed after a listing command
  failed to open its file is not a count — the `ls -l` line and the rename-into-place behind each
  listing are what make that failure loud instead of silent.
- **`mergeConfirmed` or `mergeDetails.commitSha` is never delivery evidence.** STAS-478 closed
  with `mergeConfirmed` true on a commit belonging to another card, while the published wheel
  carried zero paths of the payload that card claimed had shipped.

## The founder stack

`docker-compose.founder.yml` is the fourth `-f` overlay and the only record of what our
stack runs. It names one commit for every lane that `docker-compose.prod.yml` pins to
the backend image, plus the frontend, and
`backend/tests/test_founder_deploy_pin.py` fails the suite when those tags name
different commits or when a newly added queue is left out. Self-hosters never use this
file; the published files stay pull-only, which the same test guards.

The command the stack is composed with, confirmed by `com.docker.compose.project.config_files`
on the running containers:

```bash
BASE_IMAGE=stash-backend:main-<sha8>-pi docker compose -p stash \
  --project-directory /home/schindler/git/stash \
  -f docker-compose.prod.yml -f docker-compose.local.yml \
  -f docker-compose.dogfood.yml -f docker-compose.founder.yml \
  <command>
```

`-p stash` matches the existing project. `--project-directory` keeps the primary
checkout's `.env` as the secret source while the compose files come from the commit
being deployed, which is why no step in this runbook has to move a checkout.
`BASE_IMAGE` must be exported even though nothing builds here:
`docker-compose.dogfood.yml` declares it as a required interpolation with no default,
and compose resolves interpolations for every command including `config`.

Lanes this stack actually runs today: `backend`, `worker`, `heavy-worker`, `beat`,
`frontend`. `sync-worker` is pinned by the founder file so it
can never come up on a different commit, but it has no container: do not start
`sync-worker` casually, it has a backlog measured in hundreds of tasks.

## Build the pair, from the commit and nothing else

The pair is `stash-backend:main-<sha8>-base` (pi-free), `stash-backend:main-<sha8>-pi`
(the dogfood overlay the founder stack runs), and `stash-frontend:main-<sha8>`. Build
them from a tree detached at exactly that commit, never from a mutable working tree and
never one lane alone:

```bash
SHA=$(git rev-parse refs/heads/deploy/main)   # or the trunk commit you are proving
SHA8=$(git rev-parse --short=8 "$SHA")
git worktree add --detach .fusion/worktrees/trunk-build "$SHA"
cd .fusion/worktrees/trunk-build

docker build -f backend/Dockerfile -t "stash-backend:main-${SHA8}-base" .
docker build -f backend/Dockerfile.dogfood \
  --build-arg BASE_IMAGE="stash-backend:main-${SHA8}-base" \
  -t "stash-backend:main-${SHA8}-pi" .
docker build --build-arg BACKEND_INTERNAL_URL=http://backend:3456 \
  -t "stash-frontend:main-${SHA8}" frontend/

cd - && git worktree remove .fusion/worktrees/trunk-build
docker images | grep "main-${SHA8}"
```

A bare `docker build` of the dogfood overlay needs `--build-arg`; the
`BASE_IMAGE=... docker build` form works only through compose's `args:` and fails with
`base name (${BASE_IMAGE}) should not be blank`.

## Prove it before the founder touches it

The founder database holds the founder's real data. Prove the candidate against a copy:

1. Dump to disk, never `/tmp` (this box has an inode-exhaustion history and the dump
   was 1.96 GB once and 4.6 GB after):
   `docker exec stash-postgres-1 pg_dump -U stash -Fc stash > <somewhere-with-space>.dump`.
   Check the size and `pg_restore --list` before trusting it.
2. Restore into a pgvector container this deploy owns, on a port you probed:
   `docker run -d --name deploy-proof-pg -e POSTGRES_USER=stash -e POSTGRES_PASSWORD=proof \
   -e POSTGRES_DB=stash -p 127.0.0.1:<free-port>:5432 pgvector/pgvector:pg16`, then
   `pg_restore -j 4 -U stash -d stash <file>.dump` (last measured: 626 s, rc 0).
3. Boot the candidate against it. `docker network create` fails on this host ("all
   predefined address pools have been fully subnetted") and ports published to
   `127.0.0.1` are unreachable container to container, so use the copy's bridge IP:

```bash
DB_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' deploy-proof-pg)
docker run --rm -p <proof-port>:3456 \
  -e DATABASE_URL="postgresql://stash:proof@${DB_IP}:5432/stash" \
  -e REDIS_URL=redis://redis:6379/0 \
  "stash-backend:main-${SHA8}-pi"
```

4. Diff the content, in both directions, before you trust any stamp. This is required
   whenever `python backend/migrations/check_heads.py` prints a head other than the one the
   last proof was taken against, and it is required because the founder database's
   `alembic_version` row is not a position in the trunk chain (see *The founder database is
   an island*). Build two goldens from empty in databases this proof owns — the island's own
   number, and head — then diff every table, column, index and named constraint. Put the
   dump, the goldens and the copy on a filesystem with room, and take that reading at the
   moment of the proof rather than from this file: `df -h / /home /data /data2` names the four
   independent ext4 devices under those mounts — `/home` is its own disk, not part of `/`, so
   one reading says nothing about the other — and prints what each holds now. Measured at
   20:19Z on 2026-11-14 they were 35 GB, 21 GB, 161 GB and 290 GB free, and a 4.5 GB dump plus
   an 11 GB restored database fits on neither of the first two:

```bash
INV="select 'T:'||tablename from pg_tables where schemaname='public'
 union all select 'C:'||table_name||'.'||column_name||'|'||data_type||'|'||is_nullable
   from information_schema.columns where table_schema='public'
 union all select 'I:'||indexname from pg_indexes where schemaname='public'
 union all select 'K:'||conname from pg_constraint where connamespace='public'::regnamespace
 order by 1"
docker exec <proof-pg> psql -U stash -tA -d <db> -c "CREATE EXTENSION IF NOT EXISTS vector"
DATABASE_URL=postgresql://stash:proof@127.0.0.1:<proof-port>/<db> python -m alembic upgrade <stamp>   # golden@<stamp>
DATABASE_URL=postgresql://stash:proof@127.0.0.1:<proof-port>/<db2> python -m alembic upgrade head     # golden@head
for d in golden_at_stamp golden_at_head island_copy; do
  docker exec <proof-pg> psql -U stash -tA -d "$d" -c "$INV" | sort -u > "inv-$d.txt"; done
echo '--- golden@stamp has, island lacks  (anything here means the base is too high)'
comm -23 inv-golden_at_stamp.txt inv-island_copy.txt
echo '--- island has, golden@head lacks   (anything here means trunk retired it: STOP)'
comm -13 inv-golden_at_head.txt inv-island_copy.txt
```

   Two named misses at the base are expected and explained, not defects: the island's own
   fork work dropped `one_curator_per_user_per_wiki`, and the `hnsw` index
   `idx_history_events_embedding` comes only from `0001`, so nothing above the stamp will
   ever recreate it. Anything else in that first list means the base you picked is too high
   — lower it and measure again. A second read that settles a claim rather than a name is
   `pg_get_constraintdef` / `indexdef` on the objects in question: identical spellings are
   what make an island-held object safe to converge over.

Require: `/health` answers `{"status":"ok"}`, the expected `Running upgrade <from> ->
<to>` lines appear (or none, if the stamp already matches), and zero
`error|traceback|exception` lines in the log. **A red proof means no cutover** — and a red
content diff means no cutover too, on exactly the same footing as a red boot, because the
diff is what sees the schema the stamp is lying about. Read
the migration graph before reading the failure: `python backend/migrations/check_heads.py`
reports the tree's single head, and `select version_num from alembic_version` in the
copy reports where the data actually is.

## Cutover

Set both hexes in `docker-compose.founder.yml` (one commit for all seven lanes), then:

```bash
BASE_IMAGE=stash-backend:main-<sha8>-pi docker compose -p stash \
  --project-directory /home/schindler/git/stash \
  -f docker-compose.prod.yml -f docker-compose.local.yml \
  -f docker-compose.dogfood.yml -f docker-compose.founder.yml \
  config --quiet && echo config-ok

BASE_IMAGE=stash-backend:main-<sha8>-pi docker compose -p stash \
  --project-directory /home/schindler/git/stash \
  -f docker-compose.prod.yml -f docker-compose.local.yml \
  -f docker-compose.dogfood.yml -f docker-compose.founder.yml \
  config | grep 'image:'

BASE_IMAGE=stash-backend:main-<sha8>-pi docker compose -p stash \
  --project-directory /home/schindler/git/stash \
  -f docker-compose.prod.yml -f docker-compose.local.yml \
  -f docker-compose.dogfood.yml -f docker-compose.founder.yml \
  up -d --no-deps backend worker heavy-worker beat frontend
```

`config | grep image:` is the proof of the effective pair; do not skip it. `--no-deps`
names the lanes and stops compose from recreating anything else. Recreating these
containers briefly interrupts the founder. Never `down`, `stop`, or `rm` a founder-stack
container, and never restart its database or Redis.

## Verify, then move the deploy ref

```bash
curl -sS -D - -o /dev/null http://localhost:3456/health | tr -d '\r' | grep -i '^x-stash-cli-latest:'
git show "$(git rev-parse refs/heads/deploy/main)":pyproject.toml | grep -m1 '^version = '
docker inspect stash-backend-1 stash-worker-1 stash-heavy-worker-1 stash-beat-1 \
  stash-frontend-1 --format '{{.Name}} {{.Config.Image}}'
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:3457/login
docker logs --since 10m stash-backend-1 2>&1 | grep -ciE 'error|traceback|exception'
```

`X-Stash-Cli-Latest` is derived from `pyproject.toml`'s version at import time
(`backend/cli_release.py`) and applied to every response, so it is the version of the
commit the running image was cut from. It must equal the version at the deploy ref. The
`docker inspect` readings must agree with `docker-compose.founder.yml`: the pin is a
claim about the stack, only the container readings measure it.

Only after all of that is green:

```bash
git update-ref refs/heads/deploy/main <sha>
scripts/publish-trunk.sh
git merge-base --is-ancestor "$(git rev-parse refs/heads/deploy/main)" refs/heads/main; echo "deploy-ancestor=$?"
```

## Rollback, with the truth

The previous tags stay on disk (`docker images | grep 'stash-backend:main-'`), and
re-pointing `docker-compose.founder.yml` at them plus the same `up -d --no-deps` restores
the old pair. **That is not enough once the new backend has migrated the database.** A
previous image cannot boot against a schema it has never seen, and fails at once:
`ERROR [alembic.util.messaging] Can't locate revision identified by '0219'`, measured
with 440 error lines and no `/health`. A real rollback therefore runs the downgrade from
the NEW image first, then re-pins:

```bash
docker run --rm --entrypoint alembic <new-image> current     # where the data actually is
docker run --rm --entrypoint alembic <new-image> downgrade <previous-head>
```

Rehearse that on the proof copy, not on the founder database.

## The founder database is an island, and the current blocker

**Its `alembic_version` row is not a position in the trunk chain and must never be read as
one.** The number that row carries names content from this repository's own pre-renumber
chain, so `select version_num from alembic_version` answers a question about the founder's
history, not about trunk: measured on 2026-11-14, the island reads `0225` and holds 78 base
tables where a golden database built from empty to trunk's `0225` holds 106. The diff is
exact in both directions: 29 tables the island lacks and every one of them is in the `rm_*`
workbench family, while the island holds one table the golden does not — `folder_mcp_tokens`,
whose carrier is a later revision. The only meaningful measurement is an object-level content
diff against a golden database built from empty at a named revision, in both
directions (the commands are in *Prove it before the founder touches it* below). The
classic symptom of reading the number instead is measured and reproducible: boot a trunk
image against an unrescued island and the chain trusts the stamp, skips trunk's `0214`,
never creates `rm_scoring_runs`, and dies at the first revision that reaches for it —
`asyncpg.exceptions.UndefinedTableError: relation "rm_scoring_runs" does not exist` from
`0235`'s `ALTER TABLE rm_scoring_runs ADD COLUMN progress jsonb`.

The id is ambiguous for a second, separate reason: `0225` specifically is doubly occupied.
The deployed `main-11e0644a` build wrote it meaning
`backfill_finished_at_from_close_events`, while today's tree books that id as
`trace_images`. `backend/migrations/shipped_revisions.json` records the ids no new
migration may book because of it, and `0dfd17363` (STAS-371) renumbered our local tail
above upstream's head so the two chains stop colliding.

Do not write the trunk head into this file as a fact to be read; take it from the tree at
the moment you need it: `python backend/migrations/check_heads.py` prints the tree's single
head, and `python -m alembic heads` prints what the checkout you are standing in can
reach. Booting any trunk image newer than the 2026-10-03 renumbering against an unrescued
island replays migrations whose schema already exists, which is the `ResolutionError` class
that once took the founder's product down while the workers sat in `created`. Until the
rescue below is executed, the deployable set of trunk commits is the ones whose content the
island provably already holds, and `deploy/main` stays where it is. Measured on 2026-11-12:
`select version_num from alembic_version` in the founder database reads `0225` and
`alembic heads` inside the running `stash-backend:main-11e0644a-pi` reads `0225 (head)`, so
today's stack is self-consistent and no forward move is available without that decision.

### The rescue is one move, and it is measured green on a copy (STAS-440, 2026-11-14)

The rescue this file used to recommend was a four-step path (`stamp <base>`,
`upgrade <mid>`, `stamp <later>`, `upgrade head`). It is superseded, on measurements rather
than on preference: it hand-stamps twice where the move below hand-stamps once, and it skips
the whole `0257`-`0268` range by *asserting* it, so `0260` and
`0265` never execute and trunk's curator-log archive and shared-wiki revocation are
deferred indefinitely. A definition-level read says the island's claims in that skipped
range are honest — `pg_get_constraintdef` and `indexdef` for `agents_last_run_outcome_check`,
`agents_curated_through_pair_check`, `idx_history_events_owner_created`,
`one_key_credential_per_user_provider`, `user_agent_credentials_user` and the
`folder_mcp_tokens` column set are byte-identical to a golden built at trunk head — so the
older path was not unsafe. It was just strictly less informative at equal cost, and the move
below costs nothing extra.

**The move, proven on a faithful copy of the founder's own data:**

```bash
python -m alembic stamp <base>      # the base measured below, never remembered
python -m alembic upgrade head      # with INTEGRATIONS_ENCRYPTION_KEY from the founder keyring
```

Measured 2026-11-14 on a copy restored from a `pg_dump -Fd -j 4` of the island: `rc 0`, 58
revisions executed, `alembic_version` at trunk head, 144 base tables where a golden built
from empty to that head holds the same 144, and a second `upgrade head` in the same run
reported nothing pending. Content, both directions, against that golden: nothing the copy
held that trunk's head has retired, and one object trunk's head has that the converged copy
still lacks —
`idx_history_events_embedding`, the `hnsw` index spelled only by `0001`'s initial schema, so
no revision above it will ever create it and a converged island still needs it made by hand
if semantic search is to stop seq-scanning `history_events`.

- **The measured base is `0213`, with two named containment exceptions.** Containment was
  tested by building goldens from empty and diffing every table, column, index and named
  constraint in both directions. Golden@`0213` is inside the island except
  `one_curator_per_user_per_wiki` (the island's own fork work dropped it — and it is exactly
  what `0261`'s `DROP INDEX` is about, so it is a guard requirement, not a reason to lower
  the base) and `idx_history_events_embedding` above. Nothing else.
- **The collision list is `0257`, `0261`, `0262`, `0263`, `0266`**, in that order, each dying
  with `column ... already exists` before the fix. All five are now re-runnable in their own
  file, one catalog read per node, and `backend/tests/test_migration_founder_lineage_reapply.py`
  pins both lineages. `0258` was hit and survived here — but STAS-443's census measured why it
  only *happened* to survive: `agents_last_run_outcome_check` is present on the island with
  trunk's widened definition, so the unguarded `DROP CONSTRAINT` succeeded there, while the
  statement can still meet an object it did not create on a lineage that never had it. It now
  carries the house `DROP CONSTRAINT IF EXISTS` form at `0258:41`, the one `0260:49` uses on
  this very constraint name two revisions later; the `ADD` sits in the same transaction, so one
  codepath remains. (`0260`, `0264`, `0265`, `0267`-`0273` needed nothing, and the earlier
  sentence here that defended leaving `0258` unguarded was written from that single surviving
  pass rather than from the object's history.)
- **The census is value-level, not row counts.** Row counts prove the copy is faithful; they
  cannot see an `UPDATE`. What this run actually wrote: `0268` and `0269` set `finished_at` on
  2 520 run sessions (NULL count 3 200 to 680, whole-table digest moved), and `0272` re-encrypted
  both local credential documents to add `thinking` (decrypted content identical apart from that
  one key). `0223` had nothing to delete on this data (0 rows for provider `x`, 0 `x_saves` and
  `instagram_saves` sources), and `0265` took its early return, so no archive, no wiki rebuild
  and no queue reset ran. Re-measure every one of those numbers on the copy you are about to
  converge; they are this dump's, not the island's today.
- **The move was re-measured green against trunk's *live* head on 2026-11-15 (STAS-443), and
  the last step was taken by the shipped boot.** `check_heads.py` printed `0274` when the
  host-side `upgrade head` finished on a fresh single-writer restore of the retained dump;
  `0275` landed on trunk the same evening, and booting the lane carrying STAS-443's guards
  against that converged copy — `uvicorn --workers 2`, `init_db` as migrator, the founder
  keyring supplied — printed exactly one `Running upgrade 0274 -> 0275` across two workers,
  zero error lines, `/health` 200, stamp `0275`. The value census before-to-after that leg
  moved exactly two lines: the `user_agent_credentials` digest (0275 re-encrypts local docs;
  the decrypted doc shows the owner's name and every shared value unchanged, keys added
  `thinking` and `context_window`) and the stamp line. The object diff against a golden built
  to the same head names exactly one surviving miss — `idx_history_events_embedding` — plus
  eleven CHECKs whose `pg_get_constraintdef` differs in cast spelling only, value sets equal.
  Do not read `0275` as a fact: take the head from `check_heads.py` in the tree you run.
- **Four effects are irreversible, and the operator owns all four.** `0223`'s `DELETE FROM
  user_integrations WHERE provider = 'x'` at `0223:27` and its disabling of the `x_saves` and
  `instagram_saves` sources at `0223:19-24`, whose `downgrade()` is a deliberate `pass` at
  `0223:30-33` so no rollback re-enables them either; `0265`'s archive and shared-wiki rebuild,
  whose `downgrade()` raises `NotImplementedError` by design at `0265:177-179`, so no alembic
  rollback undoes it — the `0265` probe (`workspaces.curation_generation` present) says the
  founder lineage takes the early return and does not fire it; `0269`'s rewrite of
  `sessions.finished_at` at `0269:84`; and `0263`'s `_backfill_names()`, which before STAS-443
  decrypted every credential row and overwrote `name` unconditionally — a rename the founder
  had made on his endpoint rows would have been destroyed by the pass and could not be rolled
  back, because the previous name was never stored. It now selects `WHERE name IS NULL`
  (`0263:123`), which is pinned by `test_migration_fork_object_convergence.py` with an
  owner-chosen name no backfill could invent; on the founder lineage measured 2026-11-15 both
  rows were already named, so the guard selects zero rows and never decrypts. Earlier text here
  named two, then three, and understated the set.

**Executing it on the island remains the operator's decision**, and it stayed unexecuted as far
as this record knows: this record came from a copy, the founder database was read with `SELECT`s
and nothing else, and no cutover happened as a side effect of measuring it. *(Superseded as to
fact on 2026-11-16 by STAS-441's execution, recorded near the end of this file; not superseded
as to rule — that move went because the operator authorized it by name, and it is no precedent
for an un-authorized stamp.)* Read the recipe honestly: it begins with a
hand `stamp`, and this section does not relax the Prohibition below that refuses a hand
re-stamp — that Prohibition is precisely why the move is no agent's to run. On the copy a stamp
is the instrument that sets the starting line, which is where it stays run; on the island the
same statement is a human claiming a schema position the data may not hold, and claiming it is
what the Prohibition exists to keep out of automated hands.

Two decisions stay open after this measurement, and both are the operator's:

1. **Whether to converge the island at all.** The move is green and reversible except for the
   four effects above, and on this dump the irreversible set measured empty for `0223` (0
   rows) and for `0263` (both credential names already set) while `0268`/`0269` wrote
   `finished_at` on 2 520 sessions. Re-measure both on the copy you restore, because the island
   grows (`history_events` read 4 906 620 on 2026-11-14 against the dump's 4 905 837).
2. **What to do about the one object a converged island still lacks.** `idx_history_events_embedding`
   is spelled only by `0001`, so no revision above the base will ever create it and the
   converge leaves it missing: semantic search keeps seq-scanning `history_events` until someone
   creates it by hand, in the same maintenance window as the converge, or accepts the scan.

### The census that replaced "at least 12" (STAS-443, 2026-11-15)

The intake counted collisions by text-scanning revision files, landed on 12, and explained the
boot-killing collision it had missed by calling that DDL "composed dynamically". Both halves
are falsified on the tree: `0257`'s `share_wiki` add was a plain `op.add_column` — the exact
shape the scan was said to be blind to — and the real coverage gap is that `0256..0273` carry
41 `op.execute(` lines against one `add_column` line, which no grep can enumerate objects
through. The instrument is a diff of two resulting catalogs: one inventory query keyed by
kind with a definition digest (`md5(indexdef)`, `md5(pg_get_constraintdef(oid))`), run against
 databases built from empty by the chain's own words (`0213`, `0225`, head) and against a
fresh restore of the founder dump. Measured on the `rm_*` retirement trunk and re-measured at
`0275`:

- **The additive hole is 558 objects at `0225`, not 408 and not 38.** The intake's two totals
  were both wrong: 29 tables + 312 columns + 68 indexes + 149 constraints, because the 29
  `rm_*` tables carry 529 dependent objects with them. Q1 settles with it: the hole is the
  consequence of a stamp that names the wrong position, and replaying from the measured base
  creates all 556 `0214..0225` objects with zero collisions — intersection of that replay
  segment with the island measured empty, divergence zero.
- **`COLLIDE` against head is 30 objects and `DIVERGENT` is zero.** Every island-held member
  of the `0226..head` delta matches trunk's definition digest byte-identically — including
  `one_curator_per_scope`, whose drop-and-rebuild in `0261` is therefore a content-preserving
  re-validation on this lineage, not a rewrite. The exists-but-divergent class the plan
  reserved stays named (a future lineage can still carry it, and the rebuild and the
  convergence test are its instruments), but this island has no member of it.
- **The FKs that ride colliding columns are present on the island too** —
  `agents_curator_folder_id_fkey`, `session_folders_wiki_folder_id_fkey`,
  `agents_credential_id_fkey`, all identical to trunk's — so the class plan review named
  "column present, trunk's inline FK absent" has no measured member here either, and the
  post-run catalog-minus-golden diff is the mechanical gate that proves no guard silently
  dropped one.
- **Of the intake's twelve, four were never collisions at all** (`drive_documents.extraction_task_id`,
  `user_sources.sync_task_id`, `users.developer_platform_only`, `workspaces.curation_generation`
  are created at-or-below the island's own numbering position, not by `0226..head`), six were
  already guarded on trunk, and the census found what its list had missed: `share_wiki`,
  `agents.model_id`, `agents.digest_model_id`, `user_agent_credentials.name`, and the eight
  `K:`/`I:` objects enumerated in the card's `census` document. Every one is now guarded or
  measured to a no-op.
- **`0263`'s primary-key drop is skipped by catalog read on the island** (`_primary_key_is_id()`
  is true there), `0267`'s widening is a legal no-op retype, `0271`/`0274`'s unguarded adds
  cannot meet a foreign object on this lineage (their columns are absent), and eleven CHECKs
  differ from the golden only in cast spelling — `((... )::character varying)::text` versus
  `(... )::text[]` — with equal value sets, which is what
  `backend/tests/test_migration_fork_object_convergence.py` asserts as semantic equality.

Full member lists, the per-object decision table, and the already-guarded adjudication are
the `census` and `decision-table` documents on STAS-443.

### The boot-time migrator repeats; it does not interleave (STAS-443, 2026-11-15)

The intake recorded `Running upgrade 0234 -> 0235` printed seven times while the stamp sat at
`0225` and suspected the boot migrator of re-entrancy under `--workers 2`. Measurement licenses
the opposite verdict, and no change shipped to `backend/database.py`, `backend/entrypoint.sh`,
or a new `backend/migrate.py`:

- Real pending work with the shipped boot: two `Context impl PostgresqlImpl` lines, **one**
  `Running upgrade` line, the stamp moved once. `pg_advisory_lock(715551)` at
  `backend/database.py:62` serializes; the losing worker finds head reached and migrates
  nothing.
- A deterministically failing migration booted with one uvicorn parent printed the intake's
  shape twenty times — `Running upgrade`, traceback, rollback, stamp unmoved — with exactly
  one writer. The repetition is the supervisor restarting an *atomic* run, not two
  interleaving ones. (This one was a mistake first — an empty encryption key — and was kept
  as the measurement once its shape was recognized.)
- The one shape no retry alone was supposed to produce — a stamp *below* DDL that only later
  revisions create — also needs no second writer once you are above `0256`: `0256`'s
  `autocommit_block()` commits everything through `0255`, and a failure above that point
  leaves `0257`'s column visible with the stamp reading `0255`. Measured directly on the
  card's first replay attempt (stamp `0255`, `share_wiki` present, `rm_scoring_runs` present,
  `turn_timeout_seconds` absent — the standing copy's exact silhouette). Below the commit
  point the second-writer fingerprint stands: a `0235` column under a `0225` stamp cannot
  come from this chain, and the intake itself names two agents restoring one database.

### The copy harness on this machine, in facts a future card cannot re-derive (STAS-443, 2026-11-15)

- `/` reads 98% (21 G free at this reading — 97% at intake, and it moves) and docker's
  data-root is on `/`, so restoring a 20+ GB `PGDATA` into a named volume fails with `could
  not extend file`. Dumps and `PGDATA` bind under `/data`. Freeing space on `/` by pruning
  other projects' images or volumes is operator rollback territory — do not do it.
- `docker network create` fails here ("all predefined address pools have been fully
  subnetted"); the working copy project passes an explicit `172.25.77.0/24`, because the
  default pools `172.17-31` are exhausted on this machine.
- **One restore and one migrator at a time, proven before any measurement runs**: the
  duplicate-primary-key probe `select count(*) from (select conrelid from pg_constraint
  where contype='p' group by conrelid having count(*) > 1) d` reads zero and
  `pg_stat_activity` shows zero other backends on the database. Two `pg_restore`s into one
  database produce `multiple primary keys` and void every number taken from it — observed
  twice in one week, two agents and two compose projects against one target.
- The founder keyring is the named volume mounted at `/app/.secrets` inside
  `stash-backend-1` (read the name from `docker inspect`; at this reading
  `stash_backend_secrets`, file `integrations_encryption_key`). It decrypts the island's
  credential rows — probed, reported as `OK` only. Any copy run crossing `0263`, `0272` or
  `0275` needs `INTEGRATIONS_ENCRYPTION_KEY` in its environment; without it the boot fails
  loudly, rolls back atomically, and prints the repetition pattern the intake misread.
- Boot probes: `/health` 200 is the gate. A `GET /login` against the backend answers 404 and
  that is the correct backend shape — `/login` is a product-frontend page; the backend route
  is the POST on the users router. Do not chase that 404.
- The intake-era standing copy (compose project `stash3995`: hand-applied additive DDL plus a
  chain stopped at `0255`) is a hybrid and is not a proof — as an empty census baseline it
  would also *pass falsely*, because its missing set is already filled. A proof starts from a
  fresh restore of the retained dump. And `founder-repair-39cb4595.sql` is not a mechanism:
  the replay route above is green with zero collisions, so the chain creates the `rm_*` family
  itself; the `.sql` was never committed, and keeping an untracked hand-written copy of DDL
  the chain owns would be a second schema source, which the Prohibitions below already refuse.

### The `.founder-11e0644a-pin.yml` claim was never measured, and it is false

An untracked file named `.founder-11e0644a-pin.yml` sat in the working tree asserting that
trunk's `0214`-`0225` schema "is already applied" to the founder database. That sentence was
never the output of a query. It is quoted here from the intake that ordered this correction:
the file is untracked, so no commit in this repository carries its text, and it sits in the
trunk checkout rather than in any lane worktree, which is where every card measuring it has to
run. What is measured is that the claim is false: the island lacks 29 of the tables that a golden at
`0225` holds, every one of them in the `rm_*` family, and that absence is the measured reason
a boot of a newer image dies on `rm_scoring_runs`. The tracked record is
`docker-compose.founder.yml`, which names the same commit and carries an explicit NOT CUT
OVER paragraph (`backend/tests/test_founder_deploy_pin.py` guards it). Because the pin file is
untracked, correcting it in place is not a lane change and no commit or gate in this
repository can carry or verify that edit; this card could not reach the file from inside its
worktree and so did not make it. STAS-441 records the same file as one of two ad-hoc untracked
pins a live compose project was started with, which is why it cannot be left where it is: the
file belongs to the cutover, and the Prohibitions already refuse an untracked pin file.

### Booting a proven image off the island's `0225` is red at `0273`, measured 2026-11-14

The prohibition below says the founder database's migration is the boot of a proven image and
nothing else. That instrument was measured against a faithful copy of tonight's island data, and
it does not land a coherent schema at this distance:

- The pair was built from trunk `39cb45957` by the recipe above: `stash-backend:main-39cb4595-base`
  (4.12 GB), `stash-backend:main-39cb4595-pi` (4.55 GB), `stash-frontend:main-39cb4595` (167 MB),
  each exit 0. **The frontend half of the wedge below is gone**: `docker build frontend/` at this
  commit compiles with zero type errors, which `ef10258cc`'s fixture corrections had already
  made true for every commit at or after it.
- The copy: `pg_dump -Fd -j 4` of the island at `0225` = 4.5 GB in 10 m 16 s, `pg_restore -j 6`
  into a `pgvector/pgvector:pg16` whose `PGDATA` is bind-mounted on a roomy disk (the dump and the
  copy must not live on `/` or `/home`: 33 GB and 19 GB free respectively). The copy read back the
  source's stamp `0225` and the same seven row counts (`history_events` 4 905 837,
  `security_audit_events` 140 567, `page_edits` 6 591, `sessions` 3 201, `pages` 205, `agents` 33,
  `users` 28).
- The boot: 48 lines of `asyncpg.exceptions.UndefinedTableError: relation "rm_scoring_runs" does
  not exist` from application startup, `/health` never answered (300 s of `000`), and the copy's
  `alembic_version` ended at **`0213` — behind where it started**. The chain advanced
  `0225 → 0256` while two uvicorn workers each ran their own `upgrade head` over the same
  database, and the id `0225` in the copy names a schema position that trunk's `0225` does not:
  `rm_scoring_runs` is created by trunk's `0214`, which a boot that starts at `0225` never
  applies. This is the ambiguous-id trap, not a bug in the candidate.

So for the boot-only path no forward move exists that both satisfies the prohibition and
works, and the founder database's migration is not the boot's to make. **That is the state of
knowledge on 2026-11-15, and it is where this file's reasoning still stands; what changed on
2026-11-16 is that the *other* path was taken.** STAS-441 converged the founder database onto
trunk's chain and cut the stack over to the pair built from `5f44d522`, inside a maintenance
window the operator authorized by name, and the section
"The island is converged and the founder stack runs one commit" near the end of this file is
that execution's record, with every reading it took. That record supersedes three factual
claims made in the paragraphs above — that the move stays unexecuted, that the founder
database still reads `0225`, and that the pin still names `11e0644a`. It supersedes none of the
reasoning: the boot-only path is still closed, a hand `stamp` is still what the Prohibition
below exists to keep out of automated hands, and the only reason the move was executable at all
is that a human authorized this card to run it.

### The deployable band's frontend does not build, measured 2026-11-12

That band has a second, independent wedge, and it is why the founder frontend is still
running a September image while its backend lanes run October's:

```bash
git worktree add --detach <dir> 11e0644ad9fedc88bcd97409559e7a8cb8559960
cd <dir> && docker build --build-arg BACKEND_INTERNAL_URL=http://backend:3456 -t probe frontend/
```

That build exits 1. `npm run build` typechecks the test fixtures, and four of them name
types whose required fields they lack:

```
src/app/(app)/sessions/page.test.tsx(60,7): error TS2741: Property 'reward_models_enabled' is missing ... but required in type 'User'.
src/app/(app)/sessions/[sessionId]/SessionClient.test.tsx(57,7): error TS2741: Property 'reward_models_enabled' is missing ... but required in type 'User'.
src/app/(app)/skills/page.test.tsx(146,9): error TS2741: Property 'mcp_url' is missing ... but required in type 'SkillPublishInfo'.
src/app/(app)/skills/[slug]/settings/SkillSettingsPageClient.test.tsx(90,5): error TS2322: ... mcp_url?: string | undefined ... not assignable to type 'PublishedSkill'.
```

The fixtures were corrected by `ef10258cc` (2026-10-15), which is the same commit that
booked `0226` and moved the chain off `0225`. So no commit exists that both boots the
island and builds a frontend image: the whole deployable band fails the same four
errors, and completing the pair inside it is impossible rather than unfinished. The
answer is not a build flag that skips the typecheck — that hides a real defect in shipped
text. It is the operator's rescue decision above, after which the target is a recent
`main` commit: `docker build` of `frontend/` at `0b980f533a223187ed5b57c9d36c5854bb8c4c1d`
succeeds with zero type errors and the resulting image serves `/login` with 200 when
booted on a spare port.

## The island is converged and the founder stack runs one commit (STAS-441, 2026-11-16)

Everything above this heading is measurement of a move not yet taken. This section is the move,
taken. It was authorized in writing by the operator at `2026-11-16T14:50:12.836Z` — three gates
yes: rehearse on a faithful copy, run the leg on the island, take the downtime window — plus the
manual `idx_history_events_embedding` create inside that same window. It rehearsed green first, at
every gate, on a copy of the founder's own data (`STAS-441`'s Step 2/3 record is on the card).

### One commit, and the refs that say so

```
deployed pair        stash-backend:main-5f44d522-pi  +  stash-frontend:main-5f44d522
both built from      5f44d522e07d91bd1dbb15897daecdcfd7407638  (candidate8 5f44d522), a tree
                     detached at exactly that commit; -base 4.12 GB, -pi 4.55 GB, frontend 167 MB,
                     each build exit 0
migration head       0275 (check_heads.py in that tree)   |   pyproject version 0.1.491
refs/heads/deploy/main  before 11e0644ad9fedc88bcd97409559e7a8cb8559960
                        after  5f44d522e07d91bd1dbb15897daecdcfd7407638   (deploy-ancestor=0)
X-Stash-Cli-Latest      0.1.369 before the cutover  ->  0.1.491 after, equal to the version at
                        refs/heads/deploy/main read from the ref itself
founder downtime        18:43:36Z (four writer lanes stopped) -> 18:58:10Z (five lanes up) =
                        14 min 34 s
```

### The window, in the order it ran, with the readings

1. `18:43:36Z` — `docker compose … stop backend worker heavy-worker beat`, by name, `frontend`,
   `stash-postgres-1` and Redis left up. rc=0, four containers `Exited (0)`.
2. `18:43:53Z` — the rollback dump **first**: `pg_dump -Fd -j4` into a new dated directory,
   `/data/stas441/dump-island-20261116T184353Z/founder`, rc=0 in 491 s, 4.5 GB, `pg_restore
   --list` rc=0 over 583 TOC entries. That listing is also how the dump was proved to carry the
   embedding index's data and `history_events`.
3. Read-only census on the island before anything wrote: identical to the copy's before-census,
   plus the base-containment inventory re-measured on the island at the moment of proof. Direction
   one (`golden@0213` has, island lacks) held exactly one object, `one_curator_per_user_per_wiki`,
   the fork's own drop that `0261`'s `DROP INDEX` targets; direction two held exactly
   `rm_reward_models_trace_ids_check`, which `0215` drops by name inside its own `upgrade()`. **The
   measured base is `0213`**, and the island's copy of the hand index means the runbook's
   two-object remainder reads as one object here.
4. Single-writer proof: 0 other client backends on the founder database.
5. `18:52:50Z` — **leg 1, `stamp 0213`**, one throwaway container from the deployed image with the
   founder keyring volume mounted where its own entrypoint reads it (no key character crosses a
   shell): rc=0, account after `0213`, 2 s.
6. `18:52:58Z` — **leg 2, `upgrade head`**, same instrument: rc=0, **60** `Running upgrade` lines,
   first `0213 -> 0214`, last `0274 -> 0275`, 0 error lines, account after `0275`, 29 s. The walk
   is byte-identical to the copy's, line by line. That 60, not 62, is arithmetic the chain itself
   explains: `0237` and `0238` do not exist in trunk's history, so the band `0213…0274` steps 60
   times. No third alembic command was issued.
7. The authorized index statement, `CREATE INDEX IF NOT EXISTS idx_history_events_embedding …`:
   rc=0 in 0 s, `NOTICE: relation … already exists, skipping`. **Already present, not built
   here** — the island's index is valid, 8,480 MB, and its `indexdef` is `0001`'s spelling
   verbatim. Reporting a build would have been false.
8. Census and inventory re-run before any container restart (both captured on the card).
9. `18:58:02Z` — the cutover, `up -d --no-deps backend worker heavy-worker beat frontend`, rc=0.

### The value census, both sides measured rather than one side remembered

The before column is not the in-window `SELECT` list alone: the rollback dump was restored into a
scratch database and read there, so the comparison is against the bytes the rollback would
actually return. The after column is the live island, read after the legs and before the restart.

| measure | before | after |
|---|---|---|
| `alembic_version` | `0225` | **`0275`** |
| public tables | 78 | **144** |
| `sessions` with `finished_at IS NULL` | 3,234 | **689** |
| `0269`'s own predicates as a `SELECT` | 2,545 | **0** |
| `0268`'s close-event predicate as a `SELECT` | 0 | 0 |
| `history_events` | 4,929,702 | 4,929,702 |
| `security_audit_events` | 141,753 | 141,753 |
| `page_edits` | 6,650 | 6,650 |
| `sessions` | 3,237 | 3,237 |
| `pages` | 206 | 206 |
| `agents` | 33 | 33 |
| `users` | 28 | 28 |
| `user_integrations` where `provider='x'` | 0 | 0 |
| `user_sources` `x_saves`/`instagram_saves` (and of those `sync_enabled`) | 0 (0) | 0 (0) |
| `user_agent_credentials` rows and their `name`s | 2, `192.168.12.40`, `10.1.9.34` | unchanged |
| `workspaces.curation_generation` present (`0265`'s probe) | 1 | 1 |
| `idx_history_events_embedding` | 1 valid | 1 valid |

Exactly four lines moved, and the `NULL` count moved by 2,545 — the predicate's own count, which
then re-reads 0. Nothing else in the census is different, and `history_events` not moving across
the window means no ingestion traffic confounds the comparison.

### The four irreversible effects, each with its measured count

- **`0223`** deleted `user_integrations` rows for the retired X provider and disabled the
  `x_saves`/`instagram_saves` sources, with a `downgrade()` that is a deliberate `pass`. Both sides
  read **0 rows and 0 sources**, so there was nothing here for it to destroy and nothing to re-enable.
- **`0265`**'s archive-and-shared-wiki rebuild, whose `downgrade()` raises `NotImplementedError`,
  took its documented **early return**: the probe column `workspaces.curation_generation` is
  present in the pre-leg restore (read 1), so the irreversible branch never fired. `shared_wiki_*`
  appears post-leg because `0254` creates those tables and both read 0 rows — schema growth, not
  a rebuild.
- **`0269`** (with `0268` beside it) rewrote `sessions.finished_at`: **2,545 rows** closed, the
  `NULL` count falling 3,234 → 689. This is the effect the operator accepted losing, and it is the
  one a `downgrade` could not undo.
- **`0263`**'s guarded credential-name backfill selected **0 rows**: both endpoint rows already
  carried owner-chosen names, so no name was overwritten and, per the guard added in STAS-443, the
  pass never decrypted.

And the credential rows themselves, measured through the deployed image's own decrypt path with the
founder keyring mounted — the question the operator actually asked, whether the stored credential
survives without being re-issued:

```
before  doc-keys=api_key,base_url,model                  md5-all=3cefbb7642352beb1e9208187eef586a
                                                       md5-all=a36a88ae41206d49b15b15ccbca13fd0
after   doc-keys=api_key,base_url,context_window,model,thinking
                                            md5-all=e1c565788f871726fd016034303b2184
                                            md5-all=372cd7c86ac8f04d483e74bac6a5bcee
after, digest over only the three pre-existing keys: md5-shared=3cefbb764235… / a36a88ae4120…
```

The last line is the proof: the after-state digest over the keys that existed before equals the
before-state digest over the whole document, byte for byte. Every value the owner typed — including
the stored API key — is unchanged and un-rotated, only `thinking` and `context_window` were added,
and both documents decrypt on the deployed image with the founder keyring. Ciphertext digests are
not a comparison here: Fernet writes a fresh IV and timestamp per encryption, so equal documents
encrypt to different bytes by construction.

### Object convergence, measured with the stricter inventory

The runbook's query keyed constraints by name. Re-run with each index's full `indexdef` and each
constraint's full `pg_get_constraintdef` text, the converged island and a golden built from empty to
trunk's head in the same cluster are **identical: 2,704 lines each, zero differences in either
direction**. The converged copy reached the same state once its hand index landed. So the "one
surviving miss plus eleven cast-spelling CHECKs" remainder that STAS-443 named is closed on this
pair of databases, and the residual risk that a converged island is a different schema from a
freshly migrated one is measured away rather than assumed away.

### The cutover's effective compose list, and why the paths are not the runbook's

The stack now runs exactly the four documented files — `docker-compose.prod.yml`,
`.local.yml`, `.dogfood.yml`, `.founder.yml` — and the untracked `.stas270-cutover-override.yml`
and `.founder-11e0644a-pin.yml` are retired, which is what the Prohibition against untracked pin
files asks for. `com.docker.compose.project.config_files` on all five containers names those four
paths **under a detached worktree at the pinned commit**, not under the primary checkout, because
`docker-compose.prod.yml` declares a service-level `env_file` that compose resolves against
`--project-directory`: a detached tree has no `.env`, so the run passes `--project-directory` at
the checkout that owns it and points `-f` at the pinned tree. That substitution was measured, not
assumed: the render from the detached tree and the render in the documented form are **301 lines
each and differ in exactly the six intended `image:` lines, zero other lines** — no volume, bind,
network, name, port or environment line moves. `sync-worker` stayed unstarted by name, and the
primary checkout stayed clean throughout (`git status --porcelain` empty, still holding
`refs/heads/main`).

### Verification, before the ref moved

```
docker inspect on all five containers: image names equal the pin, byte for byte
config_files: exactly the four files, on all five
X-Stash-Cli-Latest = 0.1.491 = version at refs/heads/deploy/main
GET  :3456/login  -> 404  (the correct backend shape; the frontend owns /login)
GET  :3457/login -> 200
/health -> {"status":"ok"}
error-class lines in the last 10 minutes: 0 on backend, worker, heavy-worker, beat, frontend
"Running upgrade" lines in the deployed boot: 0, account 0275 before and after the restart
index: present, indisvalid=true, indexdef = 0001's spelling, 8,480 MB
scripts/publish-trunk.sh -> status=0, main and deploy/main both mirrored to the fork and read back
github run list: newest run unchanged (a scheduled Dependency audit from 2026-10-14), publish.yml's newest
                 still 2026-10-03 — the mirror triggered no release; docker-publish.yml does not
                 exist on the fork's default branch, so it cannot have run
```

### Rollback, from here

`downgrade` is not a path: `0223`'s is a `pass`, `0265`'s raises `NotImplementedError`, and `0269`
has nothing to undo but has already written 2,545 values. Rollback is **restore from
`/data/stas441/dump-island-20261116T184353Z/founder`** — the dump this window took after the lanes
were stopped and before anything wrote — plus re-pointing `refs/heads/deploy/main` at
`11e0644ad…` and the pin at the old pair. Budget for what that restore costs: this dump's schema and
all of its data loaded in about 35 minutes at default settings, of which the `hnsw` build on
`history_events` was still unfinished when it was terminated. The same index built to completion on
the same data took **1,417 s (23 min 37 s) at `maintenance_work_mem=16 GB` with zero parallel
workers**, spilling nothing, and at the container default of 64 MB it had not finished its tuple
scan after 41 minutes. A restore of this dump is therefore an hours-scale operation whose long pole
is one index, and `maintenance_work_mem` is the knob.

### What the tracked pin says now

`docker-compose.founder.yml` names `5f44d522` on all five backend lanes and on the frontend, its
`BASE_IMAGE` example names the same commit, and the `NOT CUT OVER AS OF 2026-11-12` paragraph is
deleted because the cutover landed and the container readings agree — which is what that paragraph
instructed. Its guard, `backend/tests/test_founder_deploy_pin.py`, passes 6/6 against a scratch
pgvector container at the new pin. The pin commit (`420f1041f`) is one commit directly over the
candidate on the card's lane; **it is not on `refs/heads/main`**, because that ref is held by the
primary checkout's worktree and moving it is the engine's or the operator's leg, not this one's.

### What this section does not claim

- It does not retire the `0225`-era analysis above: the ambiguous-id trap that made the boot path
  red is still exactly what it was, which is why the leg began by naming a base and proving it.
- It does not license a hand `stamp`. The Prohibition below is untouched and still true for every
  future card; this move went because one named operator authorized one named card for one named
  window, and the base-containment inventory taken at the moment of proof is what made the claimed
  schema position checkable instead of assumed.
- It does not claim the founder stack was migrated by a boot. The boot printed zero upgrade lines:
  the legs converged the database, then the deployed image booted onto a chain already at head.
- It does not claim the two untracked override files were ever load-bearing; the file above
  (`The .founder-11e0644a-pin.yml` section) already records that the claim was never measured.

## Prohibitions

- No ad-hoc lane deploys, no single-lane deploy, no lane branch, no working-tree build.
- No untracked pin file: `docker-compose.founder.yml` is tracked so its history is the
  deploy log.
- No database re-stamp by hand. Ids are ambiguous across the 2026-10-03 renumbering, so
  a stamp silently claims schema that may or may not be there. The founder database's
  migration is the boot of a proven image and nothing else.
- No push to `origin`, no force-push, no PR without the operator.
- No second stack on ports 3456 or 3457 while the founder stack owns them: they are
  exclusive per machine and that stack is a product someone is using.
