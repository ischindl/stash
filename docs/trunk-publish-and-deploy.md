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
`frontend`. `sync-worker` and `reward-worker` are pinned by the founder file so they
can never come up on a different commit, but they have no container: do not start
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

Require: `/health` answers `{"status":"ok"}`, the expected `Running upgrade <from> ->
<to>` lines appear (or none, if the stamp already matches), and zero
`error|traceback|exception` lines in the log. **A red proof means no cutover.** Read
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

The founder database sits on the single alembic stamp `0225`, and `0225` is ambiguous:
the deployed `main-11e0644a` build wrote it meaning
`backfill_finished_at_from_close_events`, while today's tree books that id as
`trace_images`. `backend/migrations/shipped_revisions.json` records the ids no new
migration may book because of it, and `0dfd17363` (STAS-371) renumbered our local tail
above upstream's head so the two chains stop colliding.

Trunk's chain now reaches `0270`. Booting any trunk image newer than the 2026-10-03
renumbering against an unrescued `0225` database replays migrations whose schema already
exists, which is the `ResolutionError` class that once took the founder's product down
while the workers sat in `created`. The rescue is derived and twice-rehearsed on the
island's own data (`stamp 0213`, `upgrade 0256`, `stamp 0268`, `upgrade head`, all four
landing a restored copy on `0270` with a measured loss census), and **executing it on the
island is the operator's decision**, because `0223`'s `DELETE` is unrevertable by design
and `0269` rewrites live rows. Until that decision is taken, the deployable set of trunk
commits is the ones whose head still matches the island's stamp, and `deploy/main`
stays where it is. Measured on 2026-11-12: `select version_num from alembic_version` in
the founder database reads `0225` and `alembic heads` inside the running
`stash-backend:main-11e0644a-pi` reads `0225 (head)`, so today's stack is self-consistent
and no forward move is available without that decision.

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
