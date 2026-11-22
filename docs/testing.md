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
  `-r requirements.txt`, which carries `celery[redis]==5.7.0` against `pyproject.toml`'s exact
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
| `test_curator.py` | Curator provisioning, schedule, gate, feed, page writes, and the watermark: an advance is compare-and-set on the pair the run read, so a moved-under run fails loud instead of swallowing an ingest rewind; monotonic within a match on the encoded pair, a refused clamp is logged, `full_history` never clears the stored position, one run per agent at a time for every run that is not a drain part (a drain part holds a lock of its own and runs beside its siblings, which `test_drain_fan_out.py` pins); the fence holds the agent row until its commit lands and a failure after the fence publishes neither the marker nor the window; the stored pair is CHECK-constrained — an event half with no instant is rejected at the database; and a requeued beat dispatch that finds the lane's run lock held writes nothing — the run in flight keeps its `started` outcome and its single harness turn through the contention and resolves its own verdict afterwards, so a copy that arrives mid-run can neither report a skip that never happened nor mute the watchdog that pages on an unresolved run |
| `test_curator_watermark_position.py` | The pair itself: the `ahead` truth table in both directions (a whole instant is ahead of any position inside it), and the CAS on the pair — a mid-tie advance is refused under a whole-instant watermark, an advance to the next event at the same instant lands, an advance must read the position it started from, a tie larger than the feed cap drains to zero, and an ingest rewind clears the event half |
| `test_curator_readout_position.py` | Which position a *readout* answers from, and that exactly one function gets to answer it: both pairs are seeded on one row months apart, so the drain half and the watermark half each reddens this file on its own — the open drain's instant is what a readout names and what its backlog counts from; with no drain pair the figures equal `curator_event_backlog` taken at the watermark exactly; a drain whose event half stayed NULL reports the whole instant it stands in rather than crediting the rest of that tie as read; the figure strictly falls across two committed passes while `curated_through` is byte-identical and the drain pair is the only thing that moved; a spy on the predicate fails if any readout call path grows its own position choice; and the shape test holds `curator_event_backlog` to its three keys while `curator_readout` adds exactly `read_from` and `draining`, so a key that leaks into the wrong helper reddens a named test instead of a route's whole-dict assertion |
| `test_curator_feed_scoping.py` | Which events each wiki may read — the internal wiki everything, the external wiki only sessions of end users who share — and the gate agreeing with that feed |
| `test_drain_fan_out.py` | One drain, many positions: the per-part slots inside the existing `drain_cursor` jsonb (no column, no migration) and the derived `drain_through` they compute; `advance_drain` fencing per slot and refusing a stale write by part id; a plan cut by a pure function that refuses to open a third window past twice the endpoint bound; adjacent parts reading disjoint row sets with the seam row owned by the earlier part; two part locks of one curator held at once, and a dead part's slot reclaimable only after its claim ages out; a part run spending one digest turn, committing its own slot, writing its own server-named report page, and printing neither `turn started` nor `turn finished`; the endpoint gauge admitting every room it has and refusing the rest with `guard=endpoint_pressure` while spending nothing; and the compose pass folding only the contiguous `done` prefix in ascending part id, pruning those slots and recomputing the derived instant in one transaction, retiring only when nothing is pending at the newest composed edge |
| `test_curator_drain_order.py` | The scoped lane's DESCENDING drain — the backlog is read newest window first so a fresh upload is not queued behind years: the kind word is one legal word and a mixed or kind-stripped cursor raises naming the agent; ten descent cells tile a span with no gap and no overlap and land on the floor by arithmetic rather than by a clamp; part ids sort as instants across a DST boundary and a year boundary; a head cell is cut once per seam with its edge captured once, and `max_open` bounds the descent cells and never the head cell; the derived column is `max(published, the surviving slots' through)` — `test_the_seeded_published_edge_is_the_watermark_and_never_the_seam` pins the seed at the live watermark, `test_publishing_and_pruning_the_head_cell_leaves_the_column_where_it_stood` and `test_an_n_cell_descent_leaves_the_column_non_decreasing_across_every_commit` pin the ten commits and nine prunes that may not go backwards, and the column stays non-NULL across the prune of the last slot; one run reads the head cell first and then tiles down to its floor, never works two head cells, resumes a slot it stopped inside, and a head window wider than its budget defers its remainder to the next dispatch while that run descends; a descent's read set is exactly the eligible rows above its floor with no row read twice, and two dispatches of one descent never re-read a window; retirement refuses while the frontier still names un-read ground, extends the floor when the live watermark fell under it, and otherwise hands the published edge to the watermark so a re-delivered lane reads forward and not down — while a hand-off proposed behind the watermark leaves the stored value kept, which is the assertion that can see `advance_watermark`'s clamp; retirement is judged on the ground no cell covers rather than the ground the plan read — `test_a_descent_that_published_real_rows_across_its_whole_band_still_retires` drives a whole five-cell descent over real rows to a hand-off whose stored `curated_through` equals the published edge while the whole-span probe still answers true behind it, because reading a row never makes it ineligible; `has_pending` asks only what the lane still owes (an open window, an un-cut cell below the frontier, input above the plan's top) so `test_a_fully_published_descending_lane_owes_nothing_and_a_fresh_row_owes_one` refuses a lane charged a metered run per tick to re-read its own published history, and `test_the_stale_curator_watchdog_reports_the_edge_that_selected_it` reports the `GREATEST(curated_through, drain_through)` instant that selected the row rather than formatting a watermark a descent may legitimately never write; and the card's bound is a test (`test_the_first_committed_pass_reads_the_fresh_document_with_a_backlog_behind_it`) whose identical fixture FAILS against the parent commit |
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
| `test_agent_schedule_alerts.py` | Beat dispatch, designed skips, and the stale-watermark / failing-curator alerts — each of them in the mode it claims: an unset `ALERT_SLACK_*` pair is a mode, so the run-failure handler and the daily curator watchdog record what they decided (the agent row, the `logger.exception`/`logger.error` line) and never ask the Slack transport for a delivery, while the configured twin of each path keeps the delivery and stays loud when it genuinely fails — a Slack transport that is down raises `ConnectionError` out of the handler after the run is already recorded as failed and its lane lock released, because a log is not delivery. The disabled run test is also the proof that a failure inside the reporting path cannot replace the run's own cause: it asserts the agent's error on the row and the lane lock released, having first shown the lock was held when the run died. The third case runs the real `send_alert` on the halfway configuration an operator reaches by setting the two variables and skipping the Slack install — no `slack_bot_installs` row — and pins what that log then says: the run's cause is on the row and logged with its traceback, the `ALERT:` line names what the alarm meant to report, and the install error that escapes is the task's own loud failure while the run's cause stays on the row — the alarm's problem never becomes the record of why the run died. It is also where the automatic drain cadence is pinned, the row for a dedicated backlog-drain test having been removed once that beat stopped existing: a lane that still owes input is re-dispatched only by the catch-up chain — by a run that finished a batch, and since STAS-438 also by a run its own ceiling cut off after checkpointing at least one pass, never by one that checkpointed nothing — so that chain's ceiling - not a beat entry - is what decides how fast a backlog drains. It also pins the catch-up ceiling as a function of the curator allowance rather than a constant, in both directions — the derived ceiling equals the floor at default settings, so a default install cannot drift by one run, and at a raised `FREE_CURATOR_RUNS_PER_MONTH` the bound a pass actually claims is the widened one, since a chain that planned 10 but claimed 7 would still have stopped at 7 — and it pins that an allowance-frozen lane is reported: runs spent against the allowance and the pending event count reach the alert, and an unrelated older `last_run_error` seeded on that row is asserted absent — `mark_run_skipped` owns the whole skip resolution now and clears the error column on every designed skip, so a line built from that column would name a cause that has nothing to do with the stop; and the stale-watermark measure is `GREATEST(curated_through, drain_through)` rather than the watermark alone, because a descending lane keeps its watermark behind it by design — `test_a_descending_curator_that_moved_its_drain_forward_stays_quiet` reddens when that clause is reverted, and a lane whose drain edge never moved is still paged |
| `test_curator_no_credential_signal.py` | The self-host profile with no model at all — no `ANTHROPIC_API_KEY`, no connected endpoint, and the real resolver unpatched because a patched one proves only that the branch exists: every tick records `skipped_no_credential` with `last_run_error` still NULL and records it again the next tick, the beat says the block out loud at WARNING naming both remedies, `/me/curators` already hands the outcome to the client (the premise the UI stands on), the stale-curator watchdog still refuses to page that skip specifically (the one skip it does page, `skipped_credits`, is pinned in `test_agent_schedule_alerts.py` — see the row above), and a resolved run overwrites the column the badge self-clears from. Its STAS-412 half pins what the surfaces are given instead of silence: `free_runs_per_month` travels on both curator envelopes as the account's own ceiling or `None` for the accounts that have none (Pro, internal, and enterprise alike, because `is_pro` is what lifts it), each curator row carries its own `month_run_count` rather than an account aggregate, and a count left over from a past month is reported as zero spent while the row keeps what it recorded — the endpoint states the current period, never the stale column a lane that stopped being charged carries forever |
| `test_curator_credit_metering.py` | The credit metering itself, driven through the real beat and the real route with the resolver unpatched: a tick that resolves `skipped_credits` never spends a run (the counter is unmoved across two consecutive due ticks — the defect this card is named for, and red before the fix), the eleventh tick after ten worked runs refuses at 10 rather than 11 while still consuming its clock slot, and every designed skip (`skipped_no_credential`, `skipped_no_changes`) is net zero and still stamps `last_run_at` and clears `last_run_error`. Metering is one predicate over the credential the run dials, so on one beat the customer's-endpoint lane leaves the counter at zero and stamps its tick while a cloud lane moves it by exactly one, and no `is_curator` branch around the counter is allowed for either — the same predicate meters a non-curator scheduled agent. A refund can only return what its lane paid: a failed or escaped box-lane run decrements nothing (pinned through the guard's own threading, not by patching `mark_run`), a cloud lane's refund still comes back exactly once. The ordering this card chose is pinned as a decision: with the allowance spent and no runnable credential at once the beat records `skipped_no_credential` where it used to record `skipped_credits`, and the run-now route answers the connect-a-model 402 detail where it answered the allowance one — same status, more actionable sentence, counter unmoved either way. And a manual run on a switched-off curator on a box lane really runs, unmetered, because the guard reads the explicit platform-trigger keyword rather than inferring it from `metered` |
| `test_agent_run_resolution.py` | That a curator lane is never left `started` and never resolved wrongly. The in-process guard: a soft-time-limit escape, a `BaseException` that `except Exception` cannot catch, and an escape out of a body that resolved nothing each resolve the lane with a named cause, while an escape after a recorded success leaves that verdict alone — proven through the real `_TurnLock.release` seam for both task bodies, because the release runs after `mark_run_succeeded` and a Redis failure there must neither stamp `failed` over `ran` nor fire `mark_run_failed`'s refund on a credit the run legitimately spent (the nightly body is pinned separately, since it never refunds on the happy path). A guard whose own repair fails still propagates the original escape, an escape before the run was metered invents no failure, and an unmetered escape refunds nothing where a metered one refunds its credit exactly once. The beat reaper for deaths no process survives: a lane aged past the run-lock TTL is resolved, its run lock released, and reported in exactly one operator alert that names the lane and says the allowance was untouched; a younger `started` lane is left for the run still executing, which is why the sweep's age is the very TTL it replaces. A lane its owner paused between the dispatch and the run's own gate is swept too — selection never conditions on the lane looking runnable — and the sweep reaches its repair in either alert mode: where this installation sends no operational alerts nothing is delivered and the sweep's own summary line is the record, while where they are configured a delivery that genuinely fails (`ConnectionError`) still propagates, because the compare-and-set and the lock drop are one committed write that precedes any alert. A lane that resolved itself between the sweep's SELECT and its write is not touched by the compare-and-set, a second pass resolves nothing, and a `skipped_*` lane is none of its business. The resolver's own contract: it flips `started` rows once, names its cause, and refuses lanes that never ran (NULL) or already carry a verdict. And the wall's own cut is proven on the lane that produced the founder failure: a cancellation landing mid-`anext` releases the generator's in-flight pull before anything closes, so the harness stream's own `finally` runs, nothing raises `aclose(): asynchronous generator is already running`, and the personal-lane wall ends as `RunCeilingExceeded` naming its seconds and the unread position it stopped at rather than as a laundered `RuntimeError`. A beat tick dispatched for an agent whose `run_mode` is no longer `scheduled` resolves its own charged row at that exit — `failed` with a cause naming the mode it found, and its credit moved once by the same compare-and-set — while the arms that defer to a run in flight still write nothing and keep their single refund untouched. And the harness records `exit=<code>` plus a bounded redacted stderr tail for a turn that ends badly — including a turn that also reported an in-stream error, which used to discard both — while a turn that ends well records nothing, because there is no failure to describe |
| `test_celery_visibility_timeout.py` | The broker's re-delivery window outliving the longest legal agent run: with late acks the queue hands a still-running task's message to another worker once `visibility_timeout` lapses, so the pinned Redis option must be strictly greater than the largest hard ceiling in the task registry — each decorator's own `time_limit`, plus the global `task_time_limit` for the tasks whose decorator declares none — and the test reads that maximum from the registry itself (importing the `include` list and refusing a partial one), so a decorator that outlives the window reddens the build instead of quietly reproducing the founder's 2026-10-01 double dispatch; read as a direct key, so the option's disappearance fails the build rather than defaulting past it |
| `test_source_sync_claims.py` | Sync-claim protocol and the stall watchdog's decision rule: `sync_alerted_at` is written only once the watchdog resolved a source — alert delivered, or delivery deliberately suppressed because this installation sends no operational alerts (a mode announced once at worker startup — at WARNING, so it survives a log level that hides INFO — not per tick); a failed delivery is not a resolution — it propagates and leaves the throttle untouched so the next tick retries. The ordering-guard test goes red if the throttle write is moved above the delivery. Its `_configure_alerts`/`_disable_alerts` helpers are how the whole suite declares an alert mode instead of reading a developer's `.env`: the schedule-alert, run-resolution, and curator suites import them, because patching `send_alert` is a claim about configured mode and a test that asserts silence has to say which mode it means |
| `test_developer_platform.py` | Developer console curator run and backfill dispatch, and the ingest rewind staying inside the wiki whose feed can read the events |
| `test_llm_provider.py` | Which provider a backend completion dials, answered once per run by configuration and never by reachability — a box that is off is refused, never stepped around: a backend `ANTHROPIC_API_KEY` answers for the hosted product and a connected box sees neither its probe nor a request, with no key the endpoint the console stored answers and the Anthropic client is never built, and a stack with neither is a refusal naming both remedies. The local route is tested against an HTTP server on a real port, because what it promises is bytes — the `/chat/completions` suffix joined once to a base URL that already ends in `/v1`, the bearer header, tools and tool results in chat form, the reply mapped back into Anthropic blocks — and an answer that cannot be executed fails loud naming the endpoint (no choice, no finish reason, arguments that are not JSON, a proxy's 502 HTML). Resolution is tested against stored credentials, not mocks of them: the row's `credential_id` picks which box while the credential decides how to reach it, `model_id` may override only the model id, a pin to a cloud key is refused instead of drifting to some other box, and a box that is off is refused with the transport's own reason before the run spends its one attempt. Who pays follows the same single answer: a scoped run on the endpoint the customer connected reports `metered=False` — and a free account's catch-up chain stays open on it, because there is no allowance for it to spend — while a keyed run reports `metered=True` and leaves the box unprobed; the manual "Run now" gate reads that same resolver, so a keyless stack with a live box dispatches instead of answering 503. One spelling difference between the providers is pinned where it bites: a box that cuts an answer short says `length` where the key says `max_tokens`, and either word has to reach the retry that refuses to execute the partial write. The acceptance proof runs `scoped_curation_service.run()` on the External wiki over the curator suite's dataset against that real server: the scoped tool loop closes on the documents the backend selected, on one model id for the whole run, on one probe for the whole run rather than one per scope, and it advances the watermark the lane was stuck on |
| `test_agent_endpoints.py` | Multiple local model endpoints per user: connects APPEND named rows, the default is the oldest box, `agents.credential_id` pins one box for every turn of a run (writer and digest dial the same `base_url`), the endpoint API probes before storing and refuses to delete a box a curator still points at. Its last section holds the per-box reasoning level over HTTP, where the two ways that feature can silently lie are pinned: a save answers with the level the server stored and leaves a second box alone (the level is a property of one box, not a global switch), and the write body is ignored apart from the level — an attempt to smuggle a different `base_url` and key alongside it leaves the stored address and key byte-identical, because the control that decides how much a box thinks must not be able to move where the user's key goes. A level the harness does not know, an empty one, a capitalised one, and one with trailing space are all refused with nothing written, so a typo cannot wait until the next turn to fail; a level above `off` is refused while a verbatim `models.json` override is stored (that file, not the level, is what the turn writes, so accepting the pair would show a level the request never carries), while `off` itself stays writable; and a foreign user's id, a random uuid, and a key provider's name all answer 404 rather than leaking that some row exists. `GET` also reports the levels this deployment accepts, which is the only reason the browser can offer a list it cannot get wrong. Its next section holds the per-box context window over HTTP, the field STAS-465 replaced a shipped constant with: one code path connecting two boxes answers two different `contextWindow`/`maxTokens` pairs (a rack box keeps the shipped output ceiling, an edge box gets a window-sized one, so the pair is shown to be derived per endpoint rather than stamped once), a `PUT .../context-window` resizes one box and resizes nothing else @ even a body carrying `base_url`, `model`, `api_key` and `thinking` alongside the number leaves every other key byte-identical @ the same write refuses while a verbatim `models.json` override is stored and succeeds once the override is reset, `1024`, `0`, `32768.5` and `"32k"` are all refused with the stored window unmoved, and a foreign id, a random uuid and a key provider's name answer 404 |
| `test_agent_auth.py` | Which harness and credential a turn runs on, and what a local turn actually hands `pi`. The reasoning-level half asserts the wire consequence in both directions rather than that a dict grew: a stored level appears on argv as `--thinking <level>` *and* as the two provider-doc keys that let `pi` compute and name a budget (the argv flag alone sends no number, the doc keys alone reserve no thinking phase), while `off` is asserted equal to the inherited shapes — no flag, no level hidden in the model shorthand (`pi` splits the last colon, and a vLLM model id may contain one), no `reasoning` key written as `false`, and `compat` exactly the two flags it always carried. The negative halves are pinned because `off` is not "the model does not think": a stored doc missing its `thinking` key refuses the turn naming the migration rather than defaulting, since a box that lost its level would reason while the screen reported a bound, and a level stored beside a verbatim override refuses the turn where the two are read together, not only at the write that could have prevented it. The window half pins the same shape: a stored doc missing `context_window` refuses the turn naming migration 0275 and the hint the connect form uses (a box that lost its size would be asked for output it cannot serve), the synthesized entry's `contextWindow` is the stated number with `maxTokens` derived as the smaller of the shipped ceiling and the window minus the answer room @ asserted for three windows, one of them so small the ceiling loses @ and the entry keeps the two provider-doc keys the level needs, since a model entry that lost them would reason without a budget |
| `test_migrations.py` | Alembic upgrade/history smoke tests |
| `test_migration_chain.py` | The migration graph as a property of the directory, not of one file: one head, no revision id claimed twice, every `down_revision` resolvable, and no file booking an id a deployed build already stamped (`shipped_revisions.json`, keyed by `(id, slug)`, because a stamped database cannot say which of two same-numbered revisions it ran). Two further manifest rules guard the record itself, asserted through the same functions `check_heads.py` exports so guard and test cannot diverge: `stamp_only_node_errors` reddens any id recorded as stamped that the tree gives no node for — a file or an `absorbed` registration — because a stamped database dies at boot with `Can't locate revision identified by …`; `captured_at_errors` reddens a `captured_at.head` that is not the graph's head (or is missing/empty) and stays silent when the graph names no single head. Every id the manifest says a database can hold — booked and stamped alike, read from the manifest rather than a hardcoded list — must also resolve as an ancestor of head. Every manifest rule is shown red on the mistake it exists for (the rule-fires test synthesizes its own `stamp_only` entry, so an empty real one cannot turn it into an accidental pass), and a graph alembic cannot resolve comes back as an error naming the fix rather than a traceback |
| `test_migration_data_convergence.py` | The founder-lineage convergence node runs through its own `converge()`: a database that never converged gets trunk `0203`'s curator-log archive and `0204`'s shared-wiki revocation, a second run changes nothing, and a database that ran trunk `0203`–`0207` the normal way does no data work at all |
| `test_migration_founder_lineage_reapply.py` | The founder-lineage databases hold the curator/credential segment (`0257`–`0266`) while their stamp sits below it, so that segment has to run twice over — which is exactly what five of its nodes could not do until STAS-440. Lineage 1 builds the chain to the end of the range, seeds the fork's world on top of it (a curator agent with a folder scope, model pick, digest pair and populated watermark; a project cleared for the shared wiki; two credential rows already named under an `id` primary key), then moves the version row alone back to the state before the range and re-runs it three times over: every seeded value must come back byte-identical and the catalog must name exactly the segment's objects, with `one_curator_per_user_per_wiki` gone and `one_curator_per_scope` present. The whole re-apply runs under a Fernet key that is valid but is NOT the one the seeded rows are encrypted under, so a node that still decrypted a named row would die with `InvalidToken` — exit 0 is therefore the proof the guards skipped the work, not that nothing read it. Lineage 2 is the database that lacks those objects: seeded in the pre-segment shape, it must receive the reshape (primary key becomes `id`, the endpoint row named from the host inside its own encrypted document, ciphertext bytes unmoved because the range stops before `local_endpoint_thinking`), a second box storable while a second key per provider is refused, and the watermark pair CHECK rejecting an event with no instant. Both lineages resolved by slug through `migration_ids.py` on their own scratch databases, because a literal revision id is how a renumber makes a migration test pass for the wrong reason |
| `test_migration_fork_object_convergence.py` | What STAS-443's census made measurable: a founder-lineage database arrives at trunk's DEFINITIONS, not merely at head. Seeded in the measured island shape at 0258's parent — colliding columns carrying their inline FKs, the retired `one_curator_per_user_per_wiki` dropped as the founder chain retired it, the widened run-outcome check absent (the inverse class), the owner's own names on encrypted credential rows — and, stronger than measured so a guard cannot pass by inheriting its target, `one_curator_per_scope` seeded with the pre-folder rule and the watermark pair CHECK seeded weaker. After `upgrade head`: the scope index and both CHECKs equal trunk's definitions (drop-then-rebuild converges where `IF NOT EXISTS` would keep the wrong rule), the absent retired index did not stop the run, a name the owner chose survives `_backfill_names()` while an unnamed row is still named from its host, and the three FKs riding on colliding columns equal trunk's — founder configuration must never ship FK-less. Each assertion was reddened by a named perturbation (STAS-443 runbook): 0261's rebuild regressed to a skip, the ADD halves of 0258/0260/0266 suppressed, 0261's index drop unguarded, the backfill's `name IS NULL` restriction removed, and the seed's inline FKs withheld |
| `test_migration_close_backfill.py` | Migration `0268` is evidence only: a session whose close events are already stored gets the latest of their instants, one with no close event stays open, a close ingestion stamped first is never moved backwards, and the second run changes nothing |
| `test_migration_close_curator_runs.py` | Migration `0269_close_finished_curator_run_sessions` (trunk's `0226`, re-issued above the absorbed ceiling) decides from stored rows alone which server-created run sessions ended, and both failure directions are asserted: a run that recorded its terminal `assistant_message`, and a run a later session of the same lane (its `-digest` phase and run stamp stripped) succeeded after, close at the instant of their own last event — never the migration's clock — while a lane's last run that died without final text, a session already closed, and any non-run family (`fusion-task-*`, interactive) keep `finished_at IS NULL`. Run-session ids are built with `scheduled_session_prefix`, so the prefixes the migration freezes cannot drift from the service's live spelling |
| `test_migration_local_endpoint_thinking.py` | Migration `0272_local_endpoint_thinking` carries every existing local endpoint forward with the level that describes what it does today (`off`), and the assertion that matters is about data, not bytes: the encrypted doc of a box connected before the level existed still decrypts to the address, model and key its owner typed, with one key added, while a key provider's row is untouched ciphertext-for-ciphertext (Fernet's random IV means the local row's bytes cannot survive a rewrite, so byte-identity is claimed only where nothing rewrote anything). A row already carrying a level keeps it, the graph stays one head, and the downgrade is a deliberate no-op: old code ignores the extra key, so rolling back must not destroy the level a user chose — the record says which direction is one-way and why |
| `test_migration_local_endpoint_context_window.py` | Migration `0275_local_endpoint_context_window` backfills the size of every existing local box out of what that box already declared, never one global number: a row that never saved a `models.json` override is stamped with the window Stash itself used to claim about every box, an override row is read out of its own encrypted file and stamped with its own `contextWindow` @ two override rows stamped with two different numbers is the assertion that makes a single constant impossible @ and a row whose override cannot be read, or whose stamped window sits below the service's floor, refuses the upgrade naming its own id rather than clamping or defaulting. A row that already carries a window is left as written, a key provider's row is untouched ciphertext-for-ciphertext, a database with no local endpoint never opens the keyring (a stranger key still reads back byte-identical), the graph stays one head, the floor is pinned equal to the constant the service enforces, and the downgrade is a deliberate no-op for the same reason `0272`'s is |
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
before it, and the contract is **one fence for every forward writer**: the Memory and
project-folder completions (`agent_service.mark_curated`) execute
`agent_service.advance_watermark`, and the shared-workspace commit
(`scoped_curation_service._checkpoint_pass`, called at every pass boundary by
`scoped_curation_service.run`) executes its own fenced UPDATE in its own transaction, after
re-checking the scope permission. Both anchor on the position their dispatcher loaded (never a
re-read of the row the run finishes on) and both raise `CuratorWatermarkConflict` when no row
matches, so no forward writer holds a looser rule. A scoped run therefore fails loud on a
mid-run move instead of stamping over it:
`test_scoped_curation.py::test_a_scoped_run_whose_position_moved_underneath_it_refuses_the_pass`
pins the refusal, the rival's position, the absence of a published summary, and the absence of a
per-pass meter line, while
`test_a_full_history_scoped_backfill_reads_from_before_its_watermark` pins the backfill state
that must advance rather than conflict. The window the fence is responsible for is pinned at its two
edges by `test_curator.py`'s `test_the_fence_holds_the_agent_row_until_its_commit_lands` (a rival
that starts its fenced commit while the first transaction is open provably blocks on it and is
then refused) and `test_a_failure_after_the_fence_publishes_neither_the_watermark_nor_the_window`
(the crash boundary: a raise after the matched fence rolls the stamp back and leaves the window
still offered).

**A scoped run is a loop of bounded passes and stamps its watermark once per completed pass, fenced
on the pair that pass started from.** A run that had to carry every event still owed through one
window could not finish at all once the founder backlog outgrew what a single run can read (measured
on the founder stack 2026-09-23 — `curated_through` unmoved since 2026-08-22 across 42 runs, each one
dying at the 600 s wall). Spreading the walk across runs did not fix that, because a run is one wall:
the slice a run read was all it could lose, so a slice whose own reading outgrew the ceiling died at
the ceiling and the next run read the same slice again. `scoped_curation_service.run` therefore
loops: each pass asks `scoped_curation_inputs.load_batch` for one budget-sized batch, reads it across
every scope with a claim on it, and moves the durable position — `curation_cursor` beside
`batch.through`, in the UPDATE that fences on the pair that pass started from — before the next batch
is read, so the most a ceiling cut can ever discard is the pass in flight. A pass that advanced only
the cursor half of the pair (the instant stays one microsecond behind the document still being read)
is a checkpoint exactly like one that moved the instant, and a pair that moved nowhere is not written
at all. The loop ends when a batch stops owing anything. The batch budget is a whole-run number
derived from the run's own ceiling, so three scopes on one lane do not spend three budgets —
`load_scope` takes its run's batch as a required argument and cannot load its own. Because the batch
is what a checkpoint covers, a batch that still owes input but cannot fill even one row — a ceiling
short enough that its derived budget falls under the loader's one-row floor — raises before any scope
runs rather than spinning on an empty slice. A pass whose pair moved nowhere while its batch still
owed input is the same failure one pass later and raises too, because the loop's whole termination
argument is that every pass either drains the backlog or moves the pair. That second shape is pinned
by `test_a_pass_that_owed_input_and_moved_nothing_fails_instead_of_re_reading_it`, which forces a
loader that hands back the slice it was given and counts the loads, so a run that went back for the
same characters again fails on the count. A pass's summary is published with its checkpoint, so a
pass that fails later, or a run the wall cuts, leaves no report of unread work behind, and each
committed pass logs one INFO drain-rate line — pass number, agent, committed instant, the batch's
own characters, scope count, that pass's wall, and since STAS-458 the granted `budget=` beside the
calibrated `turn_seconds=` / `calibrated_seconds_per_character=` and the `seconds_per_character=` the
pass's own characters actually cost — which is what makes a cut run readable as the
passes it finished; `test_one_run_drains_a_document_larger_than_its_budget_in_committed_passes`
pins one line per committed pass and no line for one that committed nothing. An ingest rewind or
an overlapping run therefore matches no row instead of being stamped over. Where the read
starts and what the checkpoint fences on are two different facts, and only the backfill pulls them
apart: the read begins at the row's own position while the fence keeps the pair the dispatcher loaded,
so the `full_history` backfill — whose dispatcher zeroes that position inside its own copy of the
agent — still reads with no watermark, which
`test_a_full_history_scoped_backfill_reads_from_before_its_watermark` pins against its own control
run (the ordinary run on that drained row is asserted to hand the model nothing at all). Pinned by
`test_scoped_curation.py`'s `test_cursor_is_committed_only_after_all_scopes_succeed`,
`test_large_files_and_sources_resume_without_skipping_or_unbounded_input`,
`test_same_timestamp_group_larger_than_event_limit_drains` and
`test_revised_large_document_restarts_its_partial_cursor`, and on the loop itself by
`test_a_ceiling_cut_keeps_its_completed_passes_and_the_next_run_resumes_them` (a run its own ceiling
cuts off keeps the pass that finished, publishes exactly one summary, and is picked up by the next run
at the offset the checkpoint names rather than at the start of the document) and
`test_one_run_drains_a_document_larger_than_its_budget_in_committed_passes` (one run walks a document
its budget cannot hold, committing several times at one instant with a different cursor each time).

**A history drain has a position of its own, and that pair is the only position a draining run reads
or writes.** `agents.curated_through` stays the monotone incremental watermark — the clamp is what
makes an ingest rewind un-clobberable — while `agents.drain_through` beside `agents.drain_cursor` is
the resumable read position of a `full_history` backfill, whose backlog sits *behind* that watermark
by design. A fan-out keeps that one store and makes it plural rather than adding a second one: each
part owns a slot inside `drain_cursor.parts` holding its window (`from`, `to`), its own resumable
position (`cursor`, `through`), its `state` (`open` or `done` once its report page is written) and
the `claim_expires_at` that makes a dead worker's slot reclaimable after the deadline and not before
it. `drain_through` is then a DERIVED value — the `from` of the oldest slot whose history the wiki
has not absorbed — recomputed in the same UPDATE that advances or prunes any slot, never
hand-stamped, never NULL while a plan or a slot stands. `curation_service.drain_derived_through` is
the only place that arithmetic lives, and
`test_the_derived_instant_is_the_oldest_uncomposed_slot_and_never_null` beside
`test_a_hand_edited_column_beside_parts_raises_rather_than_being_repaired`
(`backend/tests/test_drain_fan_out.py`) hold both halves. A DESCENT is the second kind of drain and
it derives from the other end: `max(published, the surviving slots' through)`, where `published` is
the newest window edge already committed and is seeded at the live `curated_through` rather than at
the plan's seam — the seam is a guess at the newest end, so a seam-shaped seed would name ground no
committed pass ever read, and the stale-curators alert reads this column directly. Because a descent
publishes by PRUNING (the window that finished leaves the slot set, and it was the one holding the
maximum), the column needs its own fence beside the per-slot one: `advance_drain` refuses a commit
whose aggregate sits below where the column stands, which no slot fence can see. That raise is a
boundary fence and not a reachable state for this lane's two writers — `prune_published_part` raises
`published` to the window edge in the same statement that deletes the slot, and that is what makes
the race safe — so what is pinned is the arithmetic it protects:
`test_the_seeded_published_edge_is_the_watermark_and_never_the_seam`,
`test_publishing_and_pruning_the_head_cell_leaves_the_column_where_it_stood` and
`test_an_n_cell_descent_leaves_the_column_non_decreasing_across_every_commit`
(`backend/tests/test_curator_drain_order.py`) hold ten commits, nine prunes and one direction. A run
that checkpointed a drain into the
watermark was therefore refused by the very clamp
that makes the watermark safe and re-read its first slice on every dispatch until the worker killed
the task (measured on the founder copy 2026-11-14: one dispatch worked 1437.8 s and died at Celery's
`task_soft_time_limit = 1500` with nothing durable, because the lane had no wall of its own and the
only position it could write was the monotone one). Three things keep the two positions honest. The
drain is *seeded by its absence*: `drain_through IS NULL AND drain_cursor = '{}'` is a seed meaning
"read the whole feed", and `curation_service.drain_of` is the single reader of that rule, so a
second dispatch on a lane already draining resumes the pair it finds instead of re-zeroing it —
`test_two_history_drain_dispatches_resume_one_position_rather_than_rereading_it`
(`backend/tests/test_scoped_curation.py`) pins the disjoint read sets, and both dispatches leave
`curated_through` byte-identical. `agent_service.advance_drain`
advances the pair at every committed pass with the same fence as the watermark and deliberately *no*
monotone clamp, because a drain's forward guarantee comes from its own read (`complete_through`
promises a position strictly ahead of what it read) while a drain behind the watermark is exactly its
job — `test_every_committed_pass_of_a_drain_moves_the_drain_position_and_no_more`
(`backend/tests/test_scoped_curation.py`) counts one advance per meter line, and
`test_mark_curated_cannot_walk_the_watermark_back` (`backend/tests/test_curator.py`) holds the other
half: the drain moves, the watermark does not. And when the drain reaches the end of its feed —
for a fan-out, once its compose pass has folded every slot's report and nothing is pending at the
newest composed edge — `finish_drain` retires the pair and hands the covered past to `advance_watermark` fenced on the incremental pair the
dispatcher loaded *before* the drain existed — anchoring that write on the pair that just retired
would fence one position by the value of another, and fencing it on the position the pass read (rather
than where the same transaction had just put the pair) is the one shape that made a personal-lane
drain unable to ever retire: the hand-off refused the run's own commit and raised a conflict blaming
another run, forever at the end of every feed. Both lanes are pinned on that retirement:
`test_a_personal_drain_that_reaches_the_end_retires_its_pair_and_hands_off` asserts the pair is gone
and the watermark holds the ground, because a run that merely "succeeded" would also have been true
with the pair still pending.

**The pair is also the position a readout reports, not only the one a run reads.**
`curation_service.readout_position_of` is the single place that answers "which position is
live on this row, as a `Position`", and four readers ask it: the developer console's curator
readout, the folder curator's readout on `GET /me/curators`, the `POST /me/memory/recompute`
gate, and the credits-spent clause of the stale-curators alert. Before this, all four counted
from `curated_through` while the run path already read from the drain, so a drain that has been
committing for a month showed one frozen "still to read" figure — and the recompute button
answered "nothing new to curate" at the one lane that had work, because its whole unread backlog
sits *behind* the watermark by design. `curation_service.curator_readout` is the one readout
that counts from the answer and publishes `read_from` and `draining` beside the figures, which is
what lets both clients name the instant they measured from instead of choosing between two stored
instants — a second counter, and the reason no arithmetic for it exists in TypeScript. Two bounds
travel with the predicate. A drain pair has no event half (no `drain_through_event_id` column
exists), so during a drain the readout is the whole instant and a lane parked inside a saturated
instant is reported as reading from before that instant: the conservative direction, because the
alternative is a backlog reading zero while a tie's tail is still unread. And a drain is seeded by
its absence, so between a dispatch and its first committed pass there is nothing stored to
distinguish a seeded drain from no drain, and the readout names the watermark for both — a marker
column to tell them apart would be the second position counter. `GET /me/changes` deliberately
stays caller-relative, and that is what keeps the wall paragraph below true: its sentence about
keeping the drain budget off every reader that is not a drain pass survives only while that route
answers from the position its caller named, because `drain_pass_budget` recognises a drain pass by
comparing the request's position to the row's stored pair.

**A run's wall is one number, and it both bounds the work a run starts and decides when the run
stops.** `agent_service.run_wall_seconds` is where a row becomes a wall on *both* lanes: the worker's
own `worker_turn_ceiling_seconds()` caps the curator's ceiling and a commit reserve is subtracted, so
`task_soft_time_limit` is never the first thing that fires (`test_celery_visibility_timeout.py` asserts
that inequality against the live celery config). The scoped loop reads that same number once and
checks it *before* starting a pass, against the slowest pass already run, and returns a summary naming
the undrained feed and the position the drain stands at rather than starting work it cannot finish —
that pre-pass check is the normal exit of a long drain and the `asyncio.wait_for` wall is the rescue
for a scope that hangs, which is why both exist and neither may be deleted
(`test_a_run_stops_before_a_pass_it_cannot_finish_inside_its_wall` asserts the passes read equal the
passes committed, next to `test_a_ceiling_cut_keeps_its_completed_passes_and_the_next_run_resumes_them`
pinning what survives a rescue). A rescue has to arrive cleanly to be one: the cut cancels the turn
while its reader is inside `anext`, so `_stoppable`'s `finally` releases that in-flight pull before it
closes the generator, which is what lets the harness stream's own teardown run and lets the
cancellation reach the wall as the cancellation it was (`aclose(): asynchronous generator is already
running` is what a close over a running frame costs, and it arrived as a plain `RuntimeError`, which
no `except TimeoutError` could see). On the personal lane the same wall sizes the batch: `_budget_kept`
is the one place a character budget is cut, called both by `changes_since` (so the feed stops where
the pass will) and by `complete_through` (so the run cannot commit characters it was never shown) —
`test_the_feed_a_draining_pass_is_handed_ends_where_its_commit_will_stand` is about that boundary, not
about the arithmetic, and `test_only_a_reader_at_the_drains_own_position_is_sized_by_that_drain` keeps
the budget off every reader that is not a drain pass, including the owner browsing their own feed.

**Whether a chain may continue is the cost of the next pass, never the plan of its owner.**
`agent_schedules._chain_permitted` resolves `metered` from `require_run_auth` (the one source of truth
for whether a run spends platform tokens), allows when the pass costs nothing the monthly allowance
rations *or* the owner has an unlimited plan, and writes one INFO line carrying both answers and the
decision, because a reader of the log must be able to tell a declined chain from a stalled one. The
lane tests that used to sit in the catch-up path are gone deliberately: a `plan='free'` account whose
curator dials its own endpoint was denied the only drain its own box could have paid for, and a
metered free lane stays closed because the predicate says so, not because of which lane it is —
`test_queued_catchup_rechecks_execution_gates` therefore varies the *cost* answer (its
`metered_lane` and `local_endpoint` modes are one row on one free account) and
`test_the_chain_permission_line_names_both_answers_and_the_decision` pins the accounting line in both
directions. Honest gap, reported rather than patched: a scoped workspace answers `metered=True` by
construction because the backend's key pays, so an operator running a scoped workspace on their own
hardware cannot demonstrate the free-lane half of this rule from that workspace's row.

**The input slice is sized from the run wall by a price per character that is measured per route
class, not carried over.** `input_budget` takes the wall the loop works to
(`agent_service.run_wall_seconds` — the same number `sprite_agent_service` cuts the run at), returns
`int((wall - R) / rate)` clamped into the loader's `MINIMUM_BATCH_CHARS` floor and the
`MAX_PASS_INPUT_CHARS` ceiling, where `R` is
`RUN_COMMIT_RESERVE_SECONDS` — the checkpoint's own time — and `run` reads the wall once, so
the slice one pass is handed cannot be widened without widening the wall that bounds it. STAS-468 put
that subtraction inside the pricing rather than at a call site: a pass has to leave room to commit
itself, and the two callers must not each hold a copy of that fact. `rate` is not a shipped constant:
STAS-490 moved it into `backend/services/curator_calibration.py`, which prices a route class
(`anthropic`/`local` × `internal`/`external`) from the passes that class actually ran and seeds it
per class — 0.32646 s per character for the hosted route (STAS-458's sample: 95 s of model turn over
291 characters, priced from the arm that walked its checkpoint slowest), 0.12 for a founder box whose
completed passes measured 0.0367–0.12. A price 3–9× too slow is a batch 3–9× too small, which is how
a founder lane sat granted at 1,455 characters per pass behind 4,669,736 unread documents; the
provenance of each seed is written beside the seed, and an unpriced key raises rather than
defaulting. A wall that cannot buy the
cheapest slice `input_budget` would return *and* pay for the commit that slice exists to make —
anything at or below `_minimum_wall_seconds(rate)`, which is `MINIMUM_BATCH_CHARS × rate + R`,
152.9 s of wall at the 0.12 seed and 364.3 s at the hosted one — raises naming the pass price, its units,
the reserve and the wall that buys both. Three numbers are in play and only one of them is a wall:
334.3 s is what the loader's 1,024 characters cost at the hosted seed, 364.3 s is the wall that pays
for them plus the commit, and a row's *ceiling* has to reach 394.3 s to resolve to that wall. The
guard is what a `_smallest_pass_seconds(rate)`-seeded pre-pass check also declines the first pass
against: the guard's refuse-set is a SUPERSET of the loop's decline-set by construction
(`MINIMUM_BATCH_CHARS ≥` the batch one model turn carries), an
inclusion the tests pin because the plan's original "same set" equality was disproved by its own
wall-120 row. The 333-characters-per-ceiling-second era could not express any of this — its rate
priced ~19× more than a wall could carry and no measurement could see it. Three tests keep the price
under observation instead of under assertion:
`test_the_granted_budget_fits_the_wall_measured_in_the_sample_its_own_rate` judges the 570 s grant
against `1277 / 10211` seconds-per-character — the founder lane's own completed pass, copied into the
test from the measurement and derived from nothing it judges — so a real rate moving above what the
constants imply reddens the test while the constants sit unread. That literal is 2.61 times more
generous than the priced measure, so it cannot be the headroom gate and is not posed as one: the same
test carries a second assert on `T / C` for that; and
`test_the_run_hands_the_loader_its_wall_sized_slice_not_its_ceiling` dials a row's ceiling to 10,000 s
against the worker's cap and spies `load_batch`'s `max_chars` — the wall-sized number, not the
ceiling-sized one; while the ladder `test_every_granted_wall_affords_the_slice_it_grants` from half a
turn to 1,200 s reads its threshold from `_minimum_wall_seconds()` and asserts every refused wall sits
below it, every granted slice costs strictly *inside* the wall that granted it, and what is left over
is at least `R` — the headroom law `wall − grant × rate ≥ R`, whose minimum over the walls the shipped
pair priced is exactly `R` at wall 600, which is why the assert is non-strict by measurement rather
than by taste.

The learned rate needs two guards the shipped pair did not, and both are pinned in
`backend/tests/test_curator_pass_calibration.py`. A sample is `pass_seconds / characters`, and every
pass pays a fixed 4.7–13.9 s of model turns, so a bigger grant measures a cheaper character, which
grants more — a ratchet that runs to the pass that dies. `MAX_PASS_INPUT_CHARS` (8,192) is the rung:
`input_budget(570, 0.0367)` answers the ceiling, not 14,713. The window is what makes the pessimistic
price correctable in the other direction: `price` answers the maximum of the last
`SAMPLE_WINDOW` (12) samples, so a slow pass cannot be averaged away but does age out, and the test
that a 13th sample ends the oldest one's effect is what distinguishes a window from a latch. And a
pass that consumed its grant and committed nothing must still be recorded: the wall's own seconds over
the granted slice is a lower bound on the price of a character, never an optimistic one, which is why
`run` observes it on every failure out of the pass body (`except (Exception, asyncio.CancelledError)`,
shielded so a cancelled pass can still write) rather than only where the ceiling raises — a failure
that leaves no sample is a price that never corrects, and the next tick recomputes the same grant and
dies identically. The personal lane's cut row carries a different numerator and must be read as a
different quantity: it files the granted slice over the seconds its own reading phase spent, recovered
from the events that phase wrote, because two model turns share that lane's one wall and only one of
them is what the grant buys (STAS-500). The meter line a committed pass prints now carries `seconds_per_character=` (what
the pass measured), `priced_seconds_per_character=` (what it was charged) and `price_source=`
(`seed` or `measured`): two independent numbers, so a lane can be read from the log without a query.

Both of those moments now exist on the PERSONAL lane too (`_sample_personal_pass`,
`backend/tasks/agent_schedules.py`), which had no sample at all until STAS-499 and therefore priced
every founder grant off `SEED_SECONDS_PER_CHARACTER`. Read its rows by their own unit, because it is
not the scoped lane's: one personal turn is two model phases inside one wall, so a COMMITTED row
carries `(digest + writer) seconds over digest characters`, while `personal_budget_of` subtracts
`PERSONAL_WRITE_RESERVE_SECONDS` (270) from the wall BEFORE dividing by that same rate — the writer's
share is charged twice, and that row is systematically dearer per character than a digest-only
measurement (the founder pass's 778,2 s over 1 735 characters is 0,4485 s/char, 3,7 times its 0,12
seed, and the next grant there is `int(570 / 0,4485)` = 1 270 characters, not 4 750). The key is
`key_from_configuration(agent)`, which is the scoped lane's `route_class:curator_wiki` as well: one
bucket, so `price`'s max means a personal row prices the scoped lane's passes too, both lanes moving
together. And the cut's sample is a price of the READ, not a lower bound: STAS-500 gave it the
reading phase's own seconds (recovered from the events that phase wrote, the run's close excluded by
name) over the granted slice, so it CAN raise a later grant — as far as `MAX_PASS_INPUT_CHARS`
(8 192), which is where the clamp stops it — and it cannot shorten the writer that filled the wall. A
cut run whose reading session wrote no events files nothing and says so once. None of that makes a row
ignorable: `price` answers the maximum of its window and seeds only while the window is empty, so any
filed rate — a bound included — IS the price whenever it is the dearest row in that window, and there
it can sit above the seed and shrink a later grant; only a dearer measured row already in the window
outprices it. The row
this card refuses is the dear-by-absence one: `RunCeilingExceeded` now carries `by_user`, so a turn a
human stopped files NOTHING on either personal body — five stopped seconds over a 2 700-character
grant would otherwise have taught 0,0011 s/char, and as the max of a window that single row would hand
the next pass `MAX_PASS_INPUT_CHARS`. `test_a_turn_a_human_stopped_teaches_the_price_nothing`,
`test_the_manual_dispatch_files_the_row_its_own_two_lines_print`,
`test_the_manual_cut_files_its_bound_before_the_next_pass_is_priced` and
`test_a_personal_pass_that_read_nothing_leaves_the_calibration_untaught` in
`backend/tests/test_curator_personal_slice.py` are those four claims, one each.

`CURATOR_CATCHUP_PASSES` (`backend/config.py`, default 8) is a CADENCE bound and never was the
entitlement guard: `_chain_permitted` is what refuses a free lane's next paid pass, and the chain
dispatches `metered=False`, so raising the cadence moves `month_run_count` not at all while still
costing the deployment's own model calls. `test_a_free_lanes_chain_...` in
`backend/tests/test_agent_schedule_alerts.py` is the proof the entitlement did not move.
The ladder prints grant, price and headroom for nine witness walls, so the proof is a table in the run
output and not a match, and — via
`test_a_wall_just_above_the_refusal_starts_a_pass_at_the_loader`, which dials a 395 s ceiling with the
reserve in force — every granted wall reaches the
loader at least once. The failure the wall raises says which of the two it was:
`RunCeilingExceeded` is what `asyncio.wait_for` becoming a `TimeoutError` turns into — and its
message carries the whole exit, because a cut run's `last_run_error` is the only witness it leaves: the
wall in seconds, that the feed was left unread, and the position the run was reading from, which is the
drain's committed pair on a draining run and the watermark pair otherwise. Since `draining` is the
dispenser's fact, a `full_history` dispatch works a drain its row does not carry yet, and that pair is
named as freshly seeded and reading from the feed's start rather than answered from a row that holds no
position; a cut that named only the watermark would name a position a drain never reads from, because a
drain's backlog sits behind it. It is the
only failure that continues the catch-up chain — `_run_curator_locked` hands the backlog a cut run did
not reach to the next pass while that chain has bound left, keeping both brakes it always had (the lane
must have advanced, and the passes must be bounded), still marking the run failed and still raising.
A chain that reaches its pass bound with input pending now logs that instead of returning. Pinned by
`test_scoped_curation.py`'s `test_a_ceiling_cut_hands_its_unfinished_backlog_to_the_next_catchup_pass`,
`test_a_ceiling_cut_that_completed_no_pass_starts_no_chain` (the cut that checkpointed nothing has
nothing to hand over, so it cannot become a chain of retries) and
`test_an_exhausted_catchup_bound_names_the_backlog_it_left_behind`.

**A charged tick resolves itself, and only the run that owns a lane may resolve that lane.**
`mark_run` states that every path after it resolves the outcome, and two exits used to return out from
under that promise while `_run_guarded` reads a clean return as success. The one that was a leak —
`_run_scheduled_locked`'s `run_mode != "scheduled"` check, reached when a mode flips between the beat's
`list_scheduled()` and the row re-read inside the run lock — now resolves its own tick at that exit
with the same compare-and-set the escape guard uses, and its cause names the mode it found
(`agent run resolved without running: run_mode=…`), so the row cannot stay `started` with its credit
spent and nothing logged. What is deliberately NOT resolved is the contracted state: a dispatch that
finds the lane's run lock held defers to the run in flight, because that run owns the shared outcome,
which is why such a row may read `started` while the real turn is still running and why
`test_busy_scoped_curator_refunds_only_skipped_run` pins one refund and a NULL error there — a blanket
resolver would report a healthy lane as an outage and take its credit a second time, since the
compare-and-set refunds inside the same statement that writes it. The two rescuers stay the two that
exist: the in-process escape guard for anything that raises out of a body, and the beat's stranded-run
sweep for deaths no process survives; a copy that runs no `beat` has the guard and no sweep at all, and
that gap is reported rather than closed by a third resolver here.

**The run ceiling is one curator's own number on the lane that resolves one, and the
wall is that number capped by the worker and shortened by a commit reserve.**
`agent_service.turn_ceiling_seconds` is the only code in the tree that names a run's
ceiling: the curator's `agents.turn_timeout_seconds`, or `AGENT_TURN_TIMEOUT_SECONDS`
while that column is null. `agent_service.run_wall_seconds` turns that ceiling into the
wall a run may work to: `min(turn_ceiling_seconds, worker_turn_ceiling_seconds()) -
RUN_COMMIT_RESERVE_SECONDS`. The cap is what makes the wall reachable at all — a wall
at or above the worker's own ceiling is a wall the worker beats, and
`SoftTimeLimitExceeded` lands outside the coroutine, where no `except` can resolve the
lane, refund its credit, or continue its chain (the founder lane measured exactly that
on 2026-11-14). The reserve is the time the commit still needs after the last batch
returns: the checkpoint, the `push_event` publish, and `mark_run_succeeded`. The
`scoped-curator-{id}` lock keeps the UNcapped ceiling as its TTL: a fence expiring
while its own run is still writing the wiki would hand the next dispatch a
half-finished run, which is the single-flight guarantee migration 0258 exists to hold,
so fence TTL and wall are two numbers by design and a later reader must not fuse them.
`sprite_agent_service` resolves both once per run. The write
path closes the other gap — `worker_turn_ceiling_seconds()` reads the celery conf, and a ceiling at
or above it is refused at PATCH, because a run the worker kills never reaches the chore that marks
it failed and refunds its curator credit. Both curator lanes are now walled: the scoped lane through
`scoped_curation_service.run`, and — since STAS-447 — the personal lane too, whose curator turn is
wrapped in the same wall and raises the same `RunCeilingExceeded` — which it can only say because the
cut now releases the in-flight pull first: until STAS-457 the personal lane's wall died on closing a
generator whose frame was still running, and the resulting `RuntimeError` was never a `TimeoutError`, so
the ceiling this paragraph promises is what the reap, not the `wait_for`, delivers. Since STAS-456
that personal wall bounds the *whole* two-phase turn rather than only its write: a digest-only run on
the founder's local box used to spend the entire budget reading the wiki into extracts before the write
phase ever began, and a slow digest alone blew past the worker's cap, so `SoftTimeLimitExceeded` landed
outside the coroutine where no `except` could resolve the lane. The cut now arrives cooperatively through
the stop flag (the `wait_for` kept only as a rescue), and a turn that comes back stopped — by the wall or
by a user's Stop, one codepath — raises the same ceiling failure and commits nothing, leaving the drain
pair byte-identical and the row reporting `failed` naming the feed it left unread, rather than advancing
the watermark over a backlog nobody finished reading. Pinned by `test_curator.py`'s
`test_a_personal_wall_cut_arrives_as_a_cut_not_a_cleanup_error` (both wall exits clean — the cooperative
cut and the `wait_for` rescue — and removing the `finally` reap turns the rescue back into the reported
`aclose()` error), `test_a_stopped_personal_curator_turn_commits_nothing`, and
`test_a_committed_personal_drain_pass_prints_the_meter`. A sprite's interactive turn keeps
no wall of its own: it has a different owner and a different budget than a curator's bounded batch.
The lane answer
is `scoped_curation_service.workspace_for_agent`, the same call the dispatcher makes, and it
returns `None` twice — for a folder curator, and for every curator of an account with no External
wiki; those runs take the personal wall. What still does not carry a per-curator number is the
curator *payload*: a lane whose ceiling the API refuses to store keeps
`effective_turn_timeout_seconds: null`, and a PATCH naming `turn_timeout_seconds` on it is refused
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
