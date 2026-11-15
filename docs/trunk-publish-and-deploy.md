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
  pins both lineages. `0258` was hit and survived: its `DROP CONSTRAINT` is unguarded, and it
  stays unguarded because the `ADD` for that constraint sits in the same transaction, so the
  object always exists at the start of a pass. That exemption is the transaction's pairing, not
  a missing syntax — Postgres does have `DROP CONSTRAINT IF EXISTS`, and `0260` uses it on this
  very constraint name two revisions later. `0260`, `0264`, `0265`, `0267`-`0273` needed nothing.
- **The census is value-level, not row counts.** Row counts prove the copy is faithful; they
  cannot see an `UPDATE`. What this run actually wrote: `0268` and `0269` set `finished_at` on
  2 520 run sessions (NULL count 3 200 to 680, whole-table digest moved), and `0272` re-encrypted
  both local credential documents to add `thinking` (decrypted content identical apart from that
  one key). `0223` had nothing to delete on this data (0 rows for provider `x`, 0 `x_saves` and
  `instagram_saves` sources), and `0265` took its early return, so no archive, no wiki rebuild
  and no queue reset ran. Re-measure every one of those numbers on the copy you are about to
  converge; they are this dump's, not the island's today.
- **Three effects are irreversible, and the operator owns all three.** `0223`'s `DELETE FROM
  user_integrations WHERE provider = 'x'` at `0223:27` and its disabling of the `x_saves` and
  `instagram_saves` sources at `0223:19-24`, whose `downgrade()` is a deliberate `pass` at
  `0223:30-33` so no rollback re-enables them either; `0265`'s archive and shared-wiki rebuild,
  whose `downgrade()` raises `NotImplementedError` by design at `0265:177-179`, so no alembic
  rollback undoes it; and `0269`'s rewrite of `sessions.finished_at` at `0269:84`. Earlier text
  here named two and understated the set.

**Executing it on the island remains the operator's decision**, and it stays unexecuted: this
record came from a copy, the founder database was read with `SELECT`s and nothing else, and no
cutover happened as a side effect of measuring it. Read the recipe honestly: it begins with a
hand `stamp`, and this section does not relax the Prohibition below that refuses a hand
re-stamp — that Prohibition is precisely why the move is no agent's to run. On the copy a stamp
is the instrument that sets the starting line, which is where it stays run; on the island the
same statement is a human claiming a schema position the data may not hold, and claiming it is
what the Prohibition exists to keep out of automated hands.

Two decisions stay open after this measurement, and both are the operator's:

1. **Whether to converge the island at all.** The move is green and reversible except for the
   three effects above, and on this dump the irreversible set measured empty for `0223` (0
   rows) while `0268`/`0269` wrote `finished_at` on 2 520 sessions. Re-measure both on the
   copy you restore, because the island grows (`history_events` read 4 906 620 on 2026-11-14
   against the dump's 4 905 837).
2. **What to do about the one object a converged island still lacks.** `idx_history_events_embedding`
   is spelled only by `0001`, so no revision above the base will ever create it and the
   converge leaves it missing: semantic search keeps seq-scanning `history_events` until someone
   creates it by hand, in the same maintenance window as the converge, or accepts the scan.

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

So no forward move exists that both satisfies the prohibition and works, and the rescue stays the
operator's decision. Nothing above re-stamped anything by hand, the founder database was not
touched (it still reads `0225` with its lanes on `main-11e0644a-pi`), and
`docker-compose.founder.yml` was left naming `11e0644a`, because the pin is a claim about the
stack and the container readings still measure that older pair.

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
