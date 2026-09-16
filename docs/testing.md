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
| `test_curator.py` | Curator provisioning, schedule, gate, feed, page writes, and the watermark: an advance is compare-and-set on the position the run read, so a moved-under run fails loud instead of swallowing an ingest rewind; monotonic within a match, a refused clamp is logged, `full_history` never clears the stored position, one run per agent at a time |
| `test_curator_feed_scoping.py` | Which events each wiki may read — the internal wiki everything, the external wiki only sessions of end users who share — and the gate agreeing with that feed |
| `test_curator_event_identity.py` | One event, however often its session was re-pushed: the identity the feed, the gate, the watermark boundary, and the backlog share; distinct-vs-raw backlog; the honest zero (a leftover of only the curator's own transcripts is not work); drain equality; and both endpoints publishing the honest number |
| `test_first_day_curator.py` | First-day curator tick, and the ingest rewind re-opening the cursor that imported history predates |
| `test_agent_schedule_alerts.py` | Beat dispatch, designed skips, and the stale-watermark / failing-curator alerts |
| `test_developer_platform.py` | Developer console curator run and backfill dispatch, and the ingest rewind staying inside the wiki whose feed can read the events |
| `test_agent_endpoints.py` | Multiple local model endpoints per user: connects APPEND named rows, the default is the oldest box, `agents.credential_id` pins one box for every turn of a run (writer and digest dial the same `base_url`), the endpoint API probes before storing and refuses to delete a box a curator still points at |
| `test_migrations.py` | Alembic upgrade/history smoke tests |
| `test_startup_logging.py` | App startup owns root logging: one INFO handler on stderr, and the migration runner must not disable app loggers |
| `test_dockerfile_pi_free.py` | The shipping backend image stays pi-free: the named pattern matches nothing in `backend/Dockerfile`, matches reported by line number; the node+pi bake lives only in the dogfood overlay |
| `test_collab.py` | Sharing, copy, and collaboration on user-scoped objects |
| `test_session_folder_share_wiki.py` | Per-project shared-wiki opt-in: starts off, only the switch flips it |
| `test_websocket.py` | ConnectionManager delivery, dead-socket cleanup, pg_notify, oversized fallback |

**The curator watermark (`agents.curated_through`) has several writers and one CAS-protected
advance.** `mark_curated` — the completion write for both the personal-memory curator and the
project-folder curators — lands its write only while the stored value is still the position
the run *read* when it started (`WHERE curated_through IS NOT DISTINCT FROM <read position>`);
inside that match, `greatest(...)` keeps the write monotonic, so a matched read whose proposal
sits behind the stored value clamps rather than regresses and the refused position is logged
rather than dropped. If anything moved the marker under the run — a rewind or an overlapping
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
before it. One forward writer still bypasses the CAS: the scoped-workspace commit in
`scoped_curation_service.run_workspace` stamps its own watermark unguarded under the
permission lock (not even the monotonic `greatest` applies there); it is left as found and
needs its own card.

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
