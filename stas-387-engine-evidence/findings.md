# STAS-387 findings — directive-shaped text reported in a bash tool's stdout

Card: STAS-387. Review level 3 (Full). Deliverable: evidence and a verdict, no
product change in either repository, no pull request.

This file grows one section per step. The gate that governs its order: no
hypothesis, no candidate status, and no claim about a mechanism appears in this
file before the Step-0 commit recorded below exists.

## Measurement roots (every number in this file names one)

| Root | What it is | How this session reaches it | Revision / state measured |
| --- | --- | --- | --- |
| R1 | This stash card worktree (`stas-387`, branch `fusion/stas-387`) | native, cwd-relative git | `ce61de53a` (`ce61de53a79d0f0a4dbf46c5205d2e584cc342bd`) |
| R2 | Engine checkout at the Fusion trunk, repo `Fusion`, not this one | `git --git-dir=$HOME/git/Fusion/.git` (read-only, accepted by the boundary guard) | `refs/heads/main` = `c7d02a3e5499726565524677c99eb5689cb1b5be`, commit date `2026-11-04T15:38:03+01:00` |
| R3 | The live daemon process that runs this board | parent-chain walk via `ps`; its public `/api/health` | self-reports version `0.78.0-beta.9`; **no git revision exposed**; exact commit not identified — see "R3 limit" below |
| R4 | Persisted records under the project `.fusion` directory (per-card task stores, per-agent run logs, artifacts) | absolute paths derived from `$HOME` at runtime (accepted); `../../` traversal refused | 294 task directories, all `STAS-*`; no other board's cards live on this disk root |
| R5 | The installed agent runtime — the component that owns the `bash` tool (outside both repositories) | absolute paths derived from `$HOME` at runtime (accepted) | `@earendil-works/pi-coding-agent` `1.5.0`, 339 dist files, newest mtime `2026-11-04 11:35`, 0 files touched after the incident minute |

Raw captures: `raw/environment-census.txt` (R1/R2/R3 plus shell, hook, wrapper and
capability ledgers), `raw/sentinel-roundtrip.txt` (AC0), `raw/contamination-scan.txt`
(R4 ledger scan). All regenerate via `bash stas-387-engine-evidence/generate.sh`.

## Step 0 — baseline captured before any hypothesis

### R1: the card worktree

- `git rev-parse --short HEAD` = `ce61de53a`; branch `fusion/stas-387`; shared
  common git dir `/home/schindler/git/stash/.git`.
- `git status --porcelain` = 0 lines once this directory is committed (it read
  `1` while the evidence directory was still untracked).
- Exclusion legality, proven rather than assumed, with **no ignore file
  committed**: `git check-ignore -v .engine/x .fusion-tmp/x` resolves to
  `/.engine/` at `.git/info/exclude:33` and `/.fusion-tmp/` at line 34 of the
  **common** exclude, so both names are excluded for every worktree of this
  repository.
- Measured form correction: with neither directory present, the bare-name
  command `git check-ignore -v .engine .fusion-tmp` returned **rc=1 with no
  output**, because those patterns are directory-only and git cannot classify
  an absent bare path as a directory. Child-path form is the form to rely on.
- `.engine` is absent in this worktree: no nested engine checkout was
  provisioned, so no second engine checkout at a divergent revision exists.

### R2: the engine trunk, and how the two quoted revisions relate

- `refs/heads/main` = `c7d02a3e5499726565524677c99eb5689cb1b5be`
  (`RUFU-395: own branch-conflict refusals in a card register and silence
  exhausted parks`, committed `2026-11-04T15:38:03+01:00`).
- The revision the reporter measured, `37b7575e8970045018e7e48e8625a6bf4a665303`
  (`RUFU-462: report the resolved V8 heap ceiling at boot, on health surfaces,
  and in Mailbox`, `2026-11-04T13:46:52+01:00`), **is an ancestor of the trunk**
  (`merge-base --is-ancestor` rc=0), `rev-list --count` = 1 forward and 0 back.
  The two revisions the body quotes are therefore one commit apart on a single
  line of history, not divergent measurements of two worlds.
- Both revisions declare the same release version in
  `packages/cli/package.json`: `0.78.0-beta.9`. Semver cannot discriminate them,
  which matters for R3 below.

### R3: the live daemon, and its limit

- Reached by walking this session's own parent chain with `ps`: the daemon is
  `/usr/bin/node --inspect=127.0.0.1:9229 packages/cli/dist/bin.js dashboard
  --host 0.0.0.0`, started `2026-11-04 18:54:14` local (`+01:00`). Its script
  path is **relative**, so the checkout it runs from is not recoverable from
  `ps` output.
- Attempt to resolve that process's working directory
  (`readlink /proc/<pid>/cwd`) was **refused** by the worktree boundary guard,
  verbatim: `Command targets a path outside the worktree boundary. Commands
  must run inside the worktree.`
- The daemon's own public surface answers: `GET /api/health` →
  `status=ok`, `version=0.78.0-beta.9`, and **no git revision field**. The two
  endpoints that would carry more, `/api/version` and `/api/system/info`, both
  answer `{"error":"Unauthorized","message":"Valid bearer token required"}` —
  reading them is an operator leg.
- One behavioural bracket is measurable without any of that. The live health
  response carries `heapLimitIsV8Default`. Searching engine source at R2 shows
  that token in `packages/dashboard/src/server.ts:246`,
  `packages/dashboard/src/routes/register-system-maintenance-routes.ts:164` and
  its test file at `37b7575e89`, while the **parent** of that revision returns
  no match (rc=1), and `git log -S` names `37b7575e89` as the introducing
  commit. A live surface that serves that field therefore runs a build **at or
  after `37b7575e89`** — the reporter's own revision is a lower bound on what
  the reporter was sitting in, not merely a different tree.
- That bracket has a second consequence: the built `packages/cli/dist/bin.js`
  in the trunk checkout is dated `2026-11-02T13:12`, before the commit that
  introduced the field the live daemon serves. The trunk checkout's `dist` is
  **not** the live build, so R2 must never be quoted as "what is running".
- **R3 limit, stated not repaired:** exact live revision = **running build
  unidentifiable — fix liveness unverified**. The bracket says `>= 37b7575e89`
  and cannot say whether it equals trunk `c7d02a3e54`. Every revision-bound
  claim later in this file inherits this line.

### AC0 sentinel matrix — byte-for-byte round-trip

Sentinel `STAS387-SENTINEL-4f9d1c7b6e2a`, expected payload 30 bytes (sentinel
plus one newline). Captured with python3 as the byte dumper, so no byte-dump
utility is assumed. Each shape was captured twice: to a file (what the shell
emitted) and to this session's own tool result between `TOOL_CHANNEL_BEGIN` /
`TOOL_CHANNEL_END` markers (what the model channel shows).

| Launch shape | Command | exit | captured_bytes | byte_equal |
| --- | --- | --- | --- | --- |
| login-and-rc | `bash -l -c "printf '%s\n' …"` | 0 | 30 | YES |
| interactive-rc | `bash -i -c "printf '%s\n' …"` | 0 | 30 | YES |
| non-login-plain | `bash -c "printf '%s\n' …"` | 0 | 30 | YES |
| initialised-empty | `env -i bash --norc --noprofile -c "printf '%s\n' …"` | 0 | 30 | YES |

- All four captures are byte-identical to the expected payload; the tool-channel
  renderings carried the sentinel line and nothing else. **No unexpected byte
  appeared in any capture**, so nothing here jumps the card to AC1.
- The `interactive-rc` shape emitted two stderr lines (`cannot set terminal
  process group`, `no job control in this shell`) — job-control noise on stderr,
  recorded because the capture claims to be complete; stdout stayed clean.
- **No-task session shape: `not-read`.** No tool available here opens an agent
  session with no task, and anything this card can spawn is itself task-scoped.
  Two substitutes are recorded instead and are labelled substitutes: the
  initialised-empty control, and Step 1's read of whether the tool-result
  assembly path branches on session or task type at all. The live no-task
  capture stays an operator leg (AC6).

### Shell, hook and wrapper census

- `PROMPT_COMMAND`, `BASH_ENV` and `ENV` are all **unset** in the tool's shell.
- The tool's shell is `/bin/bash` with `argv0=/bin/bash`, `SHLVL=2`,
  `BASH_VERSION=5.3.20(1)-release`; `SHELL` names `/usr/bin/zsh`, which the tool
  does not invoke.
- **Sourcing discriminator:** six variables exported only by the home startup
  files (`TMPDIR`, `TMP`, `TEMP`, `DENO_TLS_CA_STORE` from `.bashrc`;
  `EDITOR`, `QT_QPA_PLATFORMTHEME` from `.profile`) are **all unset** in the
  tool's shell, while `bash -l -c` in the same session shows
  `TMPDIR=/var/tmp/agent-scratch`. The tool's shell therefore sources **no**
  startup file; it inherits the daemon's environment. PATH entries that rc
  files historically appended are inherited launch artefacts, not evidence of
  sourcing inside the tool's shell.
- Startup files present: `.bashrc` 4708 B, `.bash_profile` 172 B, `.profile`
  319 B; `.bash_login` absent. `$HOME/.env` (102 B) is not a bash startup file
  and bash never sources it; it is named only and its contents appear in no
  artifact of this card because it holds a secret value.
- Directive-shaped content in those files: **0 lines** in each of `.bashrc`,
  `.bash_profile`, `.profile` (pattern: `respond with an empty|^System:|IMPORTANT:`,
  case-insensitive). The `printf` lines that grep finds in `.bashrc`
  (lines 16-19, 32-34) sit inside its colour-test function body, not at source
  time.
- `core.hooksPath` is **unset**, so hooks resolve in the shared common dir
  `.git/hooks/`, holding engine-owned `pre-commit`, `prepare-commit-msg` and
  `commit-msg` guards that write to commits and are not invoked by a tool
  stdout.
- Wrapper check on the instrument: `bash`, `sh`, `git`, `python3`, `node` all
  resolve under `/usr/bin`. The two home-directory PATH entries ahead of
  `/usr/bin` (`~/.pi/agent/bin`, `~/.opencode/bin`) hold one binary each —
  `fd` and `opencode` — neither of which shadows the interpreters above.

### Capability probe ledger (forms bound for all later steps)

| Form | Outcome |
| --- | --- |
| `git --git-dir=$HOME/git/Fusion/.git …` | **accepted** (rc=0) → bound for all R2 reads |
| `ls $HOME/git/stash/.fusion/tasks` (derived absolute) | **accepted** → bound for R4 store reads |
| `ls $HOME/git/stash/.fusion/agents` (derived absolute) | **accepted** → bound for R4 run-log reads |
| `ls ../../tasks` (relative traversal to the project root) | **refused**: `Command targets a path outside the worktree boundary. Commands must run inside the worktree.` |
| `readlink /proc/<pid>/cwd` | **refused**, same message |
| redirect to the null device (`> /dev/null`) | **refused**, same message — a slash-prefixed token in the command string; scratch uses worktree-relative paths |
| platform fetch tool against the loopback dashboard | **refused**: `ERROR [blocked-host]: Blocked private host: 127.0.0.1` → `curl` from bash reaches the public health route instead |
| `curl localhost:4040/api/health` | **accepted**, no auth required |
| `fn` CLI (`fn project list`, `fn task list -P RunFusion`) | **accepted** from this worktree |

### Cross-board dedup probe (measured, not inherited)

- `fn project list --json` from this worktree lists 25 projects including
  `RunFusion` (`proj_9326d74a133b421a`, path `/home/schindler/git/Fusion`) and
  `stash` (`proj_203869387892476c`). The cross-board probe is therefore
  reachable from a card worktree; it is **not** an operator leg on this host.
- `fn task list -P RunFusion` returned 798 lines. Scanning those titles for
  `inject|stdout|tool output|empty message` returned exactly two cards,
  `RUFU-586` (planner overseer writing duplicate steering comments) and
  `RUFU-775` (description write re-injecting the Original Description into
  PROMPT.md) — neither owns directive-shaped text in a tool-output channel.
- This board: `fn_task_search`-equivalent search plus the R4 disk scan found no
  other Stash card owning this. The disk store holds only `STAS-*` rows, so
  this board's forensics are complete on disk while RunFusion's are title-level
  only through the CLI: **no cross-board duplicate owner found on either board,
  by named instrument.**

### Contamination ledger

Written to `census-exclusions.txt` and regenerated as `raw/contamination-scan.txt`,
which also re-checks every excluded entry. Measured carriers of the literal:
this card's `PROMPT.md` and `task.json`, the Workflow Planner's and the
reporter's own agent memory files, the registered planning packet artifact
("Card packet: prompt injection in bash tool output…"), and this card's
`generate.sh` (it must name the string it searches for). Two further carriers
are deliberately **not** excluded, because they are primary agent run records
rather than write-ups: `agents/agent-e7428828-runlogs-run-d60fbce1.jsonl` (1
matching line, file mtime `2026-11-04T16:24`, the reported incident minute) and
`agents/agent-94d01490-runlogs-run-3d8a64bf.jsonl` (1 matching line, mtime
`2026-11-04T19:06`, after the report circulated). Steps 2 and 3 read them with
a time bound and a row-type discriminator.

### Step-0 commit gate

- Step-0 commit SHA (the commit that first landed this directory):
  `296dfc9c0d73f95785b496c5eeb285876c8b7cc2`. Before it, this file contained no
  candidate status and no mechanism claim; the candidate table below was added
  with every row reading `pending`.

## AC1 — reproduction or cannot-reproduce

**Verdict: cannot reproduce.** Not "no anomaly" — the anomaly is real to the
reporter and unreplicable from every position this card can occupy. Full
account, one variable per line, each with the surface that measured it.

Variables varied, and what each one produced:

| Variable | Values tried | Surface | Outcome |
| --- | --- | --- | --- |
| Shell initialisation mode | login+rc, interactive+rc, non-login, `env -i --norc --noprofile` | R1 card worktree, through the same bash the tool uses | sentinel 30/30 bytes, `byte_equal=YES`, exit 0 in all four |
| Shell startup-file content | rc files as found (0 directive-shaped lines each) | R1 `~/.bashrc`, `~/.bash_profile`, `~/.profile`, `/etc/bash.bashrc`, `/etc/profile` | no directive text, no `PROMPT_COMMAND`, no `BASH_ENV`, no `ENV` |
| Launch shape actually used by the tool | `bash -c` (or `-s` on legacy WSL) — non-login, non-interactive | R5 `dist/utils/shell.js:17-18`, spawn site `dist/core/tools/bash.js:63` | rc sourcing excluded by the spawn code itself |
| Engine revision | `c7d02a3e54` (trunk) and `37b7575e89` (reporter's) | R2 bare repository, whole tracked tree each | literal absent at both (rc=1, 0 lines) |
| Runtime build | `@earendil-works/pi-coding-agent` 1.5.0, 339 dist files | R5 install tree | literal absent; 0 files newer than the incident minute |
| Task vs no-task session | task-shaped measured live; no-task shape recovered from the reporter's own run record | R1 sentinel + R4 run log `run-d60fbce1` (`taskId=''`) | no task/no-task difference available to measure live; the incident run is measured to be a no-task heartbeat |
| Command content | the sentinel command only | R1 | no injection |

Surfaces read: R1 shell/init/hook/wrapper census; R2 both revisions; R5 the
runtime that owns the tool; R4 agent run logs (9 543 JSONL files — a count
measured on 2026-11-04, stated as prose with a timestamp because the platform
appends a new run log for this card's own session while any capture runs, so it
cannot be a captured constant), the board
SQLite store read-only (86 tables, 30 text columns in 20 message/log/session/
event/document tables), `.fusion/tasks`, `.fusion/artifacts`, the MCP agent-log
surface on this card both filtered and unfiltered (482 entries).

Surfaces refused or unreachable, named so the verdict is not read as total:
R3 could not identify the running build (`/api/version` and `/api/system/info`
auth-gated, `/proc/<pid>/cwd` refused, `fn_web_fetch` blocks loopback); the
model's own context for the incident run was never persisted on any surface, so
the bytes the reporter actually saw are unrecoverable; the reporter's session
transcript does not exist under the runtime's directory; `.fusion/logs` holds no
matching line; other boards' cards live in another deployment and were reached
only through the read-only `fn` CLI, not on disk.

## Step 1 — the tool-output pipeline, located at file and line

Full line-level census: `raw/engine-source-census.txt`. Summary, with the root
each number came from.

### Two instruments, named separately

- **Instrument A — the installed agent runtime** (root **R5**, outside both
  repositories): `@earendil-works/pi-coding-agent`, version `1.5.0`, installed
  under the npm global tree, 339 `dist` JS files, newest file mtime
  `2026-11-04 11:35`, and **0 files modified after `2026-11-04 17:24`** local —
  so this is the build that was in place at the reported incident minute, not a
  later reinstall. This component owns the `bash` tool.
- **Instrument B — the Fusion engine at R2 plus its ACP driver**
  (`packages/pi-claude-cli`, `packages/engine`): it spawns and reads the
  runtime, sanitises assistant text and persists results. It is a different
  instrument from A, and no composing code in it sits on the bash-result path.
- The engine's `pi.ts` is a session host, not the tool host; the earlier habit
  of naming it as the tool pipeline measures the wrong instrument.

### Where a command's stdout becomes model-visible content

`<runtime>/dist/core/tools/bash.js` (Instrument A) is the only site:

| Line | What it does | Can it place non-command text in a result? |
| --- | --- | --- |
| 92-93 | streams `child.stdout` and `child.stderr` into one `onData` sink | adds nothing, but the two streams are **indistinguishable** in the result |
| 221 | `output.append(data)` into the `OutputAccumulator` | no — verbatim bytes |
| 187 | `content: [{ type: "text", text: snapshot.content or "" }]` | no — and an empty result becomes an empty string, not a notice |
| 233-235 | `formatOutput(snapshot, emptyText = "(no output)")` | **one string**: `(no output)`, only when the command produced nothing |
| 239-249 | three bracketed truncation notices naming the line range and the temp file | **harness-authored**, byte-predictable, shaped as a notice |
| 254 with 271/275/282/297 | `appendStatus` appends one of exactly four strings: `Command aborted`, `Command timed out after N seconds`, `Command terminated without an exit code`, `Command exited with code N` | **these are the only non-command-derived strings** this instrument can attach |
| 326 | `wrapToolDefinition(definition)` | no — measured pass-through (`execute` forwarded verbatim, 1 call site) |

So "does the harness append text to a tool result?" answers **yes, with a finite
enumerable vocabulary**: four status strings, three truncation forms, one empty
placeholder. None is directive-shaped and none uses a `System:` prefix. That is
the ruling AC2 demands for candidate 1 — a code path read at file and line, not
the absence of one string at one revision.

### Instrument B's measured role

- `packages/pi-claude-cli/src/acp-driver.ts:165` `sanitizeText()` strips ANSI
  and C0 control characters and caps chunk size; call sites are `:401` (tool
  **input** JSON), `:420` (`agent_message_chunk`), `:426` (`agent_thought_chunk`).
  The same file greps **0 lines (rc=1)** for `tool_result|toolResult`: the ACP
  driver has no tool-result branch at all.
- `packages/engine/src/agents/agent-logger.ts:474`
  `const type = isError ? "tool_error" : "tool_result"` — the engine classifies
  and persists a finished result and composes no text into it.
- Consequence for AC3: a rule placed in Instrument B cannot govern this channel,
  because the choke point lives in an upstream package neither repository owns.

### The reported literal, tree by tree (no absence claim crosses roots)

| Root searched | Command | Result |
| --- | --- | --- |
| R2 trunk `c7d02a3e54`, whole tracked tree | `git --git-dir=… grep -n '<literal>' refs/heads/main` | rc=1, 0 lines |
| R2 at `37b7575e89`, the reporter's revision, whole tracked tree | same command at that rev | rc=1, 0 lines |
| R5 runtime `dist` tree, 339 files | `grep -rl '<literal>'` | 0 files; also 0 files for `System:`, `empty message`, `empty response` |
| R2 near-variant `empty message` | `git --git-dir=… grep -c` | 24 files, all prose. The two the body names, re-derived at trunk: `packages/dashboard/src/routes/register-chat-routes.ts:95` and `packages/engine/src/execution/interactive-ai-session.ts:62` — both comment prose about telling an empty message apart in logs, both confirmed unrelated |

### Assembly site for AC3, in one sentence

Sanitisation of this channel belongs at `formatOutput`/`appendStatus` in
`<runtime>/dist/core/tools/bash.js` — an upstream package — because no file in
either repository this platform owns lies on that path.

### Step-1 commit SHA

`4576a7e2e` — the commit that landed this section together with
`raw/engine-source-census.txt`.

## Step 2 — the differential, and the incident's own session shape

Raw: `raw/channel-forensics.txt` (section "The spawn argv"),
`raw/environment-census.txt` (shell census, wrappers, hooks).

- **Instrument comparison named.** The Step-0 sentinel ran through the raw
  shell (R1, `bash -c`, four initialisation shapes). The production instrument
  is the runtime's bash tool, which spawns `bash -c` too
  (`<runtime>/dist/utils/shell.js:17-18` → `args: ["-c"]`, consumed at
  `dist/core/tools/bash.js:63`). Same shape, so the differential transfers:
  four shapes, byte-identical output, exit 0 — shell initialisation cannot be
  the vector, and the spawn code says the same thing independently.
- **The reporter's launch shape, recovered not re-run.** AC0 could only measure
  the task shape live; the incident's own run record carries `taskId=''` — a
  **no-task heartbeat session** — so the missing shape is now identified from
  R4 rather than inferred. That run opened at `15:20:32.961Z`, closed at
  `15:24:52.623Z`, and issued **20 bash calls**, the last three at
  `15:22:52.047Z`, `15:23:01.154Z`, `15:23:27.259Z`, immediately before the
  report row. The incident window is those calls.
- **Context echo, time-bounded, ledger applied.** Searching all 9 543 run-log
  files for the literal returns exactly two files, one row each, and in both the
  row type is `text` (assistant prose): `15:24:43.352Z` in the reporter's run
  and `18:05:00.294Z` in `agent-94d01490`'s. The five ledgered carriers are
  excluded as reporters, not candidates. So nothing retrievable pre-existed the
  report: the reviewer's memory file is `16:26:11Z`, the planner's `18:19:37Z`,
  the packet artefact `16:26:59Z` — all downstream.

## Step 3 — forensics, and the discovery that decides the AC

Raw: `raw/channel-forensics.txt`.

- **The row-type discriminator is vacuous on this platform, and that is the
  finding.** Applying it as written — literal in `tool_result` rows vs
  assistant-text rows — the tallies are 0 of 24 and 0 of 25 in the incident run.
  But the persisted rows for a bash call contain the tool *name* and timing and
  nothing else (`text='bash'`, 4 characters; the longest persisted
  `tool_result` text field across both carrier runs is 16 characters, another
  tool's name). Payload persistence is gated by
  `persistAgentToolOutput` (`packages/engine/src/agents/agent-logger.ts:204`, R2)
  and is off here. So "the directive is not in the persisted tool results"
  measures what the platform stores, not what the command printed.
- **The same is true through the MCP surface.** `fn_task_logs_read` on this card
  (482 entries, `type=tool_result` filtered and unfiltered) returns rows whose
  whole content is the tool name — read both ways, so the claim is not based on
  one call mode. (Both calls also appended their own rows to that log, a
  self-observation worth noting for anyone re-deriving: reading the log changes
  it.)
- **The board's own database holds nothing either.** 86 tables; 30 text-bearing
  columns in the 20 message-, log-, session-, event- and document-shaped tables;
  **0 rows** match (read-only connection, `mode=ro`). Combined with the
  names-only run logs: **no surface on this platform persists a bash call's
  payload text at all**, which is precisely why this class of report cannot be
  audited after the fact — the card body predicted this and the measurement
  confirms it.
- **Scope rule applied to the scan itself.** The R4 scan of stored surfaces
  excludes `~/.pi/agent/extensions`-style scratch (`.fusion-tmp`) and this card's
  own directory under `tasks/`. Both exclusions exist because the excluded files
  are copies *this card wrote* of the evidence — they quote the literal because
  the card put it there, which makes them reporters, not candidates, and makes a
  scan that included them find more files on every re-run.
- **Which card the reporter was executing:** none — `taskId=''` in every row of
  that run, so there is no card log to read for it, and the per-card log surface
  could not have held the payload anyway.
- **Guidance surfaces searched for the invariant** (added here because AC3 needs
  them): `~/.pi/AGENTS.md` returns 0 lines matching
  `instruction|system prompt|authority`; the engine trunk returns 0 lines for a
  tool-output-untrusted formulation anywhere in `packages`; and the bash tool's
  own system-prompt contribution is one guideline — `"You can inspect PI_*
  environment variables for current model and session details."`
  (`<runtime>/dist/core/tools/bash.js:34`).

## AC2 — candidate census

Statuses, each with the command that decided it and the root it ran against. No
status rests on an empty grep alone; the two exclusions rest on code paths.

| # | Candidate | Surface that can decide it | Status |
| --- | --- | --- | --- |
| 1 | Harness/daemon appends directive-shaped text to tool results | R5 runtime source; R2 engine source | **ruled out as stated** — every string the tool can attach is enumerable and none is directive-shaped: four `appendStatus` strings, three truncation notices, one `(no output)` placeholder (`bash.js:233-254, 271-297`); the wrapper is pass-through (`:326`); the engine's ACP driver has no tool-result branch at all (0 lines). Caveat kept honest: the runtime is upstream code, and extension load paths beyond `~/.pi/agent/extensions` were not censused (0 of 3 installed extensions define a bash tool, control: the runtime defines exactly 1) |
| 2 | Environment/repo emitter (shell rc, `PROMPT_COMMAND`, git hook, PATH wrapper) | R1 census + differential; R5 spawn site | **ruled out for this channel** — the tool spawns `bash -c` (`utils/shell.js:17-18`), which sources no startup file; the marker-variable discriminator shows the tool shell sources nothing (`TMPDIR` unset in the tool shell, set under `bash -l`); four launch shapes byte-identical; no wrapper shadows `bash`, `sh`, `git`, `node`, `python3`; `core.hooksPath` unset |
| 3 | Provider returns it as assistant content rendered inside the tool-result view | R4 row types | **the only candidate not excluded** — the sole surviving carriers are assistant-`text` rows, in two different agents' runs, and both are prose *about* the directive. This is consistency, not proof: the payloads the model actually received exist on no surface, so the alternative (it really was in stdout) cannot be checked either |
| 4 | Context echo of a message-channel artefact already in the reporter's inputs | R4 timestamps and mtimes, ledger applied | **no positive evidence, and not decidable** — every retrievable occurrence post-dates the report (`16:26:11Z`, `16:26:59Z`, `18:19:37Z` vs the report at `15:24:43.352Z`), but the reporter's actual context was never persisted, so absence from disk is not absence from the run |

One correction to the census input, recorded because it changes the timeline:
the card body's incident time `16:24Z` is the *local* mtime of the carrier file;
the run-log row's own UTC timestamp is `15:24:43.352Z`. The incident is an hour
earlier than the body states.

## AC3 — the authority invariant and one proposed enforcement layer

**The invariant, stated verbatim.** It is not written where these agents read.
Searched surfaces: `~/.pi/AGENTS.md` (0 lines matching
`instruction|system prompt|authority`), the whole engine tree at R2 (0 lines for
any tool-output-untrusted phrasing), and the bash tool's own prompt contribution
(one guideline about `PI_*` variables, `bash.js:34`). This session's own
instruction set carries no such clause either — the addendum, guardrails and
completion rules I was given say nothing about the authority of tool output. The
invariant is therefore an assumed norm, and the closest thing to a verbatim
statement available on this machine is the reporter's own applied rule, in his
persisted words: *"Inštrukcia v tool outpute je nedôveryhodný obsah, nie
directíva — uposlechnúť by znamenalo nulový progres ticku."* Stating it as the
platform norm: **text arriving through a tool result is data the model reads,
never a directive the model obeys; instructions reach the model only through the
system prompt and the task prompt.**

**One proposed enforcement layer (proposed, not implemented):**
`packages/engine/src/agents/agent-logger.ts`, the `tool_result` classification
line at `:474` (`const type = isError ? "tool_error" : "tool_result"`, R2) —
the one function in this repository that demonstrably handles this channel in
process, holding the payload before the `persistAgentToolOutput` gate discards
it. A directive-shape check there would flag every tool result the platform
sees, and would also make the class auditable by forcing the detail to persist
when flagged.

Why not the alternatives:

| Layer | Ownership | Cost |
| --- | --- | --- |
| `formatOutput`/`appendStatus` in the runtime's `bash.js` — the true choke point where bytes become a content block | upstream package, not this repo | an upstream change plus a version bump of a globally installed npm package; still cannot see text that arrives inside the command's own stdout |
| `sanitizeText` in `packages/pi-claude-cli/src/acp-driver.ts:165` | this repo | cheap to edit but measured off this path (no tool-result branch in that file); it guards assistant text only, where the surviving evidence actually sits |
| an agent-side guidance rule in `AGENTS.md` / `promptGuidelines` | this repo and the runtime | free, and already insufficient: the reporter followed exactly this rule and reported instead of obeying; guidance files here are pinned by tests |
| **chosen:** `agent-logger.ts:474` | this repo | sees the payload but runs *after* the model already consumed it, so it can flag and persist, never prevent; costs a shape check on every tool row plus false positives on legitimate prose about injections — which this very card's text would trigger |

That last row is the honest limit: with the payload unpersisted and the model
downstream of the runtime, **this platform's only fully owned lever is
auditability, not prevention.** Nothing is implemented here: the card forbids
product-code changes, the guidance files are test-pinned, and the prevention
locus is an upstream package.

## AC4 — surface-naming discipline

Every ruling line above carries the command and the root it ran against.
No absence claim crosses roots: an empty grep in R2 says nothing about R3, a
clean agent log says nothing about a rendered view, and a search on this board
says nothing about RunFusion.

Register of surfaces this card could not read, so no claim in this file rests on
one: the running daemon's build identity (R3, above); the reporter's model
context and tool payloads for `15:22:52Z-15:23:27Z` (persisted nowhere);
`~/.pi/AGENTS.md` content beyond grep matches (`ls` on that directory refused by
the worktree guard, the file itself greppable); pi extension load paths beyond
`~/.pi/agent/extensions`; the RunFusion board's own database (reached only
through `fn task list -P RunFusion`, 798 lines, where only RUFU-586 and RUFU-775
loosely match and neither owns this); `.fusion/logs`, which exists and holds 0
matching lines.

## AC5 — evidence outlives the worktree

`stas-387-engine-evidence/` is committed in this worktree; `generate.sh`
regenerates the captures; Step 5 attaches them to the card and mirrors the
verdict into a task document under a **new** key.

What this looks like once measured, so the claim is checkable and not a promise:

- **Six commits on `fusion/stas-387`**, every one of them touching only this
  directory (`git show --name-only` returns 0 outside paths for all six):
  `296dfc9c0` Step 0, `4576a7e2e` Step 1, `7158a8a35` Steps 2-4, `b8b2cd105`
  determinism, `d41df2827` porcelain field removal, `88e9e214e` regenerated
  captures.
- **The captures are byte-reproducible.** Deleting `raw/*.txt`, re-running
  `bash stas-387-engine-evidence/generate.sh`, and comparing leaves
  `git status --porcelain` at **0 lines**. Getting there required five fixes, all
  of them cases where the capture was measuring the card instead of the platform:
  live HEAD replaced by the pinned Step-0 baseline commit, the child PID redacted
  from captured stderr, this card's scratch directory excluded from the R4 scan,
  the `git status` line count dropped as self-referential, and this card's own
  attachments and registered artifacts excluded from the scan for the same
  reason. The run-log file total left the captures altogether and is stated as
  timestamped prose instead, for the same reason.
- **Outside the worktree**, the same content is attached to the card, registered
  as managed artifacts, and mirrored as a task document under the new key
  `verdict-stas-387`. Deleting this worktree therefore costs nothing: the branch
  commits remain in the repository, and the card carries the evidence and the
  verdict on its own records.

## AC6 — operator legs

Four legs, each with the instrument it needs, because this card cannot walk any
of them:

1. **Runtime identity of the running daemon (R3's unresolved question).**
   Needs an operator shell on the host: `readlink` on `/proc/<pid>/cwd` for the
   node process listening on 4040, or the authenticated `GET /api/version`. The
   card could not: the guard refuses `/proc` paths and the endpoint is
   auth-gated. Until then every revision claim in this file inherits R3's limit.
2. **Enable payload persistence, or accept the blind spot.**
   `persistAgentToolOutput` (`packages/engine/src/agents/agent-logger.ts:204`) is
   a configuration decision with a real cost — tool payloads, secrets included,
   then land on disk. This card proposes it as a question, not a change.
3. **The upstream runtime change.** The only preventive locus is
   `formatOutput`/`appendStatus` in `@earendil-works/pi-coding-agent` 1.5.0,
   whose source is not on this machine; filing it needs the operator's route to
   that package's owner.
4. **The follow-up card.** This session has no task-creation or delegation tool,
   so the text is drafted below for the operator to file, and is also offered as
   a completion recommendation:

   > **Title:** Persist and alert on directive-shaped text in tool results
   > **Body:** STAS-387 measured that no surface in this deployment persists a
   > bash tool's payload (`agent-logger.ts:204` gate; run-log rows carry tool
   > names only; 0 hits across 30 text-bearing columns in the board database),
   > so a tool-output injection report cannot be audited after the fact. Decide
   > (a) whether `persistAgentToolOutput` is enabled for reviewer/executor
   > agents, accepting that payloads reach disk, and (b) whether
   > `agent-logger.ts:474` should flag directive-shaped results instead of
   > discarding them. Prevention belongs upstream in
   > `@earendil-works/pi-coding-agent` `dist/core/tools/bash.js`
   > (`formatOutput`/`appendStatus`) and needs an upstream issue.
   > **Depends on:** STAS-387.
