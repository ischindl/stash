# Changelog

This file tracks user-visible changes. v0 is the open-source baseline —
everything before it is captured in git history (`git log`), not here.

## Unreleased

### Added

- A local model box now has a reasoning level you can choose. Every endpoint row in
  Settings → Cloud agent model carries a "Reasoning" selector (`off`, `minimal`, `low`,
  `medium`, `high`, `xhigh`, `max`) with an explicit Save, and the level you saved is what
  that box's next turn runs with. Until now the level was not yours to set: Stash shipped a
  fixed `off` and never asked the model to think within a budget, so a curator turn on a box
  whose server reasons by default could spend its entire output on reasoning and answer with
  nothing — the failure that reads as "0 B answer, 27 kB of thinking". Choosing a level
  bounds the thinking phase instead: measured on the founder's own vLLM box over ten
  identical curator turns, the unbounded setting produced 4 empty answers out of 10 where
  `low` produced none, and the median turn went from 44 s to 13 s. `off` keeps behaving
  exactly as it does today — for a box whose chat template reasons regardless, no level is
  what stops it; a level is what stops reasoning from eating the answer. The level is stored
  per box, so a fast laptop box and a rack box can be set differently, and a box whose
  `models.json` you edit by hand refuses a level until you either put the two matching keys
  in that file or reset the override — it will not show you a level it cannot send.

- A curator's run ceiling is now the curator's own setting. One number —
  `AGENT_TURN_TIMEOUT_SECONDS`, default 600 — used to be the wall for every curator, so a lane
  pointed at a slow local model box died at 600 s exactly like a lane that finishes in a minute,
  and the only lever was the environment plus a restart. Every curator row in Settings → Curators
  now has a "Run ceiling" in seconds, with a "Deployment default" button that hands that curator
  back to `AGENT_TURN_TIMEOUT_SECONDS`. The row shows both the number it stored and the number the
  next run will use, so an unset ceiling never reads as configured, and a refused save says so on
  the row instead of looking like a number that stuck. That one number bounds the run *and* the
  lock that keeps a second run of the same curator from starting while the first is still going —
  they are resolved together, because a lock shorter than the wall expires mid-run and hands the
  next dispatch a half-finished run. A ceiling at or above what the worker itself enforces (1500 s
  soft, 1800 s hard) is refused where it is typed, naming both numbers: the worker kills such a
  run before it can record its failure or refund the curator credit it spent, which would leave the
  lane reading "started" until the sweep noticed. The global default stays 600.

- A saved model box can be re-checked without dialling all of them. Every local model
  endpoint row in Settings now has its own "Test connection" button, and it answers with
  the same verdict the add-form's test gives: "Reachable and authenticated — serving 2
  model(s)", "Auth failed (HTTP 401): token_not_found_in_db", or "Unreachable: no route
  to host", with the box's own words unedited. Until now a row was only as fresh as the
  last time Settings loaded, and that load probes every box and returns when the slowest
  one answers. The re-test happens on the server with the key stored for that box — the
  key is never sent to the browser, so a browser-side test would call a healthy keyed box
  an auth failure — and it writes nothing: your key and your saved model list are
  untouched. It answers "is this box alive right now", not "what does it serve today";
  reload Settings for the row's model list to refresh.

- A brand-new account can open its own files without being told a URL. The navigation an
  account gets after signing up listed Traces, Reward models, Skills and Settings, so a
  person who just registered had no way in to the file browser from inside the product:
  the page existed only as an address someone had to know and type, which is exactly why
  a folder shared with them, or a skill their agent published, stayed invisible to them.
  That navigation now carries **Files**, between Reward models and Monitoring, and it lights
  up while you are anywhere in your files — the browser itself, a folder, a page, a table
  — so you always have a way back. One click opens it; the account's shared folders are
  right there. Nothing about who is shown which navigation changed, and the page you land
  on after signing up is the same one as before.

- `stash shares link folder <id>` tells you what "anyone with the link" currently allows,
  and the MCP address that access mints, so switching a link open or shut is a command
  rather than a request typed against your own account. Add `--permission read`, `comment`
  or `write` to set the level, `--permission none` to revoke the link, and omit it to ask
  and change nothing. What comes back is always what the server now holds — it writes,
  then asks again — so after a revocation you see `none` and no address at all rather than
  the word you just typed, and an address you are handed is one that still works. The level
  is chosen from the ones the server accepts; asking about a type it has no link for, such as a
  session, answers `none`, and setting one there fails with the server's own reason. Who a
  folder is shared with by name is untouched: that stays with `stash shares add` and
  `stash shares rm`.

### Changed

- The line this build is cut from now contains the upstream product in full. Upstream
  shipped about a hundred releases since the September split and our trunk had drifted
  away from all of them, so everything upstream owns — the curator, the wiki, the evals,
  the reward models — is now theirs here too, and features that were unreachable because
  we sat behind them (the intuition-MCP surface, resumable wiki curation) are reachable.
  Absorbing them retired work we had done twice over: our curator's slice-the-backlog
  loop and its run timeout are gone in favour of upstream's durable cursor and capped
  catch-up chain (`091a86370`), and our separately-derived turn wall is gone in favour of
  upstream's single `AGENT_TURN_TIMEOUT_SECONDS`. Our local release-number bumps are
  discarded, because upstream is what PyPI serves. Two things deliberately survived:
  the fenced watermark advance, because upstream ships the reset path that a bare update
  silently clobbers, and our own sharing surfaces — the MCP-handle answers for folders and
  shares, and the pi plugin assets — which came through untouched. The MCP integration
  guide's tool roster and its advertised count now match the server, at 102 tools.

- `stash shares ls` now hands over a folder's MCP handle, so reading a share no longer
  needs a raw REST call. A folder whose link is granted has *no* named people on it — its
  answer sits in the same endpoint response as a `mcp_url` the CLI was throwing away,
  because it kept only the `shares` list out of that response. Which meant the one state
  where the handle is the only useful answer printed "No shares found." and `[]`, and the
  hand-off ended with "read the handle with a raw REST call, because the CLI will not tell
  you". Text mode now adds an `anyone with the link — <url>` line, printed unwrapped so it
  is still something you can paste into `stash tools add --url`, and `--json` prints the
  endpoint's whole response — `shares`, `general_access` and `mcp_url` — so
  `stash shares ls folder <id> --json | jq -r .mcp_url` answers. The URL is whatever the
  server sent, character for character: the CLI never assembles, normalises, or defaults
  one, and a private folder — like a server too old to send the key — still says nothing
  at all. **This command's `--json` payload is an object where it used to be a bare
  array**; a script that read it takes `.shares`. The agent-facing `stash_list_shares`
  tool carries the same envelope, under the same name.

- Unregistering a server now unregisters it. `stash tools remove <name>` used to delete only the
  account-side registry row, which left whatever `stash tools install` had written into an agent's
  own MCP config sitting behind: the agent went on listing that server, went on connecting to it,
  and its tools stayed callable — the only way back was hand-editing the agent's file, or
  restoring a snapshot of it. Removal now retracts what installation wrote, in every agent config
  the install touched — remembered from install time, so removing from an unrelated directory
  still reaches a project's own config — and only then deletes the registry row. It stays
  idempotent (a server that was never installed anywhere is simply unregistered, and says so), it
  still never touches the entries you wrote by hand, and it refuses rather than half-succeeding:
  when a config file defines that server name outside Stash's management, or cannot be read as
  JSON, the command names the file and leaves the registry row intact instead of reporting a
  cleanup it did not perform. `--json` now reports the config files it cleaned under `retracted`.

- A local-model turn that answers nothing no longer passes for success. A reasoning/hybrid
  model can spend a turn's whole output budget on its reasoning channel and come back with a
  null message body (`finish_reason=length`) — and the harness still exits 0, so the run
  reported a turn with no text and no error. Now the turn is named: "pi produced no answer
  content: reasoning consumed every output token (finish_reason=length,
  output=8, reasoning=8)", carried through the same redacted error channel a real endpoint
  failure uses. The mid-reasoning transport death is named the same way instead of repeating
  the transport word pi itself offers (`terminated`), which is what a model-agnostic word for
  "the stream died" gets mistaken for. The sentence names the token counts it observed and
  never a limit: the ceiling a turn stopped at belongs to the endpoint (2887 and 1 tokens in
  the founder's log), while 8192 is only the budget we asked for. An empty turn that ended
  without reaching its ceiling is named for what was seen rather than blamed on a spent
  budget — measured on the founder's endpoint, that shape also comes with reasoning at zero
  bytes, so "exhausted" there would send an operator hunting the wrong thing. A genuine
  endpoint error still shows the endpoint's
  own words, a turn that ends in a tool call stays green, and a turn that got no evidence at
  all reports what was observed rather than guessing a budget.

- The backend can run its completions on the model endpoint you connected, not only on a
  cloud key. Before this, one question decided every curator run — is `ANTHROPIC_API_KEY`
  set? — and a self-hosted server, which by design has no cloud key, answered "no" to its
  own curators: the External wiki curator and the workspace's Memory curator refused with
  *"Scoped curation requires the backend ANTHROPIC_API_KEY"* while a working local box sat
  in Settings. Completions are now provider-aware. A backend key means the product behaves
  exactly as it always did, on Anthropic. No key means the run takes the local model
  endpoint you stored in Settings — the box your agent pins, or your default one — and
  answers on it. Nothing ever tries the other provider: a key that is set is not
  second-guessed by a box that happens to be reachable, and a stack with neither says so in
  one message naming both, because a run that quietly switched providers would quietly
  switch whose servers hold your end users' transcripts. A box that is switched off is
  refused before the run starts, quoting the endpoint's own error, rather than spending the
  one metered run your lane gets this month on a failure. A pinned agent may also name the
  model id to use on that box; the base URL and its key always come from the credential.

- Testing a local model box now names its outcome instead of handing you a colour to
  interpret. The button under the draft fields reads "Test connection" — the word you
  already use for it — and it answers with one of three verdicts: "Reachable and
  authenticated — serving 2 model(s)", "Auth failed (HTTP 401): token_not_found_in_db", or
  "Unreachable: connection refused". A box that is down is no longer described as a key
  problem, and a refused key is no longer described as a dead box. Whatever the endpoint
  itself reported is shown unedited, because LiteLLM and Ollama are more specific than a
  bare HTTP status; when an endpoint reports nothing, the line says so instead of going
  blank.

- Sharing with an agent now hands it a server. The folder share dialog shows the folder's
  MCP URL with a "Copy agent URL" button, the skill publish popover shows the MCP URL next
  to the public URL, the skill panel's toolbar copies it, and a published skill's panel
  links to its public page. The old "Agent Handoff" copy button is gone. An agent reads a
  folder through that URL for exactly as long as the folder is open by link or published
  as a skill — the same gate the MCP endpoint itself enforces.

- Your Sessions list is your work, not the curator's diary. The curator files a
  transcript of every run it performs as its own session, and those transcripts used
  to bury everything else: measured live on the founder stack, 897 of 1,285 listable
  sessions were the curator's own, and 28 of the 50 rows on the first page. The list
  hid them only if you once found a checkbox that defaults off — and nobody did.
  The list now applies the same classification the curator's feed has always enforced
  so it never feeds on its own transcripts: `agent-curate-` sessions simply never
  appear in the human-read list, with no parameter to flip and no setting to
  discover. Nothing is deleted — a curator transcript still opens by direct link,
  and the curator's own runs page is untouched; non-curator scheduled agents stay
  visible. The checkbox, the `hide_curator` parameter, and the stored preference are
  removed outright.

### Removed

- The evals product is gone, following upstream's removal of it from the open-source repo. Traces and
  their annotation screen, intuitions, reward functions, the Evals UI, prompt optimization, and the
  workers that trained them are deleted outright rather than hidden behind a flag, and the MCP surface
  drops the 33 tools that served them (`stash_intuition_*`, `stash_optimization_*`,
  `stash_reward_function_*`, `stash_trace`, `stash_traces`, `stash_import_traces`,
  `stash_annotate_trace`, `stash_train_reward_model`) — 102 tools down to 69. Two things stay, because
  upstream kept them and the database still carries them: `reward_models_enabled` and
  `product_checkpoint` remain columns on `users`, so no migration is dropped and the alembic chain
  stays a single head (an API profile keeps reporting the flag even though nothing reads it any more),
  and the `/folders/` navigation is unaffected, because `rail.tsx` carries that matcher itself.

### Fixed

- The folder share panel stays on screen on a narrow display, and with it the address you
  hand an agent. The popover used to be placed by CSS alone: `right-0` pinned it against
  its button and `max-w-[calc(100vw-2rem)]` only ever shrank its width, so the panel's left
  edge landed wherever its button's right edge happened to be and nothing pulled it back —
  on a 420 px window it measured 10 px past the right edge, with the `Agent access (MCP)`
  row and its `Copy agent URL` button sitting in the part you cannot reach. The panel's
  position is now calculated from its anchor in one single place and clamped inside the
  window, so at 320, 420 and 768 px the whole panel including the agent address is on
  screen, and in a desktop window it lands on exactly the pixels it used to.

- A self-hosted curator now gets through its backlog, one slice at a time. On a stack whose
  curator runs on a local model, the nightly curation of a shared workspace used to read every
  event still owed, curate all of it, and only then write down how far it had come — so when the
  run hit its time limit, as a month of transcripts on a slow model always did, the failure was
  total: nothing was remembered, the next night's run started from exactly the same place, and a
  backlog that one hour cannot curate was never going to drain however many nights passed. The
  symptom was a wiki that quietly stopped being written while the schedule looked healthy: on the
  founder's own stack the curation position had not moved in five weeks across 42 runs, each of
  them failing with the same message. A run now walks its backlog in slices and records its
  position after each one, so a run that runs out of time is still a failure but keeps the slices
  it finished, and the next run starts where this one stopped and does strictly less work. The
  budget is its own setting, `CURATOR_RUN_TIMEOUT_SECONDS` (default one hour, kept below the
  worker's own ceiling so the run can still report its cause), and the hour-long limit that stops
  a chat reply is untouched. Nothing about what counts as curated changed: the position still
  moves only to material the run actually read, still refuses to overwrite a move somebody else
  made while it was working, and a slice that stopped in the middle of one instant resumes at that
  event rather than after it, so no material is ever curated by nobody. Guarded by
  `backend/tests/test_scoped_curation.py` and `backend/tests/test_celery_time_limits.py`.

- A curator run that runs out of time now stops itself, and keeps the work it finished last. The
  slicing above only pays off if a run that spends its budget stops *between* slices, which is what
  its own budget check was written to do. It never got the chance: the caller wrapped the run in a
  timer of its own using the same one-hour number, but started counting earlier, before the run had
  chosen a model endpoint, so the caller's timer always won and the run's check never ran. Every
  timed-out run was cancelled rather than stopped, and a cancellation discards the slice in flight
  whole — the model work it had already paid for and the position it had earned, gone. A run now
  starts its clock as its first act, and the caller's timer sits one completion window past the
  budget, so the run's own stop is the exit that fires: a slice that comes back late is kept and
  committed, and the outer timer remains only for a slice that never comes back. That ceiling and
  the run's session lock are now one derived number, so the lock no longer expires while its own
  run is still inside the limit — which used to let a second curation into the same session. What
  an operator reads is unchanged and now actually true: a run out of time still reports `failed`
  with its cause, a run that succeeds after one reports `ran` with the cause cleared, and the next
  run still has strictly less to do. Guarded by `backend/tests/test_scoped_curation.py` and
  `backend/tests/test_celery_time_limits.py`.

- A skill's agent URL survives republishing it. The address under `/api/v1/mcp/skills/…` is
  what a person pastes into their coding agent so that agent can read a shared skill, and it
  used to be written down against a handle that lasted exactly one publication: unpublish the
  skill, publish the folder again — the ordinary way to ship an updated version — and a fresh
  slug was minted, so every URL already pasted into an agent config began answering "This
  Stash share URL is no longer shared." forever, with nothing to restore it and no way to
  learn the new address except to re-paste it into every client it had ever been handed to.
  The handle is now minted once per skill folder and kept there, the way a shared folder's
  agent handle already is: the first publish prints it, every republish prints the identical
  URL, and the URL somebody configured reads the new version. Unpublishing still means off —
  while the skill is unpublished that URL answers nothing — and deleting the folder is the
  only thing that retires the handle, so a folder created later under the same title gets its
  own. Renaming a published skill never rotates it, and no link already in circulation moves:
  the migration copies every currently-live slug across, so nothing someone has already
  pasted changes when this ships. Guarded by `backend/tests/test_share_mcp.py` and
  `backend/tests/test_skill_slug_durability.py`.

- A run that is still working no longer gets someone else's verdict written on it. On the
  founder's account on 2026-10-01 a curator run started at 11:42, and one hour later a second
  copy of that same scheduled dispatch reached a worker while the run was still curating. The
  copy did what a copy should: it stood down and let the run in flight have the lane. On its
  way out it also wrote "skipped: already running" into the agent's row — so for the next half
  hour the console, the API and the scheduler's own history reported a run as skipped when
  nothing of the kind had happened, and the alarm that pages when a run dies without reporting
  was muted by a skip that never occurred. The copy came from the queue, not from a person:
  a scheduled task is acknowledged only when it finishes, so the queue re-offers its message
  after a fixed hour, and a curation that is allowed to run 95 minutes is still alive when
  that happens. Two fixes, one in each place. A dispatch that cannot acquire an agent's run
  lock now records nothing at all — its tick and credit are already spent, but the outcome on
  the row belongs to the run holding the lock, and that run resolves it as done or failed when
  it finishes. And the queue's re-delivery window is now set above the longest run a curator
  may legally take, so a live task's message is no longer handed to a second worker while it
  is working. The trade-off is deliberate: a task whose worker genuinely died now comes back
  after two hours instead of one, because these are paid runs that cannot be safely repeated,
  and a late retry costs less than a duplicate. Older rows keep the value they were written
  with and mean what they said at the time — nothing is rewritten. Guarded by the
  requeued-dispatch case in `backend/tests/test_curator.py` and by
  `backend/tests/test_celery_visibility_timeout.py`.
- A self-hosted stack with no model no longer lets its curators idle in silence. With
  neither `ANTHROPIC_API_KEY` on the backend nor a local model endpoint connected, every
  scheduled curator refused every tick forever — the Memory wiki was never written, while
  Settings still showed a calm "Next run" date and the console page looked healthy. The
  scheduler already knew the reason, but a designed skip is a *resolved* outcome: it records
  no error, the stale-curator watchdog deliberately does not page for it, and no screen read
  the column that held the verdict. Each curator row now reads "No model connected" above the
  line saying what to connect, the developer console's curator page carries the same banner,
  and the beat logs the block at WARNING naming both remedies, so `docker compose logs`
  explains itself to someone who never opens Settings. Connect a model and the state clears
  itself on the next run — there is nothing to dismiss.
- Claude sessions now close in Stash. `~/.claude/settings.json` carried no stash
  hooks at all — the marketplace plugin was supposed to wire them and silently
  never did — and the hook scripts died with a `KeyError` outside that plugin
  because they demanded an env var only it sets, so Claude never emitted
  `session_end` and every session stayed open. The installer now merges the five
  Stash hooks straight into `~/.claude/settings.json` (your own keys and hooks
  preserved, reruns add nothing), retires an old `stash@stash-plugins` install
  with a single best-effort uninstall, and the hook scripts read
  `~/.stash/config.json` like every other agent — so `stash hook run claude
  on_session_end` works from Claude Code, the CLI, or a shell alike. Codex
  benefits too: the stale `_comment` key that poisoned `~/.codex/hooks.json`
  for its parser is swept on install.
- A curator that dies partway through a run no longer cancels every later run. When a run
  was cut short — its own time limit firing, or a deploy killing the worker underneath it —
  nothing was left alive to record that it had failed. Its row stayed "started", which is
  exactly what the scheduler reads as *a run is already in flight*, so every later dispatch
  stood down and the row never changed again. Measured on the founder's account on
  2026-09-28: five curators froze this way, their daily runs stopped silently, Settings kept
  showing the last-known state as though it were current, and pressing the run button
  answered *"Curator run in flight for this agent"* about a run that had been dead for hours
  — the same message that correctly refuses two people starting a run in the same second.
  Two things close the lane now. The dispatch itself notices a run that was killed before it
  could report, and writes the failure with its cause before the worker's own error goes
  out, so the reason the run died stays what gets logged and paged. And a sweep runs every
  five minutes for the deaths no process could ever report — the hard limit, a memory
  recycle, a kill from a deploy — claiming any run that has reported itself as started for
  longer than the wait its own lane lock already allows, and announcing each one it closes:
  the failure, its cause, the released lane. It never overwrites a truth already on the
  record: a run that logged its own success, a dispatch that stood down because someone else
  genuinely held the lane, an earlier failure, and a run that stopped at a designed gate
  (nothing changed to curate, no credential, allowance spent) are all left exactly as they
  are, and a curator that has never run at all is not a stranded one. One thing is
  deliberately not repaid: a dead run's place in your monthly allowance, because the record
  keeps no note of which runs were yours to spend and guessing would move your count either
  way. A stranded run is repaired, not refunded.

- A curator run that ends badly now says how it ended. Two things were lost before. A
  failure that already had a name — "agent turn failed: terminated" — recorded that name
  alone, without the command's exit code, and the code is the one thing that separates an
  endpoint that died mid-reasoning from a client that simply stopped waiting, because the
  harness exits 0 on most of the ways it fails. And everything the command line printed to
  its error stream was poured into the same stream as the transcript, so on a run on your own
  machine the progress noise could bury the single line carrying the cause and the failure
  came back with nothing in it. Those are kept apart now: the transcript stays the
  transcript, and what the command said on its error stream is held on its own, bounded to
  the last few hundred characters and redacted like everything else that leaves the machine.
  A named failure gains the code and those words — what the founder's curators read on
  2026-10-04 was `agent turn failed: terminated | exit=0`, where the same run used to record
  the bare word — a failure with no named cause becomes the exit code plus what the command
  said, so a missing binary or a refused login reads as itself instead of as an empty row,
  and a run that finishes cleanly still records no failure at all.

- Sessions you ended are now shown as ended. The Stash plugin has announced the end of
  every recorded session since its first release — one `session_end` event on the same
  ingestion endpoint its turns use — and the server stored that event and then ignored it.
  `sessions.finished_at` had exactly one writer, the demo router, which never runs for a
  real account, so the column was empty for every session ever recorded (measured: 1,289
  of 1,289 rows on the dogfood stack), and the sessions list — which calls a row open when
  that column is empty — listed weeks-old work as running, with no way to spot the one
  session actually still going. Ingestion now consumes its own close signal: the
  `session_end` event stamps the close at the instant the close itself was recorded, the
  same basis `last_event_at` already rests on, because the shipped plugin sends no
  per-event timestamp at all. It moves forward only, so replaying a month of transcript
  cannot rewind a close that really happened, and activity after a close moves recency
  and nothing else. Nothing derives a close from silence or from age: an imported or idle
  session with no close event keeps the column empty and stays open, and the accompanying
  migration closes only rows whose close event is already in the database. Verified by
  driving the real `stash hook run claude on_session_end` against a throwaway stack — the
  session came back closed at the close event's own stored instant. Caveat: rows that
  never received a close event at all — curator runs, and sessions an integration created
  without hook wiring — still stay open, because inventing their end is the guess this
  fix refuses to make.

- A Pi hook that crashes is now recorded and announced, instead of being silent. Pi's
  hooks are copied byte-for-byte into `~/.pi/` when you connect and then call into the
  installed `stashai` package, so the copied generation and the package drift apart on
  every release until something re-copies them — and when they drift, the copy loses:
  on the machine where this was found, `~/.pi/hooks/session_start` and
  `assistant_message` were dying on every session with
  `ImportError: cannot import name 'echo_stdout' from 'cli.formatting'`, and nothing
  said so anywhere, because Pi reads stdout and a hook that dies before printing looks
  exactly like a session with nothing to report. Every shipped handler now runs its work
  inside one guard — `stashai.plugin.hooks.guard_hook_main`, the same one behind the
  `stash hook run` path the other agents use — which writes the failure, the error text
  and a running `consecutive_failures` count into the `upload_status.json` that
  `stash status` reads, so Pi's row turns `failing` instead of staying blank, and shows
  the traceback. Pi still gets what Pi demands — exit status 0 and one JSON object —
  which here is a yellow systemMessage reading: Stash hook failed; this conversation may
  not be visible to your team. Run `stash status` for details. Pi keeps its own
  generation and its own
  installer rather than being routed through `stash hook run`. Copied handlers now
  require a package at least as new as themselves, so `~/.pi/` converges through
  `stash connect` or `stash settings` — once that package is installed — and not by a
  hand copy into `~/.pi/`.

- Guidance your agents read now converges to the version of Stash you have installed,
  instead of the version you first connected with. Every guided-behaviour file —
  `~/.codex/AGENTS.md`, `~/.pi/AGENTS.md`, and the OpenCode, Gemini and OpenClaw
  equivalents — was written once by `stash connect` and never touched again, so a release
  that rewrote the Skill model reached nobody: on the machine where this was found, five
  agents were still being taught a model the product had already abandoned, and Pi's
  installed session-start and stop hooks were failing outright against an older release.
  The managed block now carries the release that wrote it (`guidance_version=…`), and the
  first session-start hook to find a file lagging the installed build repairs it in the
  background — quietly, with nobody having to re-run `stash connect`; `stash upgrade` runs
  the same repair once the new version is on disk. Only what sits between the
  `stash-plugin` markers is rewritten, so your own prose above and below the block stays
  put. A file whose managed block can't be trusted — an end marker with no begin, or the
  same marker twice — is named and left exactly as it is rather than guessed at, because
  these are hand-editable files and a repair that swallowed someone's own instructions
  would be worse than the staleness it fixes. `stash guidance refresh` runs the repair on
  demand, and reports per agent what it changed, what was already current, and what was
  never installed.

- Project curators run on your own model box again, even if you also have a developer
  workspace. A curator that belongs to one project folder was being sent down the
  developer-platform curation path, which only speaks to Anthropic and demands a backend
  `ANTHROPIC_API_KEY` the self-hosted server has never had — so the run died before it ever
  looked at the model you pinned to that curator, and the project's wiki stopped updating
  while its activity kept coming in. The error said *"Scoped curation requires the backend
  ANTHROPIC_API_KEY"* on a lane that had nothing to do with Anthropic. Which path a curator
  takes is now decided by what that curator curates: a project's folder is read and written by
  its owner alone, so it runs on the credential and endpoint its own row names, and a box with
  no cloud key anywhere on it curates its projects normally. The External wiki curator —
  whose feed mixes your end users' transcripts — and your workspace's own Memory curator
  stayed on the stricter scoped path, which at that point still demanded the backend key;
  what those two lanes do today is the entry above.

- The dogfood stack gets its coding agent back. `backend/Dockerfile.dogfood` had no compose
  consumer at all, so a deployment that sets `AGENT_EXEC_MODE=local` built the pi-free image
  and every local agent turn shell-ed out to a `pi` binary that was not there. A third,
  opt-in overlay — `docker-compose.dogfood.yml` — now builds that Dockerfile against a
  locally built pi-free base (`BASE_IMAGE`, required, no default) and points all five
  backend-image services, the queue workers included, at the one image it produces.
  Self-hosters see no change: the two-file command in the README still pulls the published
  pi-free images, and the published images stay pi-free.

- A database stamped by an intermediate build boots again. The dogfood build booked its own
  revisions `0203`–`0209` on top of `0202`, so those numbers name different content than the
  project's own `0203`–`0209`: `alembic upgrade head` stopped at the unknown stamp
  (`ResolutionError: No such revision or branch '0209'`) and the backend never came up. The
  stamped revisions now resolve, and one convergence revision carries what they never ran —
  the schema those numbers skipped, plus the curator-log archiving and shared-wiki
  revocation, which are a no-op on a database that took the normal path. `backend/tests/test_migration_chain.py`
  now refuses any revision file booking a number a deployed build already stamped.

- Self-host compose contract: dropped the orphaned `collab` override from
  `docker-compose.local.yml` (it named a service the base file does not define, so
  `docker compose ... up` refused the whole project), and the five GHCR pins now name the
  version the repo actually ships. Guarded by `backend/tests/test_docker_release_contract.py`.

- The website's CLI reference (`joinstash.ai/docs/cli`) now tells the truth and stays
  true. Two new www guards check the page against `cli/main.py` in both directions —
  every command and flag the page names must exist in the CLI, and every command the
  CLI exposes must appear on the page — and CI's `www-test` job runs them on every
  push. The page had drifted into naming over a dozen commands that no longer exist
  and omitting dozens that do; it now has full two-way parity, so a newcomer who
  types a documented command gets what the docs promised.
- The three worst entries on that page now show the invocation the CLI really accepts.
  `stash sessions push` demands `--session` and `stash skills create` demands
  `--description`; the page wrote the first as optional and never mentioned the second,
  so copying either example ended in `Missing option`. `stash tables import` takes its
  data through `--file` or a piped stdin, not the positional `<file>` the page insisted
  on, which failed as `Got unexpected extra argument`. A third www guard reads
  requiredness and positionals straight from the CLI's own signatures, so the next time
  a required flag is documented as optional, CI reddens instead of your terminal.
- The MCP integration guide (`docs/stash-mcp-integration.md`) is back and rebuilt against
  what actually ships: the local `stash-mcp` stdio server with its current tool roster,
  the VFS path format, and the `stash tools` bring-your-own registry. The server-hosted
  transport the old guide described is gone from the product, as are the tools it listed
  that have since been removed — and a new guard test
  (`cli/tests/test_mcp_integration_doc.py`) now fails the build whenever the guide and
  the code drift apart.
- Your local model setup in Settings is a list of endpoints now, not one box at a
  time. Add one by its base URL and test it before storing it: the test lists exactly
  the models that endpoint answers with, and those are the only ones you can then pick,
  so a mistyped address or a model the box does not serve is caught before anything is
  saved — and if a reachable endpoint refuses the key you gave it, it says so rather
  than pretending the box is broken. Retyping retires a test result, and a failed test
  or a refused save keeps what you typed instead of throwing it away. The stored key is
  masked, and can be revealed only on the endpoint that actually holds it. Deleting an
  endpoint that agents still pin no longer strands them: the refusal names them
  (`Wiki curator — Project Atlas` and the rest) so you can move them first. And an endpoint a
  probe cannot reach stays on the page with the error beside it — it used to disappear
  entirely, because the old list was drawn from whatever a probe happened to answer.
- A new Curators section in Settings shows the agents that write your wiki and lets you
  aim each one at a model you choose. The workspace curator reads everything you do;
  each project curator reads only its own project. Every curator gets a model picker
  grouped by local endpoint, defaulting to the oldest connected one, and a model whose
  endpoint stops serving it stays listed instead of quietly falling back somewhere else.
  A project curator can also run a second model as a digest pass, which pre-reads its
  feed before the main run: it runs on the same endpoint the curator is pinned to, and it
  is offered nowhere else — the workspace and shared-wiki curators deliberately stay
  single-model, the latter because its feed is other people's material. Edit any
  curator's schedule, switch a project curator to idle so it only runs when asked (the
  workspace curators always keep a schedule), add a curator for a project that has none,
  and delete a project curator when you are done with it — the wiki pages it wrote stay.

- What you pick for a curator is now checked when you save it, not when it next
  runs. A model pick belongs to your local boxes, so moving a curator that carries
  one to a cloud provider is refused with the reason named, instead of saving a
  combination that then failed every later run. Clearing the pick in the same save
  is accepted, and a curator already aimed at a cloud provider is untouched by the
  rule. The endpoint list also stops waiting on each box in turn: one powered-down
  box shows its error beside itself while the reachable ones still list their
  models, instead of the Settings view hanging on whichever box is unreachable.

- The same check now covers a curator's digest model too. Pointing the digest at
  a cloud provider remains a valid choice; naming a model for it there no longer
  saves, because a model belongs to the local provider and that pairing is exactly
  what killed every later digest run mid-turn, after the write had been accepted.
  Clearing the pick in the same save is accepted, a digest already on a cloud
  provider without a model is untouched, and Settings itself is unaffected — it
  always runs a curator's digest locally, so the refusal only meets anything
  saving through the API directly.

- Self-hosters and containerized deployments get the features that until now only
  worked when someone built a custom image by hand. The backend image now ships the
  local embedding model, so semantic search answers immediately after a deploy
  instead of hanging on a first-request download. The `pi` coding agent and its node
  runtime no longer ship in the image — the shipping image is pi-free by directive;
  a deployment that runs local-exec pi sessions builds `backend/Dockerfile.dogfood`,
  an overlay on the pi-free base that adds node+pi and nothing else. And a database that
  was migrated by the old dogfood image's chain no longer sits in a state where the
  curator's designed "already running" skip is rejected by the database — one repair
  migration brings both migration histories to the same schema.
- The memory curator can no longer lose ground it has already made. Its read marker — how
  far it has read into your history — is now advanced with `greatest()`, so a run that
  started before another one finished can never overwrite the newer position with the older
  one, and completed curation stops being thrown away. Importing history still re-opens that
  marker (imported material has to get curated), but now only for the wiki whose feed can
  actually read the events: one session import used to drag both curators back to the same
  microsecond. A position the database refused is logged with the number it kept instead of
  vanishing silently. The developer console's Backfill keeps the marker too — it re-reads the
  whole history and records nothing, so the incremental position you already paid for
  survives, and the screen now says that instead of promising the watermark is cleared. And a
  curator that is already running no longer gets a second run stacked on top of it: the extra
  dispatch resolves as a designed skip that costs nothing — no second harness turn, no charge
  against the monthly allowance.

- The shared wiki's curator now reads only the history that was actually
  offered to it. The external (developer workspace) wiki's event feed, run gate,
  and watermark are scoped in SQL to sessions whose end user has `share_wiki`
  enabled, so an opted-out user's conversations never enter the delta the
  curator chews through — the filtering that previously lived only as prose in
  the curator's instructions. The cheap "did anything change" gate applies the same
  rule, so a beat no longer wakes an external curator for a delta its feed would
  then find empty. The owner's own Memory wiki is unchanged: it still curates the
  whole workspace. `stash changes` gains a `--wiki internal|external` flag so an
  agent can name the wiki it means; omitting it keeps today's behavior, and an
  unknown value is refused with a clear error instead of silently widening scope.
  CLI `stashai` 0.1.368.

- A source you never actually connected now says what is missing instead of
  promising a retry that can never help. Not-connected providers, a provider key
  the server was never configured with, and an empty account/channel selection
  now park the source as "Needs setup" with the concrete reason — "not connected
  to gmail", "TWITTERAPI_IO_KEY is not set — set it in the backend environment to
  sync X saves", "Choose the Gong accounts to index — nothing syncs until you
  do." — instead of a red "Sync hit an unexpected error — it will retry
  automatically" that retried a permanent configuration state forever. A
  genuinely temporary failure (a network blip) still retries on its own schedule.
  Background dispatch also claims each source at enqueue time, so a stale queue
  message can no longer re-run a sync that already happened: on a freshly seeded
  dev machine the work queue now stays empty instead of growing by ~300 doomed
  jobs an hour, and `seed_dev` seeds its demo sources parked so a new stack does
  not manufacture that load at all.
- The `stash` CLI restores its machine-readable output contract for AI agent
  consumers. `stash --json <command>` now works globally on any command (OR'd
  with each command's own `--json`) and stdout carries only parseable data; all
  human-facing errors, progress, and empty-state notices print on stderr.
  Errors are classified instead of flattened: 0 = success, 1 = user/auth-style
  error (bad input, 4xx), 2 = usage or internal error (missing/unknown
  arguments, transport failure, 5xx), with 20 reserved for future agent
  signals; under `--json` a failure emits a single-line
  `{"error": {"status_code", "detail", "class"}}` envelope on stderr and never
  a traceback. A misspelled command or wrong argument now appends a one-line
  `Hint:` on stderr with a Did-you-mean suggestion or a pointer to that
  command's `--help`; stdout and `--json` output are never affected. Mutating
  commands that change nothing (`connect`, `disconnect`, `rm`, `restore`,
  `skills follow`) exit 0 honestly as `{"ok": true, "changed": false}` instead
  of reporting ad-hoc prose or an error. The CLI's own test suite gained a
  coverage gate so this contract cannot silently rot again.
- Cancelling a `stash` command is now a clean exit instead of a crash report.
  Answering `n` to a confirmation prompt, or pressing Ctrl-C at a prompt, while
  a command works, or during startup, prints one `Aborted.` line on stderr and
  exits 1 — no Python traceback, and no silent exit 130 for Ctrl-C mid-command.
  Genuine bugs still print their traceback.
- Tools + Chat (the Agents chat rail) is opened per email domain via the
  backend's `TOOLS_AND_CHAT_DOMAINS` env — `/users/me` now returns
  `show_tools_and_chat` and the frontend reads that flag instead of a
  hardcoded domain list. Defaults unchanged (`heaviai.com`, `ferganalabs.com`).
- The internal-email domain list is now self-host config: `INTERNAL_EMAIL_DOMAINS`
  (comma-separated) decides which email domains get free Pro and internal
  analytics classification — no source edit needed. Defaults unchanged
  (`ferganalabs.com`, `joinstash.ai`).
- Curators and agents now work in containerized local-exec deployments: the
  backend image ships the `stash` CLI the harness shells out to, and
  `LOCAL_STASH_API_URL` points the CLI's callbacks at the backend service
  (the localhost default still covers the dev-laptop case).
- The Developer Platform console can now point a workspace's agents at a local
  model: the curator page has a "Local model" section that connects an
  OpenAI-compatible endpoint (Ollama or similar — a tunnel or self-host) on
  the workspace's behalf. Every agent of that workspace, the nightly wiki
  curator included, runs on the connected endpoint; the operator's personal
  model settings stay untouched. The connected endpoint also carries an
  editable pi `models.json` override (a "test connection" check verifies the
  endpoint before it is saved), so a self-hoster can add models or tune
  context windows instead of accepting the synthesized default.
- Agent history is now semantically searchable: `GET
  /me/sessions/events/semantic-search` answers in plain text over the
  meaning of your recorded sessions, and a background backfill embeds events
  that were recorded before embeddings existed so nothing stays unfindable.
- The developer console now routes the shared memory per project: the Sessions
  tab groups every session under its project (folder, cwd fallback), carries a
  "Feeds shared memory" switch per project beside the per-user one, uploads
  transcripts GUI-side, and moves sessions between projects. A project's
  opt-in only widens an opted-in user's material — a user's own opt-out stays
  the hard floor. Newly created projects start dark.

- Shared developer wikis require an independent privacy review of each complete
  page before customer retrieval. Existing unreviewed pages are quarantined,
  edits invalidate prior approval, and customer views omit raw provenance,
  unreviewed folder names and shared attachments. Private wikis remain available.

- Developer wiki curation can edit a small passage without regenerating an entire
  page. Runs start from new input, skip scopes with no new documents, and use
  smaller responses to reduce timeout and truncation failures.
  History, extracted files, and source documents share a bounded input budget;
  large inputs resume from saved offsets without advancing past unread content.
  Combined session documents retain chunk bounds per event, keeping source
  offsets distinct from paging offsets in the supplied document.
  Automatic catch-up attempts share a seven-run allowance per curator over 24
  hours, across all triggering chains. Busy attempts preserve the active run's
  status, concurrent page creation forces a fresh snapshot, and stale alerts
  respect slower schedules and ignore the curator's own wiki output.

- Recover from duplicate page titles during developer wiki curation. Successful
  batches respect a history-event text budget; paid developer accounts queue up
  to seven bounded catch-up passes instead of waiting another day; stale backlogs remain visible in alerts
  even after a successful batch. Truncated responses get one smaller-response
  retry without executing partial writes. Concurrent human edits stay protected,
  and overlapping curator passes skip without reporting a false failure.

- Replace the marketing homepage with a concise verifier research introduction,
  a Fergana-inspired interactive valley, and matching social previews.

- Bring in intuition models with a playground, prediction inbox, editable
  examples and rubrics, and evaluated model versions. Click a trained reward
  model to score or compare responses using its saved checkpoint, revisit run
  history, or replay its saved training pairs.

- Continue into the next trace subsection with Down at the last row, or the
  previous subsection with Up at the first row.

- Preserve trace-table sorting when opening a trace and returning to the list.
- Show the current section's range and offer “View all steps” so nested trace
  overviews do not look like the end of the trace.

- Select traces with checkboxes or Shift-click ranges in the current sort/filter
  order, then delete the selection together. Failed deletions remain selected
  for retry.

- Remove the compute provider and epoch count from expanded reward-model rows.

- Simplify reward-model creation to a name and selected traces. Use managed
  training defaults, remove the reward-criteria, epoch, and base-model controls,
  and remove the explanatory sentence beneath the dialog title.

- Label traces by source, with stable source IDs and editable names. Filter the
  trace list and training picker by source and import dates, and select only the
  shown traces when creating a reward model.

- Show reward-model training stages and time estimates from comparable completed
  runs or live batch timing, with explicit queue waits and unknown estimates.

- Replace reward-model cards and the dashed empty-state box with a compact,
  searchable table, status filters, exact timestamps, and expandable model details.
- Explain when model training is not configured instead of returning an internal
  server error, and display creation errors above the training controls.

- Keep the reward-model creation sheet open when clicking outside it, preserving
  trace selections until it is closed with the close button or Escape.

- Give user messages a quiet tint and accent, and start a new phase with the
  user's reply or approval. Reduce the completion curve to 30% opacity.

- Add a faint, smooth estimated task-completion curve to the trace minimap with its own 0–100%
  scale, evidence-based checkpoints, and separate segments for independent requests.
- Outline the selected section on the minimap, shorten visible step ranges, and
  show every step in the side rail with stronger phase markers.

- Keep focus on the selected minimap step after clicking or dragging, so Left and
  Right continue scrubbing chronologically from that step.
- Let trace messages scroll continuously between phases, with compact dividers
  and a breadcrumb that follows the current phase. Remove redundant comment prompts.
- Show the called tool above its inputs when expanded. Hide descriptive step
  badges while retaining the underlying annotations and scores.
- Tighten trace-list and collapsed message rows to 28px and show exact local import timestamps, with
  the timezone available on hover.

- Show readable action descriptions in tool rows, with raw inputs on hover and
  tool-name explanations. Label expanded tool arguments as Inputs and use neutral
  field labels. Keep section-title descenders visible within compact rows.

- Keep trace sections scoped to their own steps, with Left/Right navigating up
  and into sections. Remove the duplicate Back control. Add message comment
  icons and a contextual menu for selected text, including multi-message quotes.
- Use evenly spaced collapsed messages, quieter code typography, and scores
  without grading breakdowns. Label context separately from numbered tasks.
  Use dense, single-line section rows with setup grouped under Context. Remove
  browse-table checkboxes and duplicate search, tighten trace-list rows, and
  place ±1 first in the credit-scale toggle.

- Start trace messages collapsed, including assistant messages, with previews and
  consistent neutral row labels. Call assistant messages “Assistant” throughout.
  Replace task score cards with compact, unboxed headers containing inline values
  and the step range, without explanatory captions.
  Use a compact outline icon to return to the sections overview.

- Replace the separate Show less row with a subtle collapse icon in the message's
  left gutter, with a tooltip explaining that it collapses the text to a preview.

- Keep same-named files in connected Google Drive folders separately indexed,
  preserving each file's contents through renames and repeat syncs. Report
  duplicate skill names with the documents to choose from instead of silently
  loading one of them.

- Align the sticky Show less control with the left edge of message content.

- Add trace comments through a context menu, including selections spanning multiple
  messages. Left/Right now move between steps from the trace view; Enter opens
  sections and Esc goes back. Messages and tool calls start fully expanded.
  Show only the thin position line on the minimap, without a focus rectangle.

- Default trace credit charts to ±1 and omit the inactive Sections heading at
  the overview level. Fit scaling and navigation within sections remain available.

- Add Fit / ±1 trace-credit scaling and use baseline ticks for unscored steps.
  Separate task score summaries from message rows, with the task's step range,
  final answer, total work, and capped score. Show every suggested training base
  model in the picker and clarify that evaluation uses separate source traces.

- Plot step credit around a centered zero line: positive bars rise, negative bars
  fall, and zero has no filled height. Add an explanatory rescore tooltip.

- Keep the trace minimap marker on the selected step after a jump by matching
  scroll tracking to the space reserved for the sticky breadcrumb. Preserve
  clicked selections when a jump reaches the bottom, until the viewer scrolls
  or navigates elsewhere.

- Align trace step numbers with their message headings and move the theme toggle
  to the bottom-left sidebar.
- Leave sections containing only supplied context ungraded instead of assigning
  success scores to system instructions, requests, or tool observations.

- Simplify trace navigation and remove the label summary, Labels/Scores toggles,
  scoring explainer, and redundant navigation hints. Keep long-message collapse
  controls visible while scrolling, use warm Stash colors in dark mode, and show
  ungraded messages as minimap dots with single-line previews below the chart.

- Recover locally rejected long-trace scoring jobs after upgrading the worker.
  Hover statuses distinguish failed, queued, and running scoring jobs.

- Keep all recorded messages chronological, with compact inline expansion,
  left-gutter step numbers, syntax-highlighted code, and collapsible input and
  output rows. Remove the separate message reader and add a persistent theme toggle.
- Keep section cards one click away while the step view can scroll through the
  entire trace. Preserve the global minimaps, including navigation before scores
  arrive, and show action credit inline.
- Replace noisy scoring errors with a compact rescore control. Process long
  recordings in bounded, overlapping context windows instead of rejecting them;
  automatically resume traces rejected by the previous size limits.
- Show creation and training status for reward models trained on the current trace.
- Open single-task traces directly on their subtasks or recorded steps. Show
  readable section titles immediately while generated summaries load.
- Add local trace snapshot imports that retain saved grades and a lightweight
  worker for remote API scoring of new local imports.

- Organize traces by requests, progress subtasks, and distinct activities without
  capping section counts or splitting work into equal chunks. Generate concise
  activity titles and summaries. Let longer lists scroll, and use up/down
  arrows to select, right to open, and left to return to the parent section.
- Keep the left trace minimap at the whole-trace scale inside subsections,
  highlighting the containing task. Scrub between global tasks without changing
  the map’s scale; arrow navigation resumes afterward.
- Show cached section success scores with local objectives and an explanation.
  Judge each partition against the requests active within it, including unrelated
  tasks in the same conversation; insufficient evidence stays unscored.

- Browse long traces from request summaries into smaller sections and individual
  steps, with hover previews and direct minimap/comment navigation.
- Count adjacent tool calls and outputs as one displayed step. Render question
  replies and tool values readably while preserving source links and comment selections.
- Align plain action-credit numbers, remove full-row score colors and duplicate
  Details buttons, make user messages collapsible, and start comments closed.
- Load compact saved action and outcome scores together with the trace, avoiding
  a second download of evaluator prompts and responses before bars appear.

- Display embedded screenshots inline in traces, with click-to-enlarge previews.
  Re-uploading a native transcript restores its images without changing existing
  steps, comments, or scores.
- Give ungraded trace events no bar height, removing the alternating stripes
  while preserving their hover, click, and scrub positions.

- Replace blank trace scores with visible loading, queued, recalculating,
  waiting, insufficient-evidence, or failure statuses.

- Start the comments panel closed on traces with no comments, while preserving
  manual open/close choices during updates.
- Make the first trace navigation tick jump to the very top, including system messages.

- Move trace sharing into the three-dot actions menu.

- Remove the trace's Expand all control; individual tool details still open on demand.

- Scrub through traces and conversations by dragging along the left navigation
  rail, with immediate scrolling and a live preview of each destination.
- Keep trace controls in two compact rows: back, title, score, and actions above
  the step map. Remove the All / Conversation / Tools toggle and keep all steps
  visible. Move execution timelines into the scrolling content.

- Remove the action-credit legend and visible annotation-progress row to give
  the trace more vertical space, and place the imported timestamp beside the
  title. Scores and grading statuses remain available on hover and focus, with
  a progress summary for screen readers.

- Use continuous expected action credit from −1 to +1, calculated from saved
  classifier probabilities, across trace bars, credit summaries, and new training
  comparisons. Preserve insufficient-evidence abstentions and original verdicts.
- Remove the trace-map instruction line and show action scores or grading status
  immediately on hover or keyboard focus, replacing delayed native tooltips.

- Explain trace-bar heights with a harmful/neutral/helpful legend and show
  annotation counts, pending scores, insufficient evidence, and failed updates.
  Retry invalid automatic-classifier responses up to three times per batch,
  retaining completed batches and recovering previously stranded annotations.

- Keep previous action-credit bars visible while a growing trace is evaluated
  again. Earlier annotations are clearly labeled and replaced as current scores
  arrive; unscored new steps no longer make the entire chart appear to collapse.

- Simplify the latest reward-model workspace to Traces, Reward models, and
  Monitoring, with current skills, changes, and feedback under Monitoring.
  Search trace content, review numeric success and action-credit summaries,
  and share traces with teammate name suggestions.
- Keep the trace minimap visible, color bars by action type, size them by credit,
  and jump to the exact selected step. Remove evaluation-history panels and
  align conversation rows consistently.
- Train personal reward models from saved automatic action and outcome
  annotations. Keep training and evaluation tasks separate, exclude stale or
  disputed labels, and monitor saved model scores on recorded runs.

- Reward-model links on the marketing site open the app, and the trace-list
  alias `/reward-models/traces` redirects to `/reward-models` instead of a 404.

- Automatically extracted corrections retain the conversation and tool results
  through the user's feedback when drafting agent instructions. Unreviewed target
  guesses can be corrected or left unresolved. Older untouched drafts are archived
  and regenerated once; human reviews and released instructions are preserved.

- Automatic Jev evaluation recovers traces skipped during rolling deployments
  and retries stale database query plans. Migration backfill preserves active
  workers. Corrections without a recorded repository can still be interpreted
  and reviewed; only instruction creation remains unavailable. Recovery respects
  accounts using an older product checkpoint or with workbench access disabled.

- Skill generation automatically retries invalid model-generated names and
  descriptions, so an overlong description no longer immediately fails the job.

- Open traces, session transcripts, and chats have a slim conversation rail:
  hover a tick to preview a turn, click to jump, or navigate with the keyboard.
  Reading an earlier chat turn now holds your place while a response streams.
  Trace turn previews use assistant responses rather than thinking events, and
  transcript navigation also works in the Developer Platform console.

- Accounts can be assigned the October 5 Floodgate product checkpoint, retaining
  its Traces, Reward models, and Skills experience in the main app.

- Automatic Jev evaluations no longer stop at a daily call limit. Traces paused
  by the previous cap resume automatically and reuse their saved results.

- Agent Workbench automatically uses Jev to judge trace success and estimate each
  action’s credit from recorded results. No grader setup is required. Inspect
  saved evidence, confidence, previous trace versions, and evaluation errors
  separately; corrections can propose repository-scoped agent instructions.

- New signups see only the Developer Platform, with internal navigation,
  onboarding, and settings hidden by a per-user flag. Existing accounts retain
  both interfaces.
- Developer curation enforces sharing in backend tools: opted-out inputs stay
  in separate private runs that cannot write to the shared wiki. Developer
  curators use the backend Anthropic model without workspace credentials or
  shell access. Opting out archives the previous shared corpus privately and
  rebuilds from permitted inputs; existing opt-outs are migrated on deployment.
- External curator audit details now stay in run transcripts. Existing shared
  `Log` and `changelog` pages move to a private workspace archive, with public
  and explicit page shares removed.
- CLI onboarding redesigned (#940). `stash signin` walks a first-run wizard
  that can be re-run anytime with the new `stash setup` — no answer is final.
  Session recording is framed as private-by-default and on by default
  (`stash stop` pauses). The agent picker uses `[x]` checkboxes where enter
  toggles and a `Done` row saves. `stash connect` works in any folder — a git
  repo is no longer required. History import runs in the background via the
  new `stash import-history` (parallel uploads; `--status` attaches a live
  progress bar). Re-uploading a transcript for a deleted session now reports
  a clean skip instead of a 404 error, and no longer pollutes plugin upload
  health.
- `stash memory` is now a command group (#941): `stash memory write "<Path>"`
  creates or updates a Memory wiki page (stdin for long bodies) and
  `stash memory ls` prints the wiki tree — the direct write surface for
  agents that maintain the wiki themselves. Bare `stash memory` and
  `--recompute` are unchanged.
- The nightly cloud Memory curator has an off switch (#942):
  `stash memory --curator off|on`, also surfaced in the web curator panel.
  On-demand recomputes keep working while it's off.
- Scheduled agent run history now reports each run's status, error, duration,
  event count, and tool count while preserving the chronological transcript
  feed used by the agent workspace.
- Scheduled agent runs no longer crash in local dev mode: the MCP registry's
  `.mcp.json` is now written to the local simulated workdir instead of the
  literal `/home/sprite/work` path, which is unwritable on dev machines.
- Gong call documents now link back to the original call in Gong.
- `stash vfs stat` once again shows the source-sharing command for connected
  source roots, including roots that do not have an app URL.
- OAuth reconnects now require a stable provider account identity. Slack,
  Asana, Jira, Linear, Notion, and Gong connections refuse to store new
  credentials when identity lookup fails, preventing retained source data
  from silently continuing under a different provider account.
- Frontend server-side backend requests now require `BACKEND_INTERNAL_URL`
  or `NEXT_PUBLIC_API_URL` instead of guessing an environment, so missing
  managed deploy config fails during build rather than crashing public
  Stash pages at runtime.
- Added a committed `docker-compose.local.yml` override for laptop
  self-hosting dry runs. It exposes backend, frontend, and collab on
  localhost ports and disables Caddy.
- Self-hosting now uses a prebuilt `ghcr.io/fergana-labs/stash-frontend`
  image alongside the backend and collab images, so
  `docker-compose.prod.yml` no longer builds application containers on the
  target machine.
- Backend now routes markdown and HTML uploads to the pages table on the
  one upload endpoint, so every surface (frontend drag-drop, CLI `stash
  files upload`, MCP `stash_upload_file`) gets the same behavior. The
  response is a discriminated `{kind, ...}` payload — `kind: "page"` for
  md/html, `kind: "file"` for everything else.
- MCP server gained ten tools to reach parity with the CLI on agent-
  useful surfaces: discover (`stash_search_public_stashes`,
  `stash_read_public_stash`), page search (`stash_search_pages`),
  session ops (`stash_session_transcript`, `stash_delete_session`),
  invite management (`stash_create_invite`, `stash_revoke_invite`),
  stash access control (`stash_set_stash_access`), and table tooling
  (`stash_update_table`, `stash_export_table`).
- Renamed the three unprefixed MCP tools to share the `stash_` prefix
  with the rest of the surface: `stash_list_trash`, `stash_restore`,
  `stash_purge`.
- Added `BACKEND_INTERNAL_URL` env var so docker / self-host deployments
  route the Next.js server-side fetches at the in-network backend
  hostname instead of looping through the public URL. Public Stash pages
  no longer 500 on a fresh self-host boot.
- Added `INTEGRATIONS_ENCRYPTION_KEY`, `ANTHROPIC_API_KEY`,
  `ANTHROPIC_MODEL`, and `ANTHROPIC_FAST_MODEL` to `.env.example` —
  every variable `backend/config.py` actually reads is now in the
  reference file.
- Refreshed user-facing docs (`README`, `ARCHITECTURE`, `USE_CASES`,
  `DESIGN`, the `frontend/docs/*` pages) to match shipped product
  surface: real concept names, real CLI commands, real container set
  for self-hosting.
- Bumped the Claude Code plugin to 0.1.84 so the cached
  SessionStart context refreshes — older versions injected
  `stash history *` / `stash notebooks list` references to commands
  that no longer exist.
- Added `stash vfs`, an app-level virtual filesystem shell for browsing
  Stash with bash-shaped commands and editing existing writable pages.
- Kept `stash mount` hidden as experimental spike code; the supported
  production path is `stash vfs`.
- The developer console now routes the shared wiki per project. Sessions are
  grouped by the project they are filed under and each project carries a
  shared-wiki switch that starts OFF. A project that is off contributes nothing
  to the shared wiki, not even from users who opted in, so material filed under
  a project stops informing it until you clear that project. Unfiled sessions
  and Default-folder sessions keep routing exactly as before, and a user who
  opted out stays out whatever a project says.
- Pi is a supported coding agent again. A stray merge had silently dropped it
  from the installer registry, so `stash setup` never offered Pi and Pi
  sessions went unrecorded with no error to read. `stash setup` again detects
  a `pi` CLI or `~/.pi`, installs the hook runtime into `~/.pi`, and
  `stash status` / `stash settings --json` report Pi upload health like every
  other agent. The restore is guarded by merge-revert and installer
  call-shape tests so a future careless merge fails the suite loudly.
- Pi's instruction for sharing a coding session was a command that cannot run:
  it passed a session id as a bare positional, and `stash share` accepts none,
  so the CLI refused it before authentication and a Pi agent could not share a
  session at all. Pi now teaches the same supported form the other agents ship.
  The sweep that found it corrected guidance that had fallen behind the CLI
  elsewhere too: `stash skills create` now shows its required `--description`,
  `stash sessions push` its required `--session`, `stash upload` is written
  with its path, and table rows are queried with `stash sql` instead of a
  `stash tables search` that never existed. Pi was also the last agent still
  being taught to read a file by piping it through `sed` to keep only the first
  eighty lines — a habit the VFS stopped documenting everywhere else because
  silently dropped lines go unnoticed, and one that survived here only because
  Pi's guidance was carried in from a branch that missed that cleanup. Pi now
  reads the file whole like every other agent. Every `stash` command documented
  in the shipped agent guidance is now parsed in CI against the real CLI parser,
  so instructions can no longer drift out of sync with the tool unnoticed.
- Every agent you connect now learns the same Skill model from one canonical
  block, delivered through the file each agent actually loads. Gemini and Hermes
  guidance files carry it (STAS-210 shipped it to only a third of the agents and
  CI stayed green); a fresh `stash connect` also drops a Cursor project rule and,
  when Hermes is detected, a `HERMES.md` in the repo; `stash setup --agent openclaw`
  writes it into the OpenClaw workspace `AGENTS.md` even when the extension was
  already current. `stash prompts agent-guidance` and the Claude session-start
  hook compose from the same block and no longer teach CLI forms the parser
  rejects (a `stash skills create` without `--description`, a bare `stash upload`).
  Two CI guards hold the line: one fails if any supported agent's channel loses
  the block, the other parses every documented command — shipped files and
  composed runtime strings alike — against the real CLI.
- A request that crashes the server is named in the log again. The web middleware catches
  every unhandled request exception and logs the method, path, exception type and traceback
  frames before answering 500 — but startup migrates the database in-process first, and that
  Alembic run's logging config switched off the application logger it found already loaded.
  The process kept serving and kept swallowing its own failures: a broken route produced a
  500 for the caller and not one line about why for whoever runs the server, which is how a
  lane can be failing for hours with a healthy-looking console. Startup now re-enables it
  once migrations finish, so the failure and its frames reach the one INFO handler on
  stderr. Guarded by the startup case in `backend/tests/test_security_headers.py` and by
  `backend/tests/test_startup_logging.py`.

## v0

Initial open-source release.
