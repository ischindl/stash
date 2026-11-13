# Testing

## Which environment a verdict belongs to

A test run judges the tree its interpreter installed, not the directory you happened to `cd` into. The
`stash` on `PATH` is a released PyPI build (`uv tool install stashai`, self-updated by the plugin hooks),
so it reports on the release it came from, not on a working tree. Another checkout's environment -
usually the primary one, sometimes a card worktree that has since been removed - judges that checkout.
`cli/tests/conftest.py` refuses to collect when `typer` is off its pin or when `cli` and `stashai`
resolve outside the collecting tree, because a stale editable install made the suite report a tree it
was never running (STAS-178 spent a card cycle "fixing" code that was not wrong).

Verify in the tree that carries the change, in that tree's own environment:

```bash
cd "$(git rev-parse --show-toplevel)"
uv venv -p 3.12 && uv pip install -e .            # once per tree
uv pip install -r backend/requirements-dev.txt    # pytest, pytest-cov, ruff
.venv/bin/python -m pytest cli/tests --no-cov -q
.venv/bin/ruff check backend/ cli/ && .venv/bin/ruff format --check backend/ cli/
```

Before trusting a verdict, print whose code that interpreter judges:

```bash
head -1 .venv/bin/stash
.venv/bin/python -c "import cli, stashai; print(cli.__file__, stashai.__file__)"
which stash
```

Two failures those lines exist to catch, both seen on a real box:

- A console script whose shebang names a **different tree's** interpreter, so `.venv/bin/stash` runs a
  removed worktree's code while the shell stands elsewhere. Re-run the shebang check after every
  install, and prefer `.venv/bin/python -m pytest` over a bare `pytest`.
- An exact pin moved by a top-up install. `backend/requirements-dev.txt` starts with
  `-r requirements.txt`, which carries `celery[redis]>=5.4` against `pyproject.toml`'s exact
  `click==8.3.2`. Print both sides and require them to agree:

```bash
grep -E '"(typer|click)==' pyproject.toml
.venv/bin/python -c "import importlib.metadata as m; print(m.version('typer'), m.version('click'))"
```

Repair a drift by re-installing this tree with `uv pip install --python .venv/bin/python -e .`, never by
editing the pin. Never install into a checkout you are not changing, and never borrow another
checkout's `.venv` to judge a worktree: a `.venv` may legitimately hold runtime deps only, with no
`pytest` in it - that is a missing tool, not a licence to test the wrong tree.

---

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
| `test_curator.py` | Curator provisioning, schedule, gate, feed, page writes, and the watermark: an advance is compare-and-set on the pair the run read, so a moved-under run fails loud instead of swallowing an ingest rewind; monotonic within a match on the encoded pair, a refused clamp is logged, `full_history` never clears the stored position, one run per agent at a time; the fence holds the agent row until its commit lands and a failure after the fence publishes neither the marker nor the window; the stored pair is CHECK-constrained — an event half with no instant is rejected at the database; and a requeued beat dispatch that finds the lane's run lock held writes nothing — the run in flight keeps its `started` outcome and its single harness turn through the contention and resolves its own verdict afterwards, so a copy that arrives mid-run can neither report a skip that never happened nor mute the watchdog that pages on an unresolved run |
| `test_curator_watermark_position.py` | The pair itself: the `ahead` truth table in both directions (a whole instant is ahead of any position inside it), and the CAS on the pair — a mid-tie advance is refused under a whole-instant watermark, an advance to the next event at the same instant lands, an advance must read the position it started from, a tie larger than the feed cap drains to zero, and an ingest rewind clears the event half |
| `test_curator_feed_scoping.py` | Which events each wiki may read — the internal wiki everything, the external wiki only sessions of end users who share — and the gate agreeing with that feed |
| `test_curator_event_identity.py` | One event, however often its session was re-pushed: the identity the feed, the gate, the watermark boundary, and the backlog share — with a discriminating test per identity part, so removing any one reddens a named test (`session_id`: two sessions saying the same words at one instant; `event_type`: a `tool_use` and its `tool_result` sharing one instant and payload; `created_at`: a re-asked turn; `md5(content)`: three contents under one batch timestamp), and `agent_name` pinned OUT of it so a re-import relabelled by another client stays one event; distinct-vs-raw backlog; the honest zero (a leftover of only the curator's own transcripts is not work); drain equality; an instant holding more events than the feed cap draining over consecutive runs instead of plateauing; and both endpoints publishing the honest number, `since`+`since_event` honoured by feed and backlog alike (a malformed or orphan event half is a 400), plus the render pins for the feed command the prompt hands the curator — it carries `--since-event` exactly when the lane stands inside an instant and is byte-identical otherwise |
| `test_session_list_filters.py` | The Sessions list's server-side prefilters (`folder_id`, `agent`, `q`, paging `has_more` with an offset that partitions the (last_event_at DESC) order), the unconditional exclusion of the curator's own `agent-curate-` run transcripts — no parameter asks for them back, they survive paging, and they stay resolvable through the detail route (hidden, not deleted) — and the byte-identity guard that pins the shared clause builder's rendering and `_CURATOR_FEED_ELIGIBILITY` so the list can adopt the feed's classification without moving the feed's SQL by one byte |
| `test_session_close_ingestion.py` | That a session closes only on its own close signal: turns alone, a transcript-shaped batch, and a real transcript upload all leave `finished_at` empty; `session_end` on the single-event and the batch endpoints each stamp the close at the instant their event row was stored; a close found anywhere in a batch closes it — including the shape the shipped plugin actually sends (no per-event time) and a close that arrives before any turn; a replayed older close cannot rewind one that landed, a newer close moves it forward, and activity after a close moves recency and nothing else. Two guards pin the shape of the fix rather than its behavior: the close event's name is one shared constant (`CLOSE_EVENT_TYPE`), and `sessions.finished_at` has exactly one SQL write site in `backend/` |
| `test_plugin_close_path.py` | The deploy measurement this card could not run from a worktree, automated: the emitter every shipped `on_session_end` hook calls, the product's own `StashClient`, and the real ingestion app, with only the socket replaced — so the close a real client pushes is what lands in `sessions.finished_at`, and a session the hook never ended stays open |
| `test_curator_session_close.py` | The server closes the sessions its own scheduled runs create. `run_scheduled` is where the server observes its own run die — no plugin, no sprite-side lifecycle hook, and the dispatcher itself writes no history events — so it is the only place a run's session can be closed, and it closes them through ordinary ingestion (`memory_service.push_event` with the shared `CLOSE_EVENT_TYPE`), never by writing `sessions.finished_at`. Pinned per run shape (plain sprite, the two-phase digest+writer pair with both ids named by `scheduled_session_ids`, the scoped developer-platform lane minting the identical id under the workspace owner) and per outcome (returned, raised after its final message, and a turn refused before it wrote anything — which mints no session row just to close it). One close per session, stamped at the instant its own close event was stored, agreeing with what the run list displays as the run's end; a close that fails is logged and never replaces the run's own result |
| `test_fusion_session_close.py` | The `fusion-task-*` family, whose transcript arrives from a pusher the server cannot see: ordinary turns and a replayed transcript-import batch leave it open (the import batch is not close evidence, per `_close_instant`), the pusher's named `session_end` closes it at its stored instant, and a replayed older close cannot rewind it. This is the whole server-side obligation for the family — closing the rows the pusher never named would be guessing at another system's task lifecycle, so the test pins that the server does exactly nothing else |
| `test_scoped_curation.py` | The shared-workspace (developer-platform) curator lane: which material each scope may read, that a scoped run never reaches a credential-bearing sprite, that its watermark commit obeys the same fence as every other forward writer (an overlapping advance or a concurrent reset to never is refused loud; a backfill advances the position it read from), and that it walks its backlog in slices under a budget of its own, each slice committing the pair the next one reads from |
| `test_folder_curators.py` | One curator lane per project folder — its watermark seeded at the folder's first event, its model/endpoint pin digest-round-tripped, an inherited pin resolving byte-equal to the default curator's — and the lane that decides which curation it gets, asked once in `scoped_curation_service.workspace_for_agent`: a folder-bound curator reads one project's feed and writes one wiki folder, so it keeps the credential its own row names even under an owner with an activated developer workspace (gate passes with no backend `ANTHROPIC_API_KEY`, the resolved endpoint is the box it pins, the prompt is the folder prompt), while the External-wiki curator and that workspace's own Memory curator stay on the scoped dispatch, whose provider is now answered by the backend's own configuration rather than by one key (`test_llm_provider.py`). The ingest rewind's twin discrimination lives beside it: `test_repushed_history_does_not_reopen_the_workspace_watermark` parks the position, imports genuinely old history to prove a real import still reopens it, then re-pushes the same event to prove the twin does not; `test_mixed_batch_rewinds_only_to_the_genuinely_new_event` runs the shape a real engine capture arrives in - yesterday's transcript lines again AND the task's new terminal line in ONE batch - and pins that the position lands on the fresh line alone (`fresh - 1 us`, event half cleared), so the twins' older instants cannot drag it back. That second test is the only one of the two that can see a misfire at all: the first asserts the very instant a rewind of its own re-push would land on, so it passes whether or not the guard fires |
| `test_curator_session_id_width.py` | The digest phase of a two-phase run must carry a session id that fits the column it is written into: the production `build_digest_turn` builder under both stamp shapes (the minute beat, and the manual dispatch's second precision, which exists so a manual run never shares a session with the minute's scheduled run) is pinned by exact shape and length against the real `history_events.session_id` limit read from `information_schema`; the real writer `memory_service.push_event` stores the 71-character manual-shape id byte-for-byte (the pre-`0267` founder-stack crash was asyncpg's `StringDataRightTruncationError`), and the ingest model's `max_length` must equal the column width, so `POST /events` can never accept what the column would reject |
| `test_first_day_curator.py` | First-day curator tick, and the ingest rewind re-opening the cursor that imported history predates |
| `test_agent_schedule_alerts.py` | Beat dispatch, designed skips, and the stale-watermark / failing-curator alerts — each of them in the mode it claims: an unset `ALERT_SLACK_*` pair is a mode, so the run-failure handler and the daily curator watchdog record what they decided (the agent row, the `logger.exception`/`logger.error` line) and never ask the Slack transport for a delivery, while the configured twin of each path keeps the delivery and stays loud when it genuinely fails — a Slack transport that is down raises `ConnectionError` out of the handler after the run is already recorded as failed and its lane lock released, because a log is not delivery. The disabled run test is also the proof that a failure inside the reporting path cannot replace the run's own cause: it asserts the agent's error on the row and the lane lock released, having first shown the lock was held when the run died. The third case runs the real `send_alert` on the halfway configuration an operator reaches by setting the two variables and skipping the Slack install — no `slack_bot_installs` row — and pins what that log then says: the run's cause is on the row and logged with its traceback, the `ALERT:` line names what the alarm meant to report, and the install error that escapes is the task's own loud failure while the run's cause stays on the row — the alarm's problem never becomes the record of why the run died. It is also where the automatic drain cadence is pinned, the row for a dedicated backlog-drain test having been removed once that beat stopped existing: a lane that finished a batch is re-dispatched only by the success-driven catch-up chain, so that chain's ceiling - not a beat entry - is what decides how fast a backlog drains. It also pins the catch-up ceiling as a function of the curator allowance rather than a constant, in both directions — the derived ceiling equals the floor at default settings, so a default install cannot drift by one run, and at a raised `FREE_CURATOR_RUNS_PER_MONTH` the bound a pass actually claims is the widened one, since a chain that planned 10 but claimed 7 would still have stopped at 7 — and it pins that an allowance-frozen lane is reported: runs spent against the allowance and the pending event count reach the alert, and an unrelated older `last_run_error` seeded on that row is asserted absent, because `mark_run_skipped` writes the outcome alone and a line built from the error column would name a cause that has nothing to do with the stop |
| `test_curator_no_credential_signal.py` | The self-host profile with no model at all — no `ANTHROPIC_API_KEY`, no connected endpoint, and the real resolver unpatched because a patched one proves only that the branch exists: every tick records `skipped_no_credential` with `last_run_error` still NULL and records it again the next tick, the beat says the block out loud at WARNING naming both remedies, `/me/curators` already hands the outcome to the client (the premise the UI stands on), the stale-curator watchdog still refuses to page that skip specifically (the one skip it does page, `skipped_credits`, is pinned in `test_agent_schedule_alerts.py` — see the row above), and a resolved run overwrites the column the badge self-clears from |
| `test_agent_run_resolution.py` | That a curator lane is never left `started` and never resolved wrongly. The in-process guard: a soft-time-limit escape, a `BaseException` that `except Exception` cannot catch, and an escape out of a body that resolved nothing each resolve the lane with a named cause, while an escape after a recorded success leaves that verdict alone — proven through the real `_TurnLock.release` seam for both task bodies, because the release runs after `mark_run_succeeded` and a Redis failure there must neither stamp `failed` over `ran` nor fire `mark_run_failed`'s refund on a credit the run legitimately spent (the nightly body is pinned separately, since it never refunds on the happy path). A guard whose own repair fails still propagates the original escape, an escape before the run was metered invents no failure, and an unmetered escape refunds nothing where a metered one refunds its credit exactly once. The beat reaper for deaths no process survives: a lane aged past the run-lock TTL is resolved, its run lock released, and reported in exactly one operator alert that names the lane and says the allowance was untouched; a younger `started` lane is left for the run still executing, which is why the sweep's age is the very TTL it replaces. A lane its owner paused between the dispatch and the run's own gate is swept too — selection never conditions on the lane looking runnable — and the sweep reaches its repair in either alert mode: where this installation sends no operational alerts nothing is delivered and the sweep's own summary line is the record, while where they are configured a delivery that genuinely fails (`ConnectionError`) still propagates, because the compare-and-set and the lock drop are one committed write that precedes any alert. A lane that resolved itself between the sweep's SELECT and its write is not touched by the compare-and-set, a second pass resolves nothing, and a `skipped_*` lane is none of its business. The resolver's own contract: it flips `started` rows once, names its cause, and refuses lanes that never ran (NULL) or already carry a verdict. And the harness records `exit=<code>` plus a bounded redacted stderr tail for a turn that ends badly — including a turn that also reported an in-stream error, which used to discard both — while a turn that ends well records nothing, because there is no failure to describe |
| `test_celery_visibility_timeout.py` | The broker's re-delivery window outliving the longest legal agent run: with late acks the queue hands a still-running task's message to another worker once `visibility_timeout` lapses, so the pinned Redis option must be strictly greater than the harness tasks' `HARNESS_TIME_LIMIT` — read as a direct key, so the option's disappearance fails the build rather than defaulting past it |
| `test_source_sync_claims.py` | Sync-claim protocol and the stall watchdog's decision rule: `sync_alerted_at` is written only once the watchdog resolved a source — alert delivered, or delivery deliberately suppressed because this installation sends no operational alerts (a mode announced once at worker startup — at WARNING, so it survives a log level that hides INFO — not per tick); a failed delivery is not a resolution — it propagates and leaves the throttle untouched so the next tick retries. The ordering-guard test goes red if the throttle write is moved above the delivery. Its `_configure_alerts`/`_disable_alerts` helpers are how the whole suite declares an alert mode instead of reading a developer's `.env`: the schedule-alert, run-resolution, and curator suites import them, because patching `send_alert` is a claim about configured mode and a test that asserts silence has to say which mode it means |
| `test_developer_platform.py` | Developer console curator run and backfill dispatch, and the ingest rewind staying inside the wiki whose feed can read the events |
| `test_llm_provider.py` | Which provider a backend completion dials, answered once by configuration and never by reachability: a backend `ANTHROPIC_API_KEY` answers for the hosted product and a connected box sees neither its probe nor a request, with no key the endpoint the console stored answers and the Anthropic client is never built, and a stack with neither is a refusal naming both. The local route is tested against an HTTP server on a real port, because what it promises is bytes — the `/chat/completions` suffix joined once to a base URL that already ends in `/v1`, the bearer header, tools and tool results in chat form, the reply mapped back into Anthropic blocks — and an answer that cannot be executed fails loud naming the endpoint (no choice, no finish reason, arguments that are not JSON, a proxy's 502 HTML). Resolution is tested against stored credentials, not mocks of them: the row's `credential_id` picks which box while the credential decides how to reach it, `model_id` may override only the model id, a pin to a cloud key is refused instead of drifting to some other box, and a box that is off is refused with the transport's own reason before the run spends its one metered attempt. The acceptance proof runs `scoped_curation_service.run()` on the External wiki over the curator suite's dataset: the scoped tool loop closes on the documents the backend selected, on one model id for the whole run |
| `test_agent_endpoints.py` | Multiple local model endpoints per user: connects APPEND named rows, the default is the oldest box, `agents.credential_id` pins one box for every turn of a run (writer and digest dial the same `base_url`), the endpoint API probes before storing and refuses to delete a box a curator still points at. Its last section holds the per-box reasoning level over HTTP, where the two ways that feature can silently lie are pinned: a save answers with the level the server stored and leaves a second box alone (the level is a property of one box, not a global switch), and the write body is ignored apart from the level — an attempt to smuggle a different `base_url` and key alongside it leaves the stored address and key byte-identical, because the control that decides how much a box thinks must not be able to move where the user's key goes. A level the harness does not know, an empty one, a capitalised one, and one with trailing space are all refused with nothing written, so a typo cannot wait until the next turn to fail; a level above `off` is refused while a verbatim `models.json` override is stored (that file, not the level, is what the turn writes, so accepting the pair would show a level the request never carries), while `off` itself stays writable; and a foreign user's id, a random uuid, and a key provider's name all answer 404 rather than leaking that some row exists. `GET` also reports the levels this deployment accepts, which is the only reason the browser can offer a list it cannot get wrong |
| `test_agent_auth.py` | Which harness and credential a turn runs on, and what a local turn actually hands `pi`. The reasoning-level half asserts the wire consequence in both directions rather than that a dict grew: a stored level appears on argv as `--thinking <level>` *and* as the two provider-doc keys that let `pi` compute and name a budget (the argv flag alone sends no number, the doc keys alone reserve no thinking phase), while `off` is asserted equal to the inherited shapes — no flag, no level hidden in the model shorthand (`pi` splits the last colon, and a vLLM model id may contain one), no `reasoning` key written as `false`, and `compat` exactly the two flags it always carried. The negative halves are pinned because `off` is not "the model does not think": a stored doc missing its `thinking` key refuses the turn naming the migration rather than defaulting, since a box that lost its level would reason while the screen reported a bound, and a level stored beside a verbatim override refuses the turn where the two are read together, not only at the write that could have prevented it |
| `test_migrations.py` | Alembic upgrade/history smoke tests |
| `test_migration_chain.py` | The migration graph as a property of the directory, not of one file: one head, no revision id claimed twice, every `down_revision` resolvable, and no file booking an id a deployed build already stamped (`shipped_revisions.json`, keyed by `(id, slug)`, because a stamped database cannot say which of two same-numbered revisions it ran). Two further manifest rules guard the record itself, asserted through the same functions `check_heads.py` exports so guard and test cannot diverge: `stamp_only_node_errors` reddens any id recorded as stamped that the tree gives no node for — a file or an `absorbed` registration — because a stamped database dies at boot with `Can't locate revision identified by …`; `captured_at_errors` reddens a `captured_at.head` that is not the graph's head (or is missing/empty) and stays silent when the graph names no single head. Every id the manifest says a database can hold — booked and stamped alike, read from the manifest rather than a hardcoded list — must also resolve as an ancestor of head. Every manifest rule is shown red on the mistake it exists for (the rule-fires test synthesizes its own `stamp_only` entry, so an empty real one cannot turn it into an accidental pass), and a graph alembic cannot resolve comes back as an error naming the fix rather than a traceback |
| `test_migration_data_convergence.py` | The founder-lineage convergence node runs through its own `converge()`: a database that never converged gets trunk `0203`'s curator-log archive and `0204`'s shared-wiki revocation, a second run changes nothing, and a database that ran trunk `0203`–`0207` the normal way does no data work at all |
| `test_migration_close_backfill.py` | Migration `0268` is evidence only: a session whose close events are already stored gets the latest of their instants, one with no close event stays open, a close ingestion stamped first is never moved backwards, and the second run changes nothing |
| `test_migration_close_curator_runs.py` | Migration `0269_close_finished_curator_run_sessions` (trunk's `0226`, re-issued above the absorbed ceiling) decides from stored rows alone which server-created run sessions ended, and both failure directions are asserted: a run that recorded its terminal `assistant_message`, and a run a later session of the same lane (its `-digest` phase and run stamp stripped) succeeded after, close at the instant of their own last event — never the migration's clock — while a lane's last run that died without final text, a session already closed, and any non-run family (`fusion-task-*`, interactive) keep `finished_at IS NULL`. Run-session ids are built with `scheduled_session_prefix`, so the prefixes the migration freezes cannot drift from the service's live spelling |
| `test_migration_local_endpoint_thinking.py` | Migration `0272_local_endpoint_thinking` carries every existing local endpoint forward with the level that describes what it does today (`off`), and the assertion that matters is about data, not bytes: the encrypted doc of a box connected before the level existed still decrypts to the address, model and key its owner typed, with one key added, while a key provider's row is untouched ciphertext-for-ciphertext (Fernet's random IV means the local row's bytes cannot survive a rewrite, so byte-identity is claimed only where nothing rewrote anything). A row already carrying a level keeps it, the graph stays one head, and the downgrade is a deliberate no-op: old code ignores the extra key, so rolling back must not destroy the level a user chose — the record says which direction is one-way and why |
| `test_startup_logging.py` | App startup owns root logging: one INFO handler on stderr, and the migration runner must not disable app loggers |
| `test_repo_hygiene.py` | The repo root's *index* stays free of run artifacts: `git ls-files` proves `--full-page` and the three undotted celery beat names are untracked, and `git check-ignore` proves all four beat spellings stay ignored — the dotted one `start.sh` points `--schedule` at and the undotted ones beat writes itself. The gap it closes is the one the upstream cleanup left open: `.gitignore` carried only the dotted rule, so the cwd-relative beat files (plus a stray screenshot) were committable. Both assertions ask git rather than the filesystem, because these names legitimately exist on disk during a real beat run — and that is what makes one force-added `celerybeat-schedule` redden both halves at once (a tracked path is reported as not ignored) |
| `test_dockerfile_pi_free.py` | The shipping backend image stays pi-free: the named pattern matches nothing in `backend/Dockerfile`, and a failure reports the matched Dockerfile line texts; the node+pi bake lives only in the dogfood overlay |
| `test_docker_dogfood_compose.py` | The dogfood stack actually selects the pi-carrying image: `docker-compose.dogfood.yml` builds `backend/Dockerfile.dogfood` through a required `BASE_IMAGE` (no default, so an unbuilt base fails loud), one local tag covers every service the prod file pins to the backend image — derived, so a newly added queue cannot be stranded — the overlay declares no service the base file lacks, and both self-host files stay pull-only with no dev-only service or port |
| `test_founder_deploy_pin.py` | What the founder stack runs is one tracked commit, not folklore in one checkout: `docker-compose.founder.yml` exists (the pin used to be two untracked root files that no other worktree could see and no test could read), it retags exactly the services the prod file pins to the backend image plus `frontend` — derived, so a newly added queue cannot come up on a different build while sharing its code and queues — every tag is a local `main-<sha>` build with no registry pull, no interpolation and no default, all backend lanes share one tag, and the backend hex equals the frontend hex, which is the skew that once put a Test button in front of the founder calling a route the running backend did not have; the published self-host files stay free of the founder file and of any commit-built tag |
| `test_pi_reasoning_only_stream.py` | A pi turn that spends its whole output budget on reasoning answers nothing — and pi exits 0, so the mapped error is the only signal anyone ever gets. The fixture is a real `pi --mode json` transcript (pinned 0.84.2) recorded against a local throwaway OpenAI-compatible endpoint whose reply is only `reasoning_content` deltas up to the limit: a terminal `message_end` holding just a `thinking` block, `stopReason`/`rawStopReason` = `length`. `_map_pi` must name the cause there (`no answer content` plus `finish_reason=length` and the token counts, one ≤200-character sentence) instead of ending the turn green, and name it for the mid-reasoning transport death too — where pi's own word is `terminated` and its `auto_retry_end` summary repeats it once per attempt, so the summary no longer gets to overwrite a cause an earlier event already named. The same file holds the boundary: an endpoint error on an empty turn keeps the endpoint's own words verbatim, a tool-call-only turn and a partial answer cut off at the limit stay green, a user-role `message_end` never reaches assistant logic, and a silent empty success is now a failure that reports observed values without inventing a budget it cannot see. The three stop classes are pinned apart, because the founder's local endpoint yields all three: reasoning tokens equal to the whole output at `length` names reasoning, a `length` stop carrying no reasoning breakdown names only the ceiling it stopped at, and an empty turn at `stop` never reads as a spent budget - it measured 2/10 there with reasoning at zero bytes, so exhaustion would be a misdiagnosis (STAS-409). The production path is proven as well — `_run_harness` over the real exec loop yields no text and the named error on a zero exit, redacted — and the cause never reads as a lost transcript (`RESUME_MISSING_RE`), because that would silently reseed the session and bury the truncation |
| `test_collab.py` | Sharing, copy, and collaboration on user-scoped objects |
| `test_session_folder_share_wiki.py` | Per-project shared-wiki opt-in: starts off, only the switch flips it |
| `test_skill_slug_durability.py` | A published skill's MCP handle belongs to the folder, not to the publish record. After an unpublish the reservation row stands while the publication row is gone (the URL answers nothing, then answers again once the folder republishes); deleting the folder cascades the reservation away, so a folder created later under the same title mints its own handle; publish → unpublish → rename → republish keeps the slug while the published title changes; a second folder published under an identical title gets its own distinct slug and can never take a handle another folder already reserved while unpublished (reissuing one is the leak this closes); and a publish refused for a protected folder reserves nothing at all. The backfill is driven too, through the real `upgrade()` of the migration located by slug as `*_skill_slug_is_durable` inside a transaction that is rolled back: a skill already published before the migration lands gets its exact `(folder_id, slug)` pair — so no URL already handed out rotates at deploy — a second pass changes nothing, and the column is UNIQUE with an FK that cascades only from `folders` |
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
`test_developer_platform.py::test_ingest_rewind_stays_inside_its_wiki`). "New enough to reopen" is
its own question and must not borrow the feed's answer: the feed picks one representative of k
identical rows by smallest id, which is sound there because any copy carries the same words, but ids
come from `gen_random_uuid()` and carry no insertion order, so that tie-break calls a re-pushed twin
fresh about half the time (49.3% over 2,000 measured pairs) and reopens what the curator had already
read. The rewind therefore excludes the whole batch it just inserted from its anti-join, so what
remains is prior presence (both directions in `test_folder_curators.py`), the folder-curator
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

**A scoped run reads one bounded batch and stamps its watermark once, fenced on the pair it read.**
A run that had to carry every event still owed through one window could not finish at all once the
founder backlog outgrew what a single run can read (measured on the founder stack 2026-09-23 —
`curated_through` unmoved since 2026-08-22 across 42 runs, each one dying at the 600 s wall). The
walk is therefore spread across runs rather than inside one: `scoped_curation_inputs.load_batch`
fills a batch to `_MAX_INPUT_CHARS` and records what is still owed as `curation_cursor` — which
documents it finished and how far inside an unfinished one it got — and the run's single commit
writes that cursor beside `batch.through`, in the same UPDATE that fences on the pair this run read.
An ingest rewind or an overlapping run therefore matches no row instead of being stamped over.
Pinned by `test_scoped_curation.py`'s `test_cursor_is_committed_only_after_all_scopes_succeed`,
`test_large_files_and_sources_resume_without_skipping_or_unbounded_input`,
`test_same_timestamp_group_larger_than_event_limit_drains` and
`test_revised_large_document_restarts_its_partial_cursor`.

**The run ceiling is one curator's own number on the lane that resolves one, and it is
the same number on both sides of that wall.** `agent_service.turn_ceiling_seconds` is the only code
in the tree that names a scoped run's ceiling: the curator's `agents.turn_timeout_seconds`, or
`AGENT_TURN_TIMEOUT_SECONDS` while that column is null. `sprite_agent_service` resolves it once per run and hands that one value to
`asyncio.wait_for` and to the TTL of the `scoped-curator-{id}` lock fencing the lane, so the two
cannot drift: a lock keyed shorter than the wall expires mid-run and hands the next dispatch a
half-finished run, which is the single-flight guarantee migration 0258 exists to hold. The write
path closes the other gap — `worker_turn_ceiling_seconds()` reads the celery conf, and a ceiling at
or above it is refused at PATCH, because a run the worker kills never reaches the chore that marks
it failed and refunds its curator credit. Only the scoped lane reads any of that: the lane answer
is `scoped_curation_service.workspace_for_agent`, the same call the dispatcher makes, and it
returns `None` twice — for a folder curator, and for every curator of an account with no External
wiki. Those runs fall through to `run_chat`, where no `wait_for` carries a per-curator number and
the only wall is Celery's own task limit, so a curator payload for such a lane carries
`effective_turn_timeout_seconds: null` and a PATCH naming `turn_timeout_seconds` on it is refused
rather than stored. Pinned by `test_scoped_curation.py`'s
`test_scoped_run_timeout_preserves_progress_and_releases_lock` (the wall, the progress that
outlives it, the lock released), `test_each_curator_ceiling_sets_its_own_wall_and_its_own_fence`
(two curators and two ceilings, the shorter wall giving out first and each lane's fence TTL equal to
its own ceiling), `test_a_run_that_takes_its_own_fence_uses_the_same_ceiling` (the second lock
construction site), `test_an_unset_ceiling_fences_the_run_with_the_setting_it_read` (a null column
is the deployment default read through the resolver, not a second codepath), and
`test_a_scoped_run_never_writes_a_stop_flag_only_a_chat_lane_reads` — the measurement that lets
`request_stop`, `_pump_turn` and `run_chat` keep the global constant, since those fence chat
sessions and a scoped run returns before any of them. The bound, the round-trip and the control that
writes it are pinned by `test_folder_curators.py`'s
`test_a_ceiling_the_worker_would_beat_is_refused_and_changes_nothing` and
`test_run_ceiling_round_trips_through_the_curators_api` — both driven on a scoped-lane curator,
the only lane that reads what they write — and each half of the readout is pinned by that file's
`test_the_ceiling_readout_names_a_number_only_the_scoped_lane_reads` (the workspace rows and the
folder row of ONE account answer in different kinds, and the folder row carries neither the number
the account just stored nor the deployment default),
`test_a_lane_that_ignores_the_ceiling_refuses_the_write` (the ceiling write is refused, the column
stays NULL, and a cadence PATCH on that very same row still succeeds) and
`test_an_account_without_an_external_wiki_resolves_no_ceiling` (the row that would otherwise quote
the deployment default). The run-ceiling cases in `CuratorsSection.test.tsx` hold the browser to the
same rule: a scoped row shows an empty field beside the server's effective number rather than a
number the UI invented, and a row whose lane resolves none shows one sentence naming the worker's
task limit, with no ceiling input, no save button, and no digit rendered at all.

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
- No per-file registry here (60 test files, none indexed); the ones that pin a
  server-recorded state worth not losing are named where they are written: a
  curator whose every tick skipped for want of a model has to read as blocked on
  its Settings row (`src/components/settings/CuratorsSection.test.tsx`) and on
  the console's curator page
  (`src/app/(app)/developer/curator/page.test.tsx`)

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
