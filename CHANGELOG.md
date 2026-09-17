# Changelog

This file tracks user-visible changes. v0 is the open-source baseline —
everything before it is captured in git history (`git log`), not here.

## Unreleased

- The website's CLI reference (`joinstash.ai/docs/cli`) now tells the truth and stays
  true. Two new www guards check the page against `cli/main.py` in both directions —
  every command and flag the page names must exist in the CLI, and every command the
  CLI exposes must appear on the page — and CI's `www-test` job runs them on every
  push. The page had drifted into naming over a dozen commands that no longer exist
  and omitting dozens that do; it now has full two-way parity, so a newcomer who
  types a documented command gets what the docs promised.
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
  instead of hanging on a first-request download; it also ships the node runtime and
  the `pi` coding agent, so local-exec mode works out of the box. And a database that
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

## v0

Initial open-source release.
