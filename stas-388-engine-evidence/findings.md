# STAS-388 findings — tool-result channel authority: chokepoint re-derivation and enforcement

Card: STAS-388. Review level 3 (Full). Deliverable shape depends on one gate:
AC1's branch decision. Branches G1/G2 ship an engine change plus a red-green
test; branch G3 ships the named chokepoint, an unapplied proposal, and a
decision request — and that is a complete outcome, not a failure.

**Ordering gate.** No hypothesis, no branch declaration, and no claim about a
layer appears in this file before the Step-0 commit recorded at the end of the
Step-0 section exists. Everything above that marker is measurement.

## Measurement roots (every number in this file names one)

| Root | What it is | How this session reaches it | Revision / state measured |
| --- | --- | --- | --- |
| R1 | This stash card worktree (`stas-388`, branch `fusion/stas-388`) | native, cwd-relative git | `e2140862a` (`e2140862ac7f52a549e729d048a93e8c19cdde07`), equal to `refs/heads/main` of this repository |
| R2-A | Engine trunk, **read-only**, through the bare git dir of the sibling checkout | `git --git-dir=$HOME/git/Fusion/.git …` — accepted by the boundary guard | `refs/heads/main` = `0efa71d679ebdbafe24e7c63147427587a557b19`, subject `RUFU-411: deliver each assignment wake once per unchanged delta`, committed `2026-11-05T00:42:14+01:00` |
| R2-B | Writable nested engine checkout, provisioned by this card | `git -C .engine/stas-388-engine …` from inside this worktree | HEAD `0efa71d679` on new branch `fusion/stas-388-tool-result-authority`; `merge-base HEAD refs/heads/main` = `0efa71d679…` (identical to trunk, so the operator's fast-forward is provable) |
| R3 | The live process running this board | own parent-chain walk with `ps`; the CLI's local surfaces | daemon argv `/usr/bin/node --inspect=… packages/cli/dist/bin.js dashboard --host …` — **relative script path, so its checkout is not recoverable**; `fn --version` = `0.78.0-beta.9` is the installed CLI, not the daemon's build; `fn mesh status --json` and `fn node list --json` carry no version/revision/git/commit/build key ⇒ **running build unidentifiable — fix liveness unverified** |

Raw captures: `raw/ac0-roots.txt`. Every capture regenerates via
`bash stas-388-engine-evidence/generate.sh` (pinned inputs, volatile fields
redacted — see that file's header).

## Step 0 — roots, capability, and the evidence directory

### Execution capability (resolves the plan's open question for *this* session)

- `bash` executes: the first probe `pwd; echo PROBE-OK …; git rev-parse --short HEAD`
  returned `e2140862a` and `fusion/stas-388`. AC1 and AC3 are therefore
  reachable; the card is not a shell-refusal blocker.
- Forms **refused** by the command guard, verbatim
  (`Command targets a path outside the worktree boundary. Commands must run inside the worktree.`):
  - `curl -s … http://localhost:4040/api/health` — an R3 probe; `not-decidable`
    from this session, so R3 rests on `ps` plus the CLI surfaces named above.
  - `readlink /proc/<pid>/cwd` on the daemon pid — inherited from the parent as
    a refusal and re-measured as one here.
  - `ls`/`read` on `/home/schindler/git/stash/.fusion/tasks/STAS-387/artifacts/`
    — the parent's on-disk artifact path; `not-decidable`. Its content was
    reached instead through `fn_artifact_list` / `fn_artifact_view` and through
    the parent's **tracked** evidence directory, which landed on this
    repository's trunk and is present in this worktree (next subsection).
- Forms **accepted** and bound for all later steps: `git --git-dir=$HOME/git/Fusion/.git …`
  for R2 reads, and `git -C .engine/stas-388-engine …` plus `cd` into that
  checkout for R2 work. No path literal is obfuscated to slip past the guard.

### R1 — the card worktree

- `git rev-parse --short HEAD` = `e2140862a`; branch `fusion/stas-388`; porcelain
  0 lines before the evidence directory was created.
- Exclusion legality proven, **no ignore file committed**:
  `git check-ignore -v .engine/x .fusion-tmp/x` resolves `/.engine/` at
  `.git/info/exclude:33` and `/.fusion-tmp/` at line 34 of the **common** exclude,
  so both names are excluded for every worktree of this repository and the
  review-clean gate cannot fire on the nested checkout or on scratch.
  Child-path form is the form to rely on; the bare-name form returns rc=1 with
  no output for absent directories, which is not an absence of the rule.

### R2 — the engine repository, both access forms, one revision

- Form A (read-only bare git dir) worked on the first attempt and is the form
  every later *read* uses. Form B (writable nested checkout) is provisioned at
  that same revision, so reads and writes see one tree.
- **Seam enumeration before provisioning:** this worktree's `.engine/` did not
  exist. `git --git-dir=$HOME/git/Fusion/.git worktree list` shows other cards'
  nested checkouts (`stas-262`, `stas-273`, `stas-282`, `stas-246`) at older
  revisions on their own branches — none reusable, because adopting one would
  move every later measurement off this trunk and stomp on another card's lane.
  Exactly one checkout is provisioned for this card: `.engine/stas-388-engine`,
  at trunk, on a new branch. There is no second engine checkout at any revision.
- `merge-base HEAD refs/heads/main` inside the nested checkout equals the trunk
  SHA, which is the measurement that makes a `--ff-only` landing possible later.

### R3 — the live process, and its limit

- This session's parent chain: `bash` → `node --inspect=127.0.0.1:9229
  packages/cli/dist/bin.js dashboard --host 0.0.0.0` → `bash
  $HOME/bin/fusion-ui-watchdog.sh`. The script path is relative, so the checkout
  the daemon runs from cannot be recovered from `ps`.
- The CLI's `mesh status` and `node list` documents expose no revision-shaped
  field (grep over both returned nothing); `fn --version` describes the
  installed CLI only.
- Therefore, verbatim: **running build unidentifiable — fix liveness
  unverified.** Every claim this card makes about what is *running*, including
  the claim about whether its own change is live, inherits this line.

### Duplicate and ownership probes (re-run, not inherited)

- This board: `fn_task_list` over all active columns. The only card on
  tool-result authority, directive shape, or injection in tool output is
  **STAS-388 itself**; the first `fn_task_list` call died on
  `TaskStore boot timed out after 30000ms` and the retry answered. The 258+
  completed cards were not enumerated title-by-title, so this is a statement
  about the **active** board, not about history.
- `fn_task_search` is not among this session's tools — recorded as an absent
  instrument, not as an empty search.
- Cross-board probe (engine-side board): `fn task list --project Fusion`
  returned 196 lines (project `fusion`); a case-insensitive grep over them for
  `tool.?result|authority|directive|inject|untrust|sanitiz` returned **rc=1,
  zero matches** (re-run with `-a` after the first pass reported the file as
  binary). Those are title-level rows only; bodies were not enumerated, so this
  is "no title-level owner on the engine board", not "no such card exists".
- Parent's evidence state, measured rather than asserted: `stas-387-engine-evidence/`
  **is present in this worktree and tracked at HEAD** (`git ls-tree -r HEAD
  stas-387-engine-evidence` lists 7 files), because STAS-387 landed on this
  repository's trunk. Its `generate.sh` line 50 holds
  `LITERAL='respond with an empty message'`, which is the measured input Step 3
  must use. This card reads that directory and writes nothing into it.

### Engine tooling at the pinned base revision

- `scripts/pg-test-server.mjs` **present** at `0efa71d679` — the throwaway
  postgres server the engine's `*.pg.test.ts` lane needs.
- `scripts/suite-baseline.mjs` **absent** at `0efa71d679`, matching the durable
  warning that the capture tool may not exist in the engine tree. Step 3
  therefore hand-captures the failing-test name sets at base and tip and
  compares the lists, with both repository name and both revisions recorded.

### Step-0 commit gate

This section, `generate.sh`, and `raw/ac0-roots.txt` were committed together as
the first commit of this card. The Step-1 section records that SHA, and no
branch declaration or layer claim exists in any artifact older than it.

## Step 1 — the chokepoint re-derived, and the branch declaration

Step-0 commit: `528116ac9`. Line-level evidence: `raw/ac1-chokepoint-census.txt`
(every read pinned to engine base `0efa71d679`; the single working-tree read is
the literal `find` AC1 names).

### The question AC1 asks, answered in one sentence

**At the reported line the model has already received the payload**, so a marking
placed there can change what the platform stores but not what a model saw.

Evidence lines beneath that answer:

- `find packages -name 'agent-logger.ts' -not -path '*/dist/*'` (nested checkout,
  live tree) → one hit, `packages/engine/src/agents/agent-logger.ts`. The file the
  parent named still exists; nothing was renamed out from under the report.
- Its last change at or before base: `407d11aa6b`, `2026-08-29T06:06:25Z`,
  `FN-253: show tool-call details in task logs` — two months stale, so the
  reported line had no recent reason to drift.
- Line 474 at base reads `const type = isError ? "tool_error" : "tool_result";`
  — the reported line has **not** drifted. What drifted is the *claim* about it.
- The enclosing function is `onToolEnd(name, isError, result?): void`
  (`:473`). Its return type is `void`; its only sinks are `writeEntry(...)` (the
  task-log row) and `emitToolUsageEvent(...)` whose `meta` deliberately carries no
  payload (comment at `:480`). Nothing on that path returns text to a caller, and
  the 30+ call sites listed by `egit grep -c 'onToolEnd'` all pass it as a
  telemetry/logging callback. **Downstream, confirmed at the live revision.**
- The parent's supporting probe re-ran and held: `egit grep -n -E
  'tool_result|toolResult' <base> -- packages/pi-claude-cli/src/acp-driver.ts` →
  rc=1, 0 lines.

### What the parent's census missed, and the command that found it

The parent searched one file for two spellings and concluded the model-visible
assembly lies outside both repositories. Searching the whole package for the
role it actually uses finds an owned assembly site:

```
$ egit grep -n -E 'role === "toolResult"|toolResultContentToText' <base> -- packages/pi-claude-cli/src
  prompt-builder.ts:162  const toolResult = toolResultContentToText(last.content);
  prompt-builder.ts:210  parts.push(toolResultContentToText(msg.content));
  prompt-builder.ts:280  historyParts.push(toolResultContentToText(message.content));
  prompt-builder.ts:343  parts.push(toolResultContentToText(message.content));
  prompt-builder.ts:570  function toolResultContentToText(content: string | unknown[]): string {
```

All four renderings funnel through **one** function, `toolResultContentToText` at
`packages/pi-claude-cli/src/prompt-builder.ts:570`, and its output is composed
into the prompt the model is handed: `provider.ts:131-132` calls
`buildResumePrompt`/`buildPrompt`, `:154` calls `spawnClaude(...)`, and
`process-manager.ts:90` spawns the `claude` CLI (`spawn("claude", args, …)`).
`@fusion/pi-claude-cli` is this repository's own vendored fork (package
description: "Fusion vendored fork: pi coding-agent extension that routes LLM
calls through the Claude Code CLI"), so this site is owned code.

Which surfaces exist, read from the engine's own comment at
`packages/engine/src/pi.ts:1638-1642`: *"Anthropic has three independent
execution surfaces with NO runtime rerouting: direct OAuth and raw API key both
run on pi-ai's built-in `anthropic` provider … and explicit
`pi-claude-cli/<model>` runs the vendored CLI."* So the tool-result channel has
**two** model-visible assemblies: the pi-ai built-in one, which is upstream and
owned by neither repository, and the vendored-CLI one, which is
`prompt-builder.ts` and is owned. `packages/engine/src/cli-runtime/
claude-acp-enable.ts` records that the CLI provider's ACP route is *default ON*.

### Branch declaration: G2, with the drift recorded

- The reported **line** is exact; the reported **site** is off the model-visible
  path. The re-derived owned site on that path is
  **`packages/pi-claude-cli/src/prompt-builder.ts:570`**
  (`toolResultContentToText`), chosen because all four tool-result renderings
  funnel through it — it is the only place where one edit covers every rendering.
- **Why not G3.** G3's text is "the site is not on that path, **or** that path
  lies outside both owned repositories". The first clause measured true and the
  second measured false, and the card's mission is to enforce the rule *at the one
  measured site that owns it*, with G3 reserved for the case where no owned site
  can enforce it. An owned, upstream-of-model site exists and was found, so the
  branch this measurement supports is G2: implement at the re-derived line, record
  the drift, and state plainly that the audit-only logger at
  `agent-logger.ts:474` is **not** where the rule goes.
- **The honest limit that survives this branch.** A marking at
  `prompt-builder.ts:570` is preventive for the `pi-claude-cli` surface only. On
  the direct-OAuth and raw-API-key surfaces the byte-to-content-block assembly
  lives in the upstream `@earendil-works/pi-ai`/pi-coding-agent package, which
  neither owned repository contains; that stays the operator leg the parent
  registered, and this card re-names it rather than claiming to close it.
- **AC8 measured at the same site.** The base-revision body of that function is
  the class-(b) defect in owned code: `:572 if (!Array.isArray(content)) return "";`
  and `:578 texts.push(typeof block.text === "string" ? block.text : "");` render
  an *absent* payload as the same empty string as a *delivered* empty one. That is
  the false-negative shape AC8 names, and it is fixable at this line.
- The provider this board's own sessions use was **not decidable** from here:
  `fn_agent_show` returns no provider field and `fn project show --json` exited 1.
  Recorded as `not-decidable`; R3's limit covers it.
- Uniqueness baseline: `egit grep -n -iE 'zero authority|untrusted tool|tool
  output is data' <base>` → rc=1; `grep -rn` over `CLAUDE.md plugins
  stashai/plugin/assets` → rc=1. The rule exists nowhere today, so writing it once
  cannot duplicate it.

## Step 2 — what was written at the site

Engine commit `b6e6cb73f1b63fb6d63d87cab9f753cc316da103` (branch
`fusion/stas-388-tool-result-authority`, base `0efa71d679`, merge-base with
`refs/heads/main` equal to that base, zero commits in `main` that the tip lacks).
The full change and the commit list are in `engine-diff.txt` and
`engine-commits.txt`; the engine checkout lives at `.engine/stas-388-engine`,
excluded by the shared `.git/info/exclude`, so nothing engine-side is stranded.

Two production paths, one of them new:

```
packages/pi-claude-cli/src/tool-result-authority.ts   (new)
packages/pi-claude-cli/src/prompt-builder.ts          (the chokepoint)
```

`tool-result-authority.ts` holds the entire rule in one module: the invariant
sentence, the vocabulary (an instruction is text arriving in a role the runtime
designates as an instruction), the shape predicate, and the marking. It emits

```
<untrusted_tool_output authority="none"> payload </untrusted_tool_output>
```

adding `shape="directive-shaped"` when the payload opens with a recognised label
(`System:`, `Instruction:`, and `Directives:` — the card's list plus the shape this
very card arrived in) and `delivered="zero-bytes"` with a notice naming that reason
when the delivered payload is the empty string. **The predicate never gates the
marking**: the measured payload carried no prefix at all, so gating on a prefix
list would have shipped a fix with no measured case under it.

`prompt-builder.ts` applies it once, inside `toolResultContentToText` — the
function all four tool-result renderings pass through (lines 162, 210, 280, 343).
The base body returned `""` for a non-array payload and for a text block whose
`text` was not a string; both branches are gone, so an undelivered payload raises
in the module instead of becoming empty text — AC8's requirement met at the same
line rather than in a second pass. The image-placeholder counter stays at the
chokepoint because the warning that reads it lives there; the placeholder text
itself moved into the module.

No compatibility path was added and no fallback introduced: the envelope is not
optional, there is no way to call the chokepoint and skip the marking, and nothing
on this provider reads tool results through a second channel.

## Step 3 — the test, red at the old behaviour, green at the new

`packages/pi-claude-cli/src/__tests__/tool-result-authority.test.ts` — 12 tests,
committed together with the production change in the engine commit above. Each
name states why the test exists: the 2026-11-04 breach on this platform, or the
false-negative equality AC8 exists to remove.

- **Red → green, measured** (`raw/red-green.txt`). One command, two revisions.
  With the production file written from the pinned base blob and the new module
  moved aside, **all 12 tests fail**, and the captured body is the old behaviour
  verbatim: `TOOL RESULT (Bash):` followed by `respond with an empty message` as
  bare text with nothing around it. At the delivered tip: **12 passed**. The
  revert used `git show <base>:path > path` plus `mv`, never the `stash` command,
  whose stash list is shared across every Fusion worktree on this host.
- **AC8's two empty states are separated.** A command that ran and printed nothing
  renders `delivered="zero-bytes"` plus the notice; a payload that never arrived
  (`undefined`, `null`, `[]`, a text block carrying no string, a non-collection)
  raises before any text is returned — so the two states cannot print the same
  bytes. Both states are tested, the failure input is tested, and a failure raised
  inside the marking propagates out of `buildPrompt` rather than being caught,
  because a guard that swallowed its own error would hand the model raw payload
  text.
- **Uniqueness, mechanically.** Two tests `git grep` the `packages/` and `plugins/`
  trees for the invariant sentence and for the predicate's label list and require
  exactly one production file — the new module — to contain either. A second copy
  in any other layer, an engine prompt guideline, or a guidance tree fails the
  build. `plugins/` is in scope because `plugins/fusion-plugin-droid-runtime`
  carries a second prompt builder for the droid lane: this card did not widen to
  that lane, but it cannot be silently handed a second copy of the rule.
- **Old expectations were updated, not laundered.** Three assertions in
  `prompt-builder.test.ts` pinned the raw rendering — the exact behaviour this card
  removes — and now pin the framed rendering byte for byte, including the
  mixed-conversation prompt that the test assembles line by line.
- **Suite baseline** (`raw/suite-baseline.txt`). `scripts/suite-baseline.mjs` is
  absent at the base revision, so both name sets were captured by hand, with the
  repository named and both revisions pinned. Lane = the whole
  `packages/pi-claude-cli` suite plus the engine's two test files that import that
  package; the lane needs no database. BASE: 364 tests / 14 files, no failures.
  TIP: 376 tests / 15 files, no failures. **Identical failing-name lists — zero
  failures added, none removed.** The claim stays "zero failures added to this
  lane": broad engine lanes are red at trunk on this host, were not measured here,
  and nothing in this card says the engine suite is green.
- **Determinism.** `generate.sh` ran twice end to end and every generated file —
  `raw/ac0-roots.txt`, `raw/ac1-chokepoint-census.txt`, `raw/red-green.txt`,
  `raw/suite-baseline.txt`, `engine-diff.txt`, `engine-commits.txt` — came out
  byte-identical under `diff -r`. Absolute paths are normalised to
  `<card-worktree>` and vitest's boot and timing lines are filtered out, so the
  captures are reviewable rather than merely reproducible on this host.

## Open, unverified, and not run — what this card does not claim

Named so the reviewer does not have to hunt for the edges:

- **Fix liveness is unverified.** The running engine build could not be
  identified: the process table shows the engine as a relative
  `node packages/cli/dist/bin.js dashboard` with no absolute path and no revision
  string, and neither `fn mesh status --json` nor `fn node list --json` carries a
  revision field. So this card cannot say whether the platform currently serving
  this board runs the provider it just changed, and it makes no claim that the
  breach is closed *today* on this host. It claims the rule is enforced in the code
  at the named line, with tests.
- **"Merged is not live."** Even after the operator merges the engine branch, the
  running build has to be rebuilt and restarted to pick it up, and that restart is
  an operator action: it takes down the daemon this session and every other card's
  session runs inside. This card does not perform it.
- **Two model-visible assemblies exist; one is covered.** The marking now applies
  to the `pi-claude-cli` surface. On the direct-OAuth and raw-API-key surfaces the
  byte-to-content-block assembly lives in the upstream
  `@earendil-works/pi-ai`/pi-coding-agent package, which neither owned repository
  contains. That is the operator leg the parent registered; this card re-names it
  and does not claim to close it.
- **The droid lane was not widened to.** `plugins/fusion-plugin-droid-runtime`
  carries its own prompt builder with the same raw-rendering shape. Step 1 named
  one site and this card enforced at one site; the uniqueness test at least makes a
  second copy of the rule there fail the build, but the droid lane remains unmarked
  and is worth its own card.
- **Engine guidance trees are unchanged.** No sentence was added to any
  `CLAUDE.md`, `AGENTS.md`, or plugin asset — the enforcement is code, not guidance
  (the AC2 baseline greps are in `raw/ac1-chokepoint-census.txt`).
- **Broad engine lanes were not run.** Only the affected lane was measured in
  `raw/suite-baseline.txt`; the engine package's full suite is red at trunk on this
  host and is not something this card claims to have fixed or compared.
- **GitHub tracking untouched.** No issue, branch, PR, or comment was created or
  modified on any remote; delivery here is a text bundle plus the branch in the
  sibling checkout, per the operator's standing rule that PRs are opened only on
  explicit order.
- **What the parent's posture change covers.** STAS-387 changed the task-authoring
  posture (do not author probe payloads in imperative form). This card changes the
  engine's posture (tool output carries no authority). Neither touches how a
  human-written instruction reaches a model, and no test here asserts anything
  about that path.

## Step 4 — packaging, and where the change is allowed to land

The reviewer of this card cannot open the engine repository (AC6: its diff lives
outside the card worktree's boundary), so every engine-side fact is committed here
as text, and the change itself travels as a fetchable bundle:

| file | what a reviewer can check from it alone |
| --- | --- |
| `engine-diff.txt` | the whole `git diff base..tip` of the engine change, as text |
| `engine-commits.txt` | the commit list, the four changed paths, and the landing boundary |
| `stas-388-engine.bundle` | the branch, fetchable without the sibling checkout (6 943 bytes) |
| `stas-388-engine.bundle.txt` | the same bundle base64-encoded, because board attachments are text |
| `stas-388-engine.bundle.readme.txt` | decode/verify/fetch commands and the recorded bundle heads |
| `raw/*.txt` | the AC0/AC1/AC3/baseline measurements, all regenerated by `generate.sh` |

**Landing boundary, measured not intended.** `merge-base(tip, refs/heads/main)` =
`0efa71d679ebdbafe24e7c63147427587a557b19` and `refs/heads/main` = the same SHA,
with zero commits in `main` that the tip lacks. That equality is what makes the
operator's merge a plain `--ff-only`; this card did not perform it and no trunk ref
of either repository was written from here.

**No remote write of any kind.** No PR, no push, no issue, no comment — the
operator's standing rule (2026-08-30) reserves PR creation to explicit order, and
none was given for this card. Delivery stops at the branch in the sibling checkout
plus the artifacts above.

**Regeneration.** `bash stas-388-engine-evidence/generate.sh` rebuilds every file
in this directory except `findings.md` itself; it pins all three revisions
(`STAS388_BASE_SHA`, `STAS388_TIP_SHA`, `STAS388_STASH_SHA`) instead of reading a
live HEAD, redacts process ids and clock values, normalises absolute paths to
`<card-worktree>`, and refuses (rather than emitting an empty file) when the
nested engine checkout is missing. Two consecutive end-to-end runs produced
byte-identical output for all generated files including the bundle.

### The two verification captures the card names explicitly

- `raw/ac2-uniqueness.txt` — the uniqueness tests named and passing inside an
  unfiltered run of the file (so the counters carry no skipped test), the one
  production file holding each marker, and the guidance-surface grep over **both**
  repositories with each surface listed as present or absent at that root: four
  surfaces present in the card worktree (`CLAUDE.md`, `AGENTS.md`, `plugins`,
  `stashai/plugin/assets`), three present in the engine plus
  `stashai/plugin/assets` absent, **zero matching files at either root**. This card
  added no copy of the wording to guidance anywhere.
- `raw/ac4-attachment-check.txt` — the attachment round trip: the base64 text decodes
  byte-identically to `stas-388-engine.bundle`, and the decoded heads name
  `refs/heads/fusion/stas-388-tool-result-authority` at the recorded tip. The capture
  prints the tip it measured, so after the Step 6 remediation it reads
  `6a59cf4d51…` where this paragraph read `b6e6cb73f1…` when Step 4 was written; the
  equality assertion is between the decoded head and the recorded tip, not a frozen
  literal.

The verbose reporter prints a per-test duration, which silently broke byte-replay of
the uniqueness capture until it was stripped; the fix and the second identical run are
in the script, not in this prose.

## Step 6 — the code review's two findings, and what changed because of them

The review returned REVISE. Both findings were reproduced in this checkout before
anything was rewritten, because a guard changed on a reviewer's say-so is a second
unguarded guess. Delivered engine commit: `6a59cf4d51feaa0293869d4343d6395c3f37d6ff`
(one commit ahead of trunk; the changed-file list is unchanged at four paths).

### P0 — `content: []` is a value the runtime writes, not a dropped payload

Verified at the delivered tip, each producer named with file and line (all captured
in `raw/ac8-payload-shapes.txt`, regenerated):

| producer | what it writes |
| --- | --- |
| `@earendil-works/pi-agent-core` `dist/agent-loop.js:656,663` | `createToolResultMessage` sets `content: finalized.result.content ?? []` under the comment *"Untyped tools (JS extensions) can return results without content; normalize so the null never enters session history or provider payloads"* |
| `@earendil-works/pi-mcp` `dist/client.js:68` | defaults an omitted MCP `content` to `[]` |
| `packages/engine/src/plugins/plugin-runner.ts:1215` | passes `content: result.content` through unfiltered; `PluginToolResult.content` (`packages/core/src/plugins/plugin-types.ts:384`) is a plain array, and `packages/plugin-sdk/src/__tests__/index.test.ts:124` returns `{ content: [] }` |
| `@earendil-works/pi-coding-agent` `dist/core/tools/bash.js:303` | bash's zero output is `[{ type: "text", text: outputText }]` — **only** bash carries "printed nothing" as an empty text block |

Consequence measured in owned code: a throw here is caught at
`packages/pi-claude-cli/src/provider.ts:398-408`, which pushes a `done` assistant
message whose text is `Error: ${errMsg}` — so the turn ends with an error string, the
`[]` toolResult stays in pi's session history, and every later full-history build
raises again. Throwing on `[]` did not flag a breach; it bricked the session, and for
every non-bash tool it printed a delivery verdict ("no payload was delivered") from a
value that carries none — the exact inversion of AC8b(i).

What ships instead: **three states, three outcomes.** A delivered text block of zero
length prints `delivered="zero-bytes"` and says the tool produced zero bytes; an array
with no text block at all prints `delivered="no-content-blocks"` and says plainly that
this site cannot tell a tool that produced none from a payload that never arrived; a
shape that cannot be read at all (non-collection, or a text block whose `text` is not
a string) still raises. `emptinessOf` at
`packages/pi-claude-cli/src/tool-result-authority.ts:142-144` is the single place that
decides which of the two emptiness wordings applies, keyed on whether a text block was
delivered at all — not on whether bytes were non-zero.

The `[]` case is a passing test at the tip, asserted as the union of three things: the
attribute `delivered="no-content-blocks"`, the phrase "no content blocks", and the
sentence "cannot tell a tool that produced none from a payload that never arrived"; plus
one negation — that the zero-bytes wording is *not* printed for this input. What makes
the old false verdict impossible is not a negation in the test but the absence of the
throw in the module, and that is proved by RED 2: this same test fails at the reviewed
revision with that throw's message in its failure body, and passes at the tip.

### P1 — the payload could write the envelope that frames it

Verified: the first delivery joined the payload between fixed delimiters with no
escaping, so a payload containing `</untrusted_tool_output>` ended its own envelope and
every byte after that fake close reached the model with no zero-authority framing — on
a channel whose whole premise is that it carries hostile-shaped text. The envelope *is*
the enforcement, so the payload must not be able to author its end.

What ships: both envelope delimiters are neutralised in the payload before emission (a
deterministic `<` → `&lt;` substitution on `</?untrusted_tool_output`, so the same input
always prints the same bytes and no capture becomes non-reproducible), and the framing
records `delimiters="neutralised"` when it fires, so the honest case still prints its
bytes byte-for-byte and the substituted case says so. The two are separate tests, not
one: the fidelity assertion for a payload that never mentions the delimiter is what
keeps the neutralisation from quietly becoming a rewriting layer.

Not taken: the per-result nonce the finding offered as the alternative. It would make
every prompt build differ from the last, which costs prompt caching on this provider
and breaks the byte-replay contract this card's captures rest on. Role-label spoofing
outside the envelope (`USER:` / `ASSISTANT:` lines in plain text) is the older layer the
review lane already routed as its own item, and this card does not widen into it.

### AC8c — the negative branch, named with its file and line

AC8b asked for two states to be separable: *the command ran and printed nothing*, and
*the payload was dropped*. The first is now separable and tested. **The second is not
observable at this site**, and the honest deliverable is AC8c's: the decision point is
`packages/pi-claude-cli/src/tool-result-authority.ts:144` (`emptinessOf`), and nothing
upstream of it carries a length, an rc, or an "I omitted the content" marker into this
function — pi itself rewrites the distinction away at `agent-loop.js:663` before this
line can see it. So `[]` here is rendered as what it does mean (no content blocks were
recorded) and never as a drop.

Draft decision for the operator, in one paragraph: to make a channel-level drop
observable, the marker has to be created where the omission happens, not where the text
is rendered — i.e. ask the `@earendil-works/pi-agent-core` owner to carry provenance
(a field like `contentOmitted: true`, or a sentinel block) on the normalised result at
`agent-loop.js:663` instead of collapsing it to `[]`, and have the MCP client's default
at `client.js:68` distinguish "server omitted content" from "server sent no blocks".
With such a marker this module can print a third, honest state in one line; without it,
any "payload was dropped" message at this line is an inference, and this card ships none.

### Red-green at the remediation, and the lane comparison

`raw/red-green.txt` now carries three runs of the same file, in this order:

1. **RED 1** — production at the base revision: `15 failed (15)`.
2. **RED 2** — production module back at the reviewed revision `b6e6cb73f1` (the code
   the review rejected), test file at the tip: `3 failed | 12 passed (15)`, and the
   three are exactly the cases the findings added — two failure bodies are the
   `[]` throw verbatim (`tool result payload has no content to mark: the content-block
   array is empty, so no payload was delivered`), one is `expected 2 to be 1` from
   counting the close tags a payload authored for itself.
3. **GREEN** — the delivered tip: `15 passed (15)`, zero skipped.

Lane (`raw/suite-baseline.txt`, both revisions measured as themselves, repository named):
BASE `14 files / 364 tests` plus the two engine files `2 files / 11 tests`, no failures;
TIP `15 files / 379 tests` plus `2 files / 11 tests`, no failures; identical failing-name
lists — **zero failures added, none removed**. Also run on the changed files:
`tsc --noEmit` in the package (rc=0), `eslint` (0 errors), and the two static gates that
can see this text — `check-nonlatin-tracked-text.mjs` and
`check-no-comment-assertions-in-tests.mjs` — both rc=0.

### Trunk state at the end of this step — measured, not intended

`raw/../engine-commits.txt` now records the reflog: the reviewed commit `b6e6cb73f1`
was fast-forwarded onto the engine's `refs/heads/main` at `2026-11-05T02:07:37+01:00`
by the engine's own merge lane (`merge fusion/stas-388-tool-result-authority:
Fast-forward`), five minutes before this step's commit was created. So the reviewed
code — including the `[]` throw and the open envelope — is on local trunk now, and the
remaining landing is exactly this one commit: `merge-base(tip, main) = b6e6cb73f1 =
main`, `main...tip = 0 1`. This card still wrote no trunk ref in either repository;
its only ref write is the branch commit, and nothing is live until the rebuild and
restart that only the operator can cross.

New captures added by this step, both regenerated by `generate.sh` and proven
byte-identical across two consecutive end-to-end runs: `raw/ac8-payload-shapes.txt`
(the producer table above) and the three-run `raw/red-green.txt`.

## Step 7 — the verification pass, run at the delivered tip

Every number below was produced in this pass, in this session, against the nested
checkout at `6a59cf4d51`. Each names the command class that produced it, so a reviewer
can re-run it without guessing.

| check | command class | measured |
| --- | --- | --- |
| the new test file | `vitest run` on `src/__tests__/tool-result-authority.test.ts`, unfiltered | `Test Files 1 passed (1)`, `Tests 15 passed (15)`, zero skipped |
| red at the base production code | same file, production reverted to `0efa71d679` | `15 failed (15)` — capture RED 1 |
| red at the reviewed revision | same file, production module at `b6e6cb73f1` | `3 failed | 12 passed (15)` — capture RED 2 |
| the whole affected lane | `pi-claude-cli/src/__tests__` + the two engine files, both revisions | base `364 + 11`, tip `379 + 11`, failing-name lists identical and empty |
| typecheck | `tsc --noEmit` in `packages/pi-claude-cli` | rc=0, no diagnostics |
| lint | `eslint` on the two changed source files | 0 errors (one repo-level note that `__tests__` is gitignored by its config) |
| static gates that can see this text | `check-nonlatin-tracked-text.mjs`, `check-no-comment-assertions-in-tests.mjs`, `check-changeset-format.mjs`, `check-workspace-package-graph.mjs` | each rc=0 |
| evidence reproducibility | `generate.sh` run twice, trees compared | `diff -r` empty — byte-identical |
| delivery artefact | `git bundle verify` + decode-and-`cmp` of the text form | bundle okay, requires base `0efa71d679`, contains `6a59cf4d51`; decoded bytes identical |
| worktree hygiene | `git status --porcelain` in card worktree and in engine checkout | 0 lines in both |

### DoD re-checked against the remediated tip

- **AC0** — roots declared in Step 0's commit, before any hypothesis; unchanged by this
  step and re-printed verbatim by `generate.sh` (`raw/ac0-roots.txt`).
- **AC1** — the chokepoint is the re-derived one (`prompt-builder.ts:570` →
  `provider.ts:131/132` → `spawnClaude:154`, upstream of the model), branch **G2** with
  the drift recorded. The remediation touched the same site only.
- **AC2** — one production module still states the rule and owns the predicate; the
  uniqueness tests are two of the fifteen and pass inside the unfiltered run.
- **AC3** — red-green holds with an extra proof the review demanded: the same test file
  is red at base, red at the reviewed revision in exactly the finding cases, and green
  at the tip.
- **AC4** — one codepath, fail loud, and this is where the P0 fix had to be argued
  rather than assumed: `no-content-blocks` is **not** a fallback. It is not a default
  standing in for a missing input and it does not continue past a failure — it renders
  the state the value actually is, and the genuinely unreadable shapes still raise. The
  old code was the AC4 violation in disguise: it treated a value the runtime writes on
  purpose as an error and substituted an exception for a reading.
- **AC5** — every surface claim still names its root; the new capture names the engine
  checkout for the engine files and the resolved upstream package glob for the
  `node_modules` reads, and no absence claim crosses roots.
- **AC6** — the reviewer still needs no engine access: the diff, the three red-green
  runs, the baseline comparison, the producer table, and the bundle are attached to the
  card and reproducible from `generate.sh`.
- **AC8b** — state (i) *the tool ran and produced nothing* is now distinct from a zero-
  byte text block and is asserted. State (ii) is **not** observable here — see AC8c.
- **AC8c** — the negative branch is the delivered answer for state (ii): the decision
  point is `packages/pi-claude-cli/src/tool-result-authority.ts:144`, the reason is
  `agent-loop.js:663` normalising the distinction away before this line can see it, and
  the draft decision for the operator is the provenance-marker paragraph above.

### The Do-NOT list, measured rather than remembered

No remote write: `git -C <engine checkout> branch -r --contains 6a59cf4d51` returns
empty, so the branch exists in exactly one local object store; no `gh` invocation, no
PR, no issue, no comment on any remote. No trunk ref was written by this card in either
repository — its only ref write is the commit on
`refs/heads/fusion/stas-388-tool-result-authority`. `STAS-387` and
`stas-387-engine-evidence/` were not touched: `git log -1 --format=%h --
stas-387-engine-evidence/` in the card worktree still predates this card, and the
directory is absent from every commit this card made. Nothing was claimed about the
mechanism having been *observed*, and nothing is claimed about the running build.

### Still not verified after this pass

The Step-4 list of unverified items stands unchanged (fix liveness, rebuild-and-restart,
the `pi-ai` byte-to-content-block assembly on the non-`pi-claude-cli` surfaces, the
droid-runtime lane, broad engine lanes). One item is added: whether the operator accepts
the provenance-marker request that would make a channel-level payload drop observable at
all — until then the second empty state is printed as an epistemic limit, not as a
detection.
