# Testing

## Backend

- **Framework:** pytest + pytest-asyncio
- **Database:** Requires a Postgres instance with pgvector (`pgvector/pgvector:pg16`)
- **Config:** `pytest.ini` at the repo root

### Running tests

```bash
# Ensure the test database exists (standalone pgvector container on 5432)
docker start stash-pg 2>/dev/null || docker run -d --name stash-pg -p 5432:5432 \
  -e POSTGRES_USER=stash -e POSTGRES_PASSWORD=stash -e POSTGRES_DB=stash \
  pgvector/pgvector:pg16
psql postgresql://stash:stash@localhost:5432/postgres -c "CREATE DATABASE stash_test"

# Run migrations and tests
DATABASE_URL=postgresql://stash:stash@localhost:5432/stash_test \
  python -m alembic upgrade head

DATABASE_URL=postgresql://stash:stash@localhost:5432/stash_test \
TEST_DATABASE_URL=postgresql://stash:stash@localhost:5432/stash_test \
  python -m pytest backend/tests/ -v
```

### Test suites

| File | Covers |
|------|--------|
| `test_auth.py` | Registration, login, API key auth, password validation |
| `test_permissions.py` | Private-by-default access, owner read/write, share grants, publish records |
| `test_internal_email_domains.py` | The internal-account domain list as one env setting: defaults equal the fixed company domains, the env string replaces them (normalized, empty grants nobody), the kill switch still wins, and admin analytics follows the same list |
| `test_tools_and_chat_domains.py` | The Tools-and-Chat `/users/me` flag driven by its own env domain list — false for unlisted domains, true for the defaults and env-listed ones |
| `test_webhooks.py` | SSRF URL validation, secret hashing, delivery logic |
| `test_curator.py` | Curator provisioning, schedule, gate, feed, page writes, and the watermark: an advance is compare-and-set on the pair the run read, so a moved-under run fails loud instead of swallowing an ingest rewind; monotonic within a match on the encoded pair, a refused clamp is logged, `full_history` never clears the stored position, one run per agent at a time; the fence holds the agent row until its commit lands and a failure after the fence publishes neither the marker nor the window; the stored pair is CHECK-constrained — an event half with no instant is rejected at the database |
| `test_curator_watermark_position.py` | The pair itself: the `ahead` truth table in both directions (a whole instant is ahead of any position inside it), and the CAS on the pair — a mid-tie advance is refused under a whole-instant watermark, an advance to the next event at the same instant lands, an advance must read the position it started from, a tie larger than the feed cap drains to zero, and an ingest rewind clears the event half |
| `test_curator_feed_scoping.py` | Which events each wiki may read — the internal wiki everything, the external wiki only sessions of end users who share — and the gate agreeing with that feed |
| `test_curator_event_identity.py` | One event, however often its session was re-pushed: the identity the feed, the gate, the watermark boundary, and the backlog share — with a discriminating test per identity part, so removing any one reddens a named test (`session_id`: two sessions saying the same words at one instant; `event_type`: a `tool_use` and its `tool_result` sharing one instant and payload; `created_at`: a re-asked turn; `md5(content)`: three contents under one batch timestamp), and `agent_name` pinned OUT of it so a re-import relabelled by another client stays one event; distinct-vs-raw backlog; the honest zero (a leftover of only the curator's own transcripts is not work); drain equality; an instant holding more events than the feed cap draining over consecutive runs instead of plateauing; and both endpoints publishing the honest number, `since`+`since_event` honoured by feed and backlog alike (a malformed or orphan event half is a 400), plus the render pins for the feed command the prompt hands the curator — it carries `--since-event` exactly when the lane stands inside an instant and is byte-identical otherwise |
| `test_curator_backlog_drain.py` | The beat that drains a behind curator instead of waiting a calendar day: most-behind lanes re-woken first, at most two per tick, a lane busy or with a failed last run stepped past to the nightly tick, free-plan lanes out of monthly runs skipped, no runnable credential box skipped, no pending material skipped; the drain and the nightly tick share one meter per lane, only curator lanes are woken, and a folder lane is gated by its own folder and nothing else. The dispatch gate is the same lane-aware question the run asks (`_require_run_auth`), so a folder lane drains on its own pinned box with the backend key absent, and a lane that could only fail at — a scoped lane with neither provider, no backend key and no box — is skipped at dispatch instead of spending a metered run and one of the tick's two slots, while a scoped lane with a box connected is dispatched |
| `test_session_list_filters.py` | The Sessions list's server-side prefilters (`folder_id`, `agent`, `q`, paging `has_more` with an offset that partitions the (last_event_at DESC) order), the unconditional exclusion of the curator's own `agent-curate-` run transcripts — no parameter asks for them back, they survive paging, and they stay resolvable through the detail route (hidden, not deleted) — and the byte-identity guard that pins the shared clause builder's rendering and `_CURATOR_FEED_ELIGIBILITY` so the list can adopt the feed's classification without moving the feed's SQL by one byte |
| `test_scoped_curation.py` | The shared-workspace (developer-platform) curator lane: which material each scope may read, that a scoped run never reaches a credential-bearing sprite, and that its watermark commit obeys the same fence as every other forward writer (an overlapping advance or a concurrent reset to never is refused loud; a backfill advances the position it read from) |
| `test_folder_curators.py` | One curator lane per project folder — its watermark seeded at the folder's first event, its model/endpoint pin digest-round-tripped, an inherited pin resolving byte-equal to the default curator's — and the lane that decides which curation it gets, asked once in `scoped_curation_service.workspace_for_agent`: a folder-bound curator reads one project's feed and writes one wiki folder, so it keeps the credential its own row names even under an owner with an activated developer workspace (gate passes with no backend `ANTHROPIC_API_KEY`, the resolved endpoint is the box it pins, the prompt is the folder prompt), while the External-wiki curator and that workspace's own Memory curator stay on the scoped dispatch, whose provider is now answered by the backend's own configuration rather than by one key (`test_llm_provider.py`) |
| `test_curator_session_id_width.py` | The digest phase of a two-phase run must carry a session id that fits the column it is written into: the production `build_digest_turn` builder under both stamp shapes (the minute beat, and the manual dispatch's second precision, which exists so a manual run never shares a session with the minute's scheduled run) is pinned by exact shape and length against the real `history_events.session_id` limit read from `information_schema`; the real writer `memory_service.push_event` stores the 71-character manual-shape id byte-for-byte (the pre-`0220` founder-stack crash was asyncpg's `StringDataRightTruncationError`), and the ingest model's `max_length` must equal the column width, so `POST /events` can never accept what the column would reject |
| `test_first_day_curator.py` | First-day curator tick, and the ingest rewind re-opening the cursor that imported history predates |
| `test_agent_schedule_alerts.py` | Beat dispatch, designed skips, and the stale-watermark / failing-curator alerts |
| `test_source_sync_claims.py` | Sync-claim protocol and the stall watchdog's decision rule: `sync_alerted_at` is written only once the watchdog resolved a source — alert delivered, or delivery deliberately suppressed because this installation sends no operational alerts (a mode announced at worker startup, not per tick); a failed delivery is not a resolution — it propagates and leaves the throttle untouched so the next tick retries. The ordering-guard test goes red if the throttle write is moved above the delivery |
| `test_developer_platform.py` | Developer console curator run and backfill dispatch, and the ingest rewind staying inside the wiki whose feed can read the events |
| `test_llm_provider.py` | Which provider a backend completion dials, answered once by configuration and never by reachability: a backend `ANTHROPIC_API_KEY` answers for the hosted product and a connected box sees neither its probe nor a request, with no key the endpoint the console stored answers and the Anthropic client is never built, and a stack with neither is a refusal naming both. The local route is tested against an HTTP server on a real port, because what it promises is bytes — the `/chat/completions` suffix joined once to a base URL that already ends in `/v1`, the bearer header, tools and tool results in chat form, the reply mapped back into Anthropic blocks — and an answer that cannot be executed fails loud naming the endpoint (no choice, no finish reason, arguments that are not JSON, a proxy's 502 HTML). Resolution is tested against stored credentials, not mocks of them: the row's `credential_id` picks which box while the credential decides how to reach it, `model_id` may override only the model id, a pin to a cloud key is refused instead of drifting to some other box, and a box that is off is refused with the transport's own reason before the run spends its one metered attempt. The acceptance proof runs `scoped_curation_service.run()` on the External wiki over the curator suite's dataset: the scoped tool loop closes on the documents the backend selected, on one model id for the whole run |
| `test_agent_endpoints.py` | Multiple local model endpoints per user: connects APPEND named rows, the default is the oldest box, `agents.credential_id` pins one box for every turn of a run (writer and digest dial the same `base_url`), the endpoint API probes before storing and refuses to delete a box a curator still points at |
| `test_migrations.py` | Alembic upgrade/history smoke tests |
| `test_migration_chain.py` | The migration graph as a property of the directory, not of one file: one head, no revision id claimed twice, every `down_revision` resolvable, and no file booking an id a deployed build already stamped (`shipped_revisions.json`, keyed by `(id, slug)`, because a stamped database cannot say which of two same-numbered revisions it ran). Every manifest rule is shown red on the mistake it exists for, and a graph alembic cannot resolve comes back as an error naming the fix rather than a traceback |
| `test_migration_data_convergence.py` | The founder-lineage convergence node runs through its own `converge()`: a database that never converged gets trunk `0203`'s curator-log archive and `0204`'s shared-wiki revocation, a second run changes nothing, and a database that ran trunk `0203`–`0207` the normal way does no data work at all |
| `test_startup_logging.py` | App startup owns root logging: one INFO handler on stderr, and the migration runner must not disable app loggers |
| `test_dockerfile_pi_free.py` | The shipping backend image stays pi-free: the named pattern matches nothing in `backend/Dockerfile`, and a failure reports the matched Dockerfile line texts; the node+pi bake lives only in the dogfood overlay |
| `test_docker_dogfood_compose.py` | The dogfood stack actually selects the pi-carrying image: `docker-compose.dogfood.yml` builds `backend/Dockerfile.dogfood` through a required `BASE_IMAGE` (no default, so an unbuilt base fails loud), one local tag covers every service the prod file pins to the backend image — derived, so a newly added queue cannot be stranded — the overlay declares no service the base file lacks, and both self-host files stay pull-only with no dev-only service or port |
| `test_collab.py` | Sharing, copy, and collaboration on user-scoped objects |
| `test_session_folder_share_wiki.py` | Per-project shared-wiki opt-in: starts off, only the switch flips it |
| `test_websocket.py` | ConnectionManager delivery, dead-socket cleanup, pg_notify, oversized fallback |

**The curator watermark is a position, not a timestamp: `agents.curated_through` plus
`agents.curated_through_event_id`, and it can stop *inside* an instant.** The feed already
orders events by `(created_at, id)`, so a watermark that names only the instant cannot express
where a run that hit the feed budget actually stopped — it was pushed back 1µs, re-admitting the
whole tied instant forever, and an instant holding more distinct events than the budget could
never be consumed at all. `curation_service.Position(at, event_id)` is the one encoding of that
order (`ahead()`, and `_position_sql`/`position_bound` for SQL), and the same clause bounds the
feed, the gate, the backlog, and the watermark boundary — one clause, four readers, no mid-tie
branch in Python and no second encoding (`greatest(...)` and the ±1µs step are gone). Because the
position's event half is COALESCE'd to the max uuid, a whole-instant position collapses to
exactly the old `created_at > at` scan; the event half exists only to split an instant, so the
cursor is applied to an event's *canonical* row (the group's lowest id) everywhere at once — feed,
backlog and gate — or a consumed group with a copy above the cursor would count as work and fire
the gate while the feed showed nothing. The backlog therefore scans from the cursor's *instant*
and asks the sharp question per row inside its `GROUP BY`, which is what keeps `raw_rows`
counting re-imported copies while `distinct_events` collapses them.

Every writer sets both columns in one statement, and the writers that move the position
backwards clear the event half in the same write, so a stored pair is never half-moved (the
database CHECK refuses an event with no instant). `mark_curated` — the completion write for both
the personal-memory curator and the project-folder curators — lands its write only while the
stored pair is still the position the run *read* when it started
(`WHERE (curated_through, curated_through_event_id) IS NOT DISTINCT FROM (<read pair>)`);
inside that match, the clamp compares the encoded pair — a whole instant ahead of any position
inside it — so a matched read whose proposal sits behind the stored value clamps rather than
regresses and the refused position is logged rather than dropped. If anything moved the marker under the run — a rewind or an overlapping
run — the completion is refused loud as `CuratorWatermarkConflict` (raised through
`_run_curator_now`, recorded via `mark_run_failed` on the beat path) and the moved value,
including any deliberately re-opened window, survives untouched for the next run. Pinned by
`test_curator.py`'s `test_a_pre_rewind_completion_cannot_swallow_a_reopened_window` (the
founder interleaving: read X, rewound to Y < X mid-run, the stale advance is refused and the
window stays open), its end-to-end variants `test_a_mid_run_ingest_rewind_fails_the_curator_run_loud`
and `test_the_scheduled_curator_run_fails_loud_when_the_watermark_moves`, plus
`test_mark_curated_cannot_walk_the_watermark_back`,
`test_stale_completion_cannot_regress_an_overlapping_run`,
`test_full_history_backfill_cannot_regress_an_advanced_watermark` and
`test_a_refused_watermark_advance_is_visible_in_the_log`. Writes sanctioned to move the
marker outside the CAS are deliberate re-reads, not run bookkeeping: the ingest rewind in
`memory_service` (importing history that predates the cursor has to reopen it — pinned by
`test_first_day_curator.py::test_late_import_reopens_curation`, per-wiki scope by
`test_developer_platform.py::test_ingest_rewind_stays_inside_its_wiki`), the folder-curator
rewind in `agent_service.rewind_folder_curator_for_sessions` (filing sessions into a curated
project reopens that project curator's position —
`test_folder_curators.py::test_filing_old_sessions_reopens_the_folder_position`), and the
shared-wiki revocation reset in `end_user_service._archive_shared_wiki`, which NULLs the
external curator's position so re-sharing re-curates from the start (migration `0204`
backfilled the same reset for already-revoked wikis — `test_curation_optout_migration.py`).
The compare-and-set is what makes each of those moves un-clobberable by a run that read
before it, and the contract is **one primitive for every forward writer**: the Memory and
project-folder completions (`agent_service.mark_curated`) and the shared-workspace commit
(`scoped_curation_service.run`) all execute the same fenced statement —
`agent_service.advance_watermark` — on the transaction that publishes their writes, anchored on
the position their dispatcher loaded (never a re-read of the row the run finishes on), so the
agent row stays locked from the match until that commit lands and no forward writer holds a
looser rule. A scoped run therefore fails loud on a mid-run move instead of stamping over it:
`test_scoped_curation.py`'s `test_a_scoped_run_cannot_discard_an_overlapping_run_watermark_advance`,
`test_a_scoped_run_cannot_swallow_a_concurrent_reset_to_never`, and
`test_a_full_history_scoped_run_advances_the_position_it_read` (the backfill state, which
must advance rather than conflict). The window the fence is responsible for is pinned at its two
edges by `test_curator.py`'s `test_the_fence_holds_the_agent_row_until_its_commit_lands` (a rival
that starts its fenced commit while the first transaction is open provably blocks on it and is
then refused) and `test_a_failure_after_the_fence_publishes_neither_the_watermark_nor_the_window`
(the crash boundary: a raise after the matched fence rolls the stamp back and leaves the window
still offered).

**The stored pair has to survive the trip through the run's own feed command.** A sprite curator
reads its changes through a command the server renders into its prompt —
`stash changes --since <iso> [--since-event <uuid>] --json` — so `prompts.curator_changes_cmd`,
the CLI option, and `GET /api/v1/me/changes` all carry both halves to the same `Position`, for
the feed and the backlog alike. A position the prompt could not name would turn a mid-tie
watermark into a silent permanent skip: the run would ask for `created_at > T`, never see the
tail of the tie, and `complete_through` would count it as read anyway. Pinned by
`test_curator_event_identity.py`'s `test_the_changes_endpoint_reads_from_the_event_half_of_a_position`,
`test_a_position_named_by_instant_alone_reads_the_whole_instant`,
`test_the_feed_command_the_prompt_hands_the_curator_carries_both_halves` and
`test_a_mid_tie_lane_schedules_its_run_with_the_pair`, and on the CLI side by
`cli/tests/test_changes_wiki_flag.py`. Deployment skew is loud rather than silent either way: an
old CLI meets an unknown option and the run fails visibly, while an old server ignores the extra
query parameter and re-presents the tie tail (redundant, never lossy).

### Conventions

- Each test gets a clean database via `TRUNCATE CASCADE` after every test function.
- Use `unique_name()` from `conftest.py` for non-colliding usernames.
- Mock external APIs (Anthropic, OpenAI) — never call real LLM endpoints in tests.
- Targeted single-file runs need `--no-cov`: `pytest.ini`'s `addopts` enforces a coverage floor that one file cannot meet on its own.

---

## Frontend

- **Framework:** Vitest + @testing-library/react + jsdom
- **Config:** `frontend/vitest.config.ts`

### Running tests

```bash
cd frontend
npm test          # single run
npm run test:watch  # watch mode
```

### Conventions

- Co-locate tests with source files: `{module}.test.ts` or `{module}.test.tsx`
- Use `describe` / `it` blocks
- Use `vi.fn()` / `vi.mock()` for mocking

---

## CLI tests

- **Framework:** pytest, no database
- **Config:** `cli/pytest.ini`
- **CI:** the `cli-test` job in `.github/workflows/test.yml` (ubuntu-latest, Python 3.12)

### Running tests

```bash
uv venv -p 3.12 && uv pip install -e .          # once per checkout
uv pip install -r backend/requirements-dev.txt  # pytest, pytest-cov, ruff
source .venv/bin/activate

python -m pytest cli/tests --no-cov
```

Invoking the suite by path anchors `rootdir` at `cli/`, so `cli/pytest.ini` replaces the root
config's backend-scoped coverage gate with a CLI-scoped one (`--cov=cli --cov-fail-under=45`).
A targeted single-file run still needs `--no-cov`: one file cannot meet a whole-package floor.

### Environment integrity

`cli/tests/conftest.py` refuses to collect unless the interpreter running it **is** this
checkout's environment. That is deliberate. Past its pin, `typer` vendors a private click, so
`MissingParameter` stops being a `click.exceptions.UsageError`, escapes the boundary catch in
`cli.main`, and prints raw tracebacks — two dozen failures that describe the host interpreter
rather than the product, and that an operator can only "fix" by breaking working code. A stale
`pip install -e .` left pointing at another, usually deleted, checkout is the same failure
wearing a different face: the suite validates one tree while executing a different one.

The guard checks exactly two things:

- the interpreter's `typer` equals the pin in `pyproject.toml`, which the guard reads at run
  time — the pin has one home, so bumping it never means editing the guard;
- `cli` and `stashai` resolve to files inside this checkout, so an editable install aimed at
  another tree cannot report a verdict on this one.

On mismatch it aborts before collecting anything, naming the observed value and the remedy
(`uv venv -p 3.12 && uv pip install -e .`). There is deliberately no way to waive it: a warning
beside two dozen misleading failures is still misleading. `cli/tests/test_env_guard.py` locks
all of that, the remedy string included.

---

## Plugin tests

- **Framework:** pytest, no database
- **CI:** the `plugin-test` job in `.github/workflows/test.yml` (ubuntu-latest, Python 3.12)

### Running tests

```bash
python -m pytest plugins/tests --no-cov
```

`--no-cov` is required. The root `pytest.ini` sets `addopts = --cov=backend --cov-fail-under=30`,
so a plugin run without it fails on a backend coverage floor it has nothing to do with.

### Hermetic PATH in `test_ensure_cli.py`

`ensure_cli.sh` finds `uv` through `PATH`, so the harness gives the child `PATH` set to its
sandbox directory and **nothing else**. The sandbox is populated from an explicit allowlist —
`bash`, `awk`, `sleep`, `touch` — symlinked in by `_hermetic_bin_dir`. Never widen it with a
host directory "so the script can find tools": that is what made `uv` present-or-absent depend
on the developer's machine, and the assertion meant to prove *no uv → fail loudly* silently
checked the wrong branch while forking a real `uv tool install` on the host.

- A utility the script needs belongs on the allowlist, not on `PATH`. `sleep` is listed even
  though no test asserts on it: without it the uv stub's `sleep 5` becomes "command not found"
  and both "must not block session start" timing tests pass vacuously.
- `sh` and `env` are deliberately absent — stub shebangs name their interpreter by absolute
  path, which the kernel resolves without `PATH`. Every extra allowlisted binary is a leak.
- `find_uv()` also stats `uv` at a few **absolute** paths (`/opt/homebrew/bin/uv`,
  `/usr/local/bin/uv`), which a `PATH` sandbox cannot neutralise. `_run` asserts those are
  absent rather than relaxing the assertion; if that guard fires, the fix belongs in
  `find_uv()`'s candidate list.

### Documented commands must parse — `test_guidance_cli_invocations.py`

`test_assets_in_sync.py` proves `plugins/<agent>-plugin/` and `stashai/plugin/assets/<agent>/` are
byte-identical. Two identical copies of a command the CLI rejects satisfy it. That is how pi shipped
`stash share` taking a session-id positional: the parser refused it at exit 2, before auth, so pi
agents could not share a session at all while every parity check stayed green.

This guard asks the real parser instead. It resolves each documented `stash ...` form to its command
and builds a click context, asserting the parser raises nothing — and it never invokes a command
body, so it needs no auth, no network, and no backend. The corpus is every inline-backtick span and
fenced-block line in the plugin guidance, its shipped mirrors, and the repo's own `CLAUDE.md`, plus
the runtime strings `cli/main.py` composes (`AGENT_GUIDANCE_PROMPT`, the project `CLAUDE.md` block,
the Claude hook's `CONTEXT`) — agents load those too, so failures name the string, not a file path.
Cursor
ships its guidance as `.mdc`, so the corpus takes that extension too; a `.md`-only glob leaves an
agent's documented forms unguarded, which is the same blind spot that shipped the pi bug.

Two ways this file goes green while checking nothing, both asserted rather than assumed:

- **The group walk has to descend.** A group defers subcommand resolution until invoke, so asking
  only the root command to judge `stash skills create …` accepts nearly anything. The nested
  rejection assertion and `EXPECTED_GUIDANCE_FILES` fail if the walk or the corpus shrinks.
- **Usage errors are matched by message, not class.** Past its pin, `typer` vendors a private click
  (see [Environment integrity](#environment-integrity)), so its usage error is not a
  `click.exceptions.UsageError`. An `except` naming the click class stops matching and every form
  passes; both spellings report through `format_message()`, which is what the guard catches.

The fix goes in the document, never the parser: when a command gains a required option, every
guidance file that predates it is wrong. Restoring a rejected form and watching this file name both
the document and the parser's own reason is the check that the guard still bites.

### Every supported agent must be taught the Skill model — `test_guidance_coverage.py`

Parsing is not teaching. The guard above proves a documented command is one the CLI accepts; it says
nothing about whether any agent was ever told the concept exists. That is the failure mode this file
exists for: STAS-210 shipped the Skill/folder-sharing prose to a third of the supported agents
(`grep -ric skill` answered 0 for both `gemini-plugin/GEMINI.md` and `hermes-plugin/HERMES.md` while
the commit claimed 13/13) and every other check — byte parity, parse, hook execution — stayed green.
The symptom only surfaces later, when a founder shares a published skill and the agent on the other
end has never heard of one.

One canonical block lives in `stashai/plugin/guidance.py` (`SKILL_MODEL`, anchored by its
`<!-- stash:skill-model -->` marker so a copy can be traced to the module rather than to a
paraphrase that drifted). `CHANNELS` is the registry of which channels exist, and every one of them
must contain the block **verbatim and exactly once**:

- **Static files** (`STATIC_CHANNELS`) — the guidance a plugin ships: the `AGENTS.md` files for codex,
  opencode and pi, `GEMINI.md`, `HERMES.md`, Cursor's `stash.mdc`, and Claude's plugin `CLAUDE.md`.
- **Composed runtime strings** (`COMPOSED_CHANNELS`) — text the CLI or a hook builds at import time,
  so no file on disk contains it: the project `CLAUDE.md` block `_CLAUDE_STASH_CONTEXT`, the
  session-start hook's `CONTEXT`, and `_OPENCLAW_GUIDANCE` (openclaw ships no guidance file by design).
  A map that read only files would call those agents untaught, and would happily accept a file-based
  paraphrase instead — agents load the string, so the guard reads the string.

`AGENT_GUIDANCE_PROMPT` (printed by `stash prompts agent-guidance`, loaded by every agent at runtime)
and the repo's own `CLAUDE.md` get their own assertions for the same reason: they are channels, not
copy.

Two invariants keep the guard from quietly rotting into a green no-op:

- **The map is keyed to `cli.main._SUPPORTED_AGENTS`.** Adding a supported agent without registering
  the channel it actually loads fails `test_channels_are_wired_to_the_supported_agent_table`.
  Registration is mandatory, not a courtesy.
- **Nothing stale survives beside the block.** A channel embedding it twice, or keeping a superseded
  paraphrase under a second `## What a Skill is` heading, fails the count and heading assertions.

The shipped mirrors in `stashai/plugin/assets/<agent>/` (what `stash connect` hands out) need no
entry here: `test_assets_in_sync.py` pins them byte-for-byte to the sources above. Parity alone
cannot see a symmetric edit — delete the section from *both* copies and parity still holds — which is
why coverage asserts on the source, where such a strip does fail. Claude is the case that teaches
both rules: its mirror carries only `scripts/*.py` because its `CLAUDE.md` arrives through the
marketplace, so nothing read that file until STAS-255 registered it, and stripping its Skill section
had passed 343 tests.

Prove it still bites the same way every other guard here is proven — strip one channel, watch it
name the agent, restore:

```bash
python - <<'PY'
from stashai.plugin.guidance import SKILL_MODEL
import pathlib

p = pathlib.Path("plugins/gemini-plugin/GEMINI.md")
p.write_text(p.read_text().replace("## What a Skill is\n\n" + SKILL_MODEL + "\n\n", "", 1))
PY
python -m pytest plugins/tests/test_guidance_coverage.py --no-cov   # gemini: ... does not ship ...
git checkout -- plugins/gemini-plugin/GEMINI.md                     # back to green
```

What this guard cannot claim: it proves an agent was *told*. Whether that text ever landed on a
developer's machine is a different failure — installed plugin artifacts do not always converge with
what the repo ships, and closing that is STAS-248's convergence work, not this guard's.
