# Running Stash

This guide covers the open-source code in this repository: session collection,
persistent knowledge, skills, and the CLI.

## Connect your coding agent to hosted Stash

With [uv](https://docs.astral.sh/uv/) installed, run these commands in an
interactive terminal:

```bash
uv tool install stashai
stash signin
```

`stash signin` authenticates in your browser, then guides you through session
recording, which coding agents to record, and adding Stash instructions to your
current folder. It also offers to import historical conversations in the
background. If you accept, follow progress with `stash import-history --status`.

Recording is on during setup; pause it with `stash stop`. Re-run the wizard with
`stash setup`, or run `stash connect` from another project folder to add Stash
instructions and enable session uploads there. A Git repository is not required.

If you need to install uv as well, the installer handles that and launches signin:

```bash
bash -c "$(curl -fsSL https://joinstash.ai/install)"
```

Ask your coding agent whether it can access Stash, or browse from the CLI:

```bash
stash vfs ls /
stash vfs "rg \"database migration\" /"
```

Run `stash --help` or `stash <command> --help` for command documentation.

## Run from source locally

Install Git, uv, Node.js 20.9 or newer, and Docker with its daemon running.
Ports 3456 and 3457 must be available.

```bash
git clone https://github.com/Fergana-Labs/stash.git
cd stash
uv venv -p 3.12
source .venv/bin/activate
uv pip install -r backend/requirements.txt -r backend/requirements-dev.txt
npm --prefix frontend ci
./start.sh
```

For this setup, leave `DATABASE_URL` and `REDIS_URL` unset in your shell and
repo-root `.env`. The startup script provisions a local Postgres database and
Redis instance, starts the backend and workers, and serves the frontend.
Database migrations run when the backend starts. The script also generates a
durable integration-encryption key in `.env`.

Open <http://localhost:3457/login>. Check the backend from another terminal:

```bash
curl --fail http://localhost:3456/health
```

To connect the released CLI to this local server:

```bash
uv tool install stashai
stash signin --api http://localhost:3456
```

For editable CLI development, run `uv pip install -e .` in the activated virtual
environment. See [Contributing](../CONTRIBUTING.md) for tests and contribution
guidance. Optional provider keys and integration settings are documented in
[.env.example](../.env.example).

## Self-host on a domain

Use a server with Git and Docker Compose, a domain pointing at that server, and
ports 80 and 443 available. The deployment uses prebuilt GHCR images pinned in
[docker-compose.prod.yml](../docker-compose.prod.yml). For older frontend images, see the
[libvips license texts, source, and rebuild instructions](licenses/sharp-libvips.md).

```bash
git clone https://github.com/Fergana-Labs/stash.git
cd stash
cp .env.example .env
```

Before starting:

- Set `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` in `.env`; replace
  the example database password.
- Set `PUBLIC_URL` and `CORS_ORIGINS` to your frontend origin, such as
  `https://app.example.com`.
- Replace `app.example.com` in [Caddyfile](../Caddyfile) with your domain.
- Configure any optional model providers, file storage, or OAuth integrations
  you intend to use, following `.env.example`.
- Give the curators a model to run on, or they never run: set
  `ANTHROPIC_API_KEY` here, or connect a local model endpoint in Settings after
  your first login. Either one is enough for every curator lane, and the two
  scoped lanes are no exception — the workspace curator, which reads this
  workspace's own memory, and the shared-wiki curator, which reads material mixed
  from every end user and is therefore never handed to a shell-equipped worker.
  With no key set, both resolve the endpoint you connected and complete on it,
  exactly as the project curators do, and they spend none of the monthly allowance
  doing it: what the platform does not pay for is not metered. A key that IS set is
  the provider instead, and a connected endpoint is then never consulted, so a run
  never changes provider halfway through and with it the machine that holds your
  customers' material. With neither, every scheduled curator skips
  every tick — the backend logs `no runnable model` at WARNING and each curator
  row reads `No model connected` until a model exists.
- A local model endpoint asks how large that server's context window is, and stores it
  with the box. Nothing infers the size from a model name and nothing scrapes it off the
  endpoint: the number is the operator's to state, because it is a fact about the other
  machine's own configuration. Upgrading stamps every existing local endpoint row from what
  that row already declared — migration `0275` reads a stored `models.json` override's own
  `contextWindow` where there is one, and stamps the window this build used to claim about
  every box where there is none — so an endpoint that worked before the upgrade is told the
  same size it was told before it. A row whose override cannot be read, or whose window sits
  below the 2048-token floor the service enforces, refuses `alembic upgrade head` naming that
  row's id instead of being clamped to a guess: fix the file or the number and run the upgrade
  again.
- Leave `STRIPE_SECRET_KEY` unset and this installation evaluates no plan anywhere.
  Billing exists only while that key is set. Without it there is nothing to charge, so
  there is no plan to enforce: no cap on connected accounts, no monthly curator
  allowance, no Pro gate on the curator's catch-up chain, no "upgrade to Pro" sentence,
  and the client is shown neither a plan name nor a run cap. Every account holds the
  unlimited entitlement, which is why running your own curators needs no licence, no
  key, and no Pro grant. The corollary is that `FREE_CURATOR_RUNS_PER_MONTH` and
  `INTERNAL_EMAIL_DOMAINS` are billing knobs: neither one lifts a curator here. The
  allowance refuses nobody whether it reads 10 or 1000, and a domain listed in
  `INTERNAL_EMAIL_DOMAINS` only marks those accounts internal for analytics.
- `FREE_CURATOR_RUNS_PER_MONTH` (default 10) bounds how many runs one curator of
  an account may **work** in a calendar month. It counts the runs that actually
  ran: a tick that stops at a gate — no model connected, nothing new to curate,
  the allowance already spent — consumes its clock slot and spends none of the
  allowance. It also sets how far a backlog may
  be chased: the catch-up ceiling is that allowance spread over the month that
  funds it, floored at `CURATOR_CATCHUP_PASSES` (8 by default), so raising it is what lets a large backlog drain
  faster. Those chase passes are not billed: the catch-up chain dispatches with
  `metered=False`, so its passes never raise the month's run count the beat and
  the credit gate look at. At the default allowance of 10 the ceiling is exactly 8
  passes (10 // 31 is 0, so the floor rules), while an allowance of 1000 gives about
  32 unmetered passes per lane per 24 h — the monthly tally never sees them.
  Metering that chain waits for the first paid tier. Only a lane the platform pays
  tokens for is metered at all: a curator dialling the local endpoint you connected
  runs unmetered, so its allowance is never spent and, with nothing charged, no
  consumption numeral is shown for it. A count the pre-fix code recorded anyway is
  not a metering verdict: migration 0273 lowers the rows past the allowance to the
  runs the month can prove, and the rest roll off when the calendar month turns.
  The rest of this bullet describes an installation that can charge. Where
  `STRIPE_SECRET_KEY` is set, an account past its allowance stops non-Pro curators dead
  mid-backlog: every later tick logs `curator monthly allowance spent` at WARNING, the
  row reads `skipped_credits`, the stale-watermark watchdog names it — runs spent, the
  allowance, and the events still pending — and Settings → Curators states it on the
  row ("N of 10 free runs used this month") instead of reporting a healthy schedule
  that quietly produced nothing; an account with no allowance (Pro, internal, or
  enterprise) is shown no numeral at all. When the allowance is spent *and* no model
  is runnable, the reason reported is the missing model, because that is the one an
  operator can act on. A lane frozen there never drains on its
  own; on an installation that bills, the levers are the allowance, a Pro grant, or
  moving the curator onto the customer's own endpoint, which takes it out of metering.
  On a box with no `STRIPE_SECRET_KEY` none of those three applies and none is needed:
  the allowance never refuses anything, so nothing is ever stamped `skipped_credits` and
  no curator is frozen there. What is left to steer is how far a backlog is chased per
  day — the catch-up bound named above, which `CURATOR_CATCHUP_PASSES` declares and `FREE_CURATOR_RUNS_PER_MONTH` still floors
  even though it refuses nothing — and each curator's own run ceiling. Raising that
  bound buys real model calls on your own spend, because the chase passes are unmetered.
- `CURATOR_CATCHUP_PASSES` (default 8) is how many catch-up passes one curator lane may chain
  per day: the ceiling a chain plans against and what one lane may claim per 24 h. It is a
  CADENCE bound, not an entitlement — what a pass is metered at, and the `_chain_permitted`
  refusal of a free lane's next paid pass, are untouched by it, so raising it cannot buy an
  allowance. What it buys is inference on your own endpoint: passes of a 570 s wall are about
  14.3 hours of one lane's model time per 16 passes, so 96 is roughly a 63% duty cycle of that
  box, and one lane's arithmetic ceiling is near 147 passes a day. The founder stack declares
  96 for its three celery lanes in `docker-compose.founder.yml`, beside the pin, precisely so
  no self-hoster inherits it; the published compose files never mention it.
- `AGENT_TURN_TIMEOUT_SECONDS` (default 600) is the *default* run ceiling: how
  many seconds one curator run may take before it is cut off and its lane freed.
  A ceiling is a property of a curator, not of the process — Settings → Curators
  gives each one its own "Run ceiling" in seconds, which is what a lane running
  on a slow local model endpoint needs, and clearing it returns that curator to
  this setting. What one pass of that run is handed to read is sized from the wall that number
  resolves to — not the raw ceiling — priced in model turns: the turns the wall can still afford once
  the checkpoint commit has taken its own reserve, times characters-per-measured-turn, floored at the
  loader's 1,024-character smallest batch. Because the
  wall is the ceiling capped by the worker's own task ceiling, no slice is ever sized beyond what the
  worker can let it walk. A ceiling whose wall cannot pay for both the cheapest pass and its own
  commit fails the run loudly and names both prices, rather than draining nothing forever — and the
  number to dial is a CEILING, which is the reserve higher than the wall it becomes: the 1,024-character
  floor costs 334.3 s of wall and the commit wants 30 s of its own, so the wall must reach 364.3 s and
  the ceiling about 394 s at the shipped price. A curator's own number
  bounds both its run and — for any run that is not a drain part — the lock that
  keeps a second run of that curator from starting while the first is still
  going, so the two can never disagree: a lock keyed shorter than the wall would
  expire mid-run and hand the next dispatch a half-finished run. A drain part
  narrows that lock rather than shortening it: the part holds a key of its own
  (`agent-run:<id>:part:<part_id>`) at the same TTL, so several parts of one
  curator run together while an un-parted run still excludes all of them, and what
  bounds how many are live is the endpoint setting below, not this ceiling. Saving a ceiling
  at or above the worker's own task ceiling is refused where it is written — the
  worker would kill such a run before it could record the failure or refund the
  curator credit it had already spent.
- What that ceiling can buy is capped by the worker, and the cap is measured rather than derived:
  with the shipped worker ceilings (`task_soft_time_limit=1500`, `task_time_limit=1800`) the wall
  tops out at 1,470 s, and the highest ceiling the product will actually accept is **1,499** — a
  ceiling of 1,500 or more is refused where it is written, because the worker would kill such a run
  before it could record the failure or refund its credit. The measured pair on the founder's
  personal lane is therefore ceiling 1,499 → wall **1,469 s** → grant **3,658 characters**, which
  leaves the wiki-writing phase 1,427 s against the more than 887 s it was measured needing before
  this card wrote anything down about it. The read phase is not what needs the room: the same lane's
  digest phases answered in 33 to 57 s while their feeds were priced at up to 1,278 s. Nor is the
  ceiling what makes today's feed readable at all: its leading instant is 4,000 characters, over
  even the 3,658-character grant, which is what the skip below is for.
- A personal pass can now be handed a window that opens *after* its watermark, and the material it
  leaves behind is printed. When the leading timestamp group of a feed is by itself bigger than the
  grant the pass' wall pays for — the founder lane was being handed 4,000 characters against a
  grant of 1,784 — that one group is skipped, at most one group per pass, the feed command starts
  just past it, and the commit stands on the last event the pass was actually shown. The forfeit is
  a WARNING, `curation: curator <id> forfeits unread instant <iso> (<n> events, <c> characters):
  it is over the <grant>-character budget one pass can carry, and the watermark will pass it
  unread`, printed once per pass from the read side, so counting those lines counts lost instants;
  a skipped instant is behind the watermark and will never be curated, which is why the line is a
  WARNING and not a debug note. A feed whose leading instant *and* the one behind it are both over
  the grant hands nothing and forfeits nothing, and says `has no passable part` instead — that lane
  is stuck rather than losing history, and the remedy is the ceiling above or a retention decision,
  not a bigger skip. What each phase cost is now one grep too: `curation phase=<phase> agent=…
  session=… seconds=… prompt_chars=… persona_chars=… feed_chars=…` per phase, and
  `curation: personal window agent=… key=… rate=… source=… wall=…s priced_share=…s grant=…
  characters=… events=… until=…` once per personal pass before it reads a character.
- `CURATOR_DRAIN_MAX_CONCURRENT_TURNS` (default 3) is the drain fan-out's one bound: how many of
  one curator's drain turns may be live at ONE endpoint at once — part turns and the compose pass
  together — counted in a Redis claim set keyed by that endpoint, or by the harness's name when the
  platform funds it, so a second worker pointed at the same box cannot widen it. The value is a
  measurement: three rounds on one local endpoint (`model=qwen`, 512 max tokens,
  `thinking_token_budget=2048`) on 2026-11-15 gave 4,8 tok/s aggregate at N=1 with a per-turn wall
  of 107,6 s, and 13,5 tok/s at N=3 with walls of 107,3 / 108,7 / 113,5 s — the latency of one turn
  all but unchanged. The N=6 round of the same session read 52,8 tok/s with walls of 35 to 58 s, and
  it ran on a freer box: it is named here as contaminated, not as an argument for 6. A part the full
  gauge refuses spends no turn and no credit, prints `guard=endpoint_pressure` carrying the gauge
  size and the limit, and hands its slot claim straight back, so the next dispatch takes it the
  moment room frees. What the bound does not do is widen a part's allowance: each part is handed the
  same grant an un-parted run is — the wall less the commit's own 30 s reserve, per turn, neither
  divided nor reused — so a fan-out spends more of the month's turns, never a larger slice per turn.
  The worker pool is a second and often lower supply, reported rather than reconciled: the heavy lane
  runs `--concurrency=2` per replica, so parts can stand ready while the pool works two.
- `CURATOR_DRAIN_PART_HOURS` (default 24) is the other knob and it is a granularity, not a
  measurement: how wide a window one part reads, so a smaller number means more parts and more
  compose work rather than a faster endpoint. Windows also never drift off that granularity: a
  window is opened only once its close has been reached, so every seam stays `from + part_hours`
  from the instant the drain was seeded, and the head of a plan waits for its seam rather than
  opening a window of the leftover minutes. A clamped window is what the live proof caught killing
  a run: the next pass opens from that mid-second seam and part ids name seconds, so it reaches
  for an id the neighbouring slot already spent. Neither knob makes a big backfill quick, and the
  arithmetic is worth stating instead of smoothing: the owner backlog measured from 2026-09-02 to
  2026-11-15 is 4 191 130 events across 549 421 829 characters, about 140M tokens, which at an
  optimistic 50 tok/s aggregate is roughly 32 days of wall. The fan-out trades 162 days for days,
  not for minutes.
- Operational alerts stay off unless you finish them: set both `ALERT_SLACK_*`
  variables in `.env` and install the Stash Slack bot into that workspace, so
  the team has a `slack_bot_installs` row and its bot is in the alert channel.
  Two variables with no installed bot raise on every delivery attempt and page
  you nothing. Either way each worker lane names its mode at startup — WARNING
  when this installation sends no operational alerts — and `docker logs
  <worker-lane> | grep 'operational alerts'` shows it.
- Every decision a curator dispatch makes about a turn is one line on the worker
  lane that made it, and each begins `agent schedule:`. A turn names the lane it took
  (`lane=scoped` or `lane=personal`) when it starts and the wall seconds it took when
  it ends. A personal run that commits no pass (a cut, or a turn stopped before it
  finished) still leaves only those two lines as evidence it happened; a personal run that
  commits a drain pass also writes a `curator pass=` line (see below), so a drain that is
  making progress is now visible on the log and not only in the row. Drain parts speak on lines of
  their own instead — `drain part=<id> agent=<id> committed_through=... characters=... wall=...s` for
  a part that committed, with `drain coordinator` and `drain compose` naming the cut and the fold —
  and a part run prints neither `turn started` nor `turn finished`, because those two lines are the
  un-parted run's bracket and a part must not be counted inside it. A pass that spends no turn names the guard
  that refused it with a `guard=` token (`curator_off`, `not_pro`, `no_pending_input`,
  `catchup_ceiling`, and for a drain part `endpoint_pressure` beside `claim_held`,
  `lane_lock_held`, `needs_auth`, `user_gone` and `slot_absent_or_done`), and a catch-up chain that stops mid-backlog names the condition
  that ended it (`outcome_not_ran`, `no_progress`) — the cost decision is the one guard
  that answers in both places, on the line that also carries `metered=`, `is_pro=` and
  the verdict. Which lane a pass runs on is never a guard: what a chained pass costs is
  the question, and a lane with no workspace simply answers it as an unmetered one. So
  a decline, a chain that finished, and a crash are three different lines. The one
  command that reads them:

  ```bash
  docker compose -f docker-compose.prod.yml logs heavy-worker | grep 'agent schedule:'
  ```

  All four celery programs — `worker`, `heavy-worker`, `sync-worker`, `beat` — install
  the same stderr handler the API process installs, at boot, through one function in
  `backend/logging_config.py`, so a record from a worker prints the same way one from
  the API process does and names the module that emitted it:
  `2026-11-15 09:00:00,000 INFO backend.tasks.agent_schedules: agent schedule: …`.
  All four also run with `--loglevel=info`, so an empty window in that log is neither a
  level problem nor a lost record: it means the run never reached a line worth
  printing. Both curator lanes write one `curator pass=` meter line per committed pass,
  which is the line to watch while a backlog drains. The scoped line carries the granted
  `budget=`, the calibrated seconds-per-character, and what the pass's own characters
  actually cost (`seconds_per_character=`), so calibration drift reads off the worker log
  without dialing the model. The personal lane prints three lines, and only one of
  them is its calibration row's twin:

  * `agent schedule: personal curator <id> committed through=… characters=… budget=…` is
    the committed pass, and its `characters=` IS the characters the calibration row filed.
    The row's seconds are the `wall=` of that same run's bracket line, `agent schedule:
    curator <id> turn finished lane=personal wall=…` — one reading of the clock feeds both,
    so a row can be checked against the log without a database query. Its unit is the whole
    turn's seconds over the digest's characters, which `docs/testing.md` states in full.
  * a pass the wall cut commits nothing, so it prints no `characters=` at all: from inside
    its own failure branch it files the GRANT over the wall it spent, a lower bound on what
    a character cost (shielded on the manual path, because that branch re-raises).
  * `curator pass=1 agent=… committed_through=… lane=personal` is the DRAIN-rate line, and
    it counts nothing by design: a drain pass is sized by `drain_budget_of`, reports only,
    and never carried a character count. It carries no `budget=` or
    `seconds_per_character=` because the function that prints it knows only the boundary it
    advanced to and the lane it stands on.

  A personal turn a human stopped files no row at all: `RunCeilingExceeded` now says who cut
  the run, and the seconds of a feed somebody abandoned are not what a character costs. That
  run still reports `failed`, still alerts, and teaches the price nothing.

```bash
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Caddy provisions HTTPS. Open `https://app.example.com/login` using your domain,
then check the backend and connect your CLI:

```bash
curl --fail https://app.example.com/health
uv tool install stashai
stash signin --api https://app.example.com
```

Compose generates and persists the integration-encryption key when
`INTEGRATIONS_ENCRYPTION_KEY` is unset. Set it explicitly if you manage secrets
outside Compose. Inspect startup failures with
`docker compose -f docker-compose.prod.yml logs backend frontend`.

To upgrade, back up your database, review the incoming changes, then run:

```bash
git pull --ff-only
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

The localhost Compose override currently references a `collab` service without
an image or build definition and fails configuration validation. Use the local
source setup above for running on localhost.
