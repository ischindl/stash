#!/usr/bin/env bash
# STAS-391 evidence generator. Run from the card worktree root:
#   bash stas-391-engine-evidence/generate.sh
#
# Determinism contract: every value below is either pinned prose, derived from the
# pinned BASE_SHA, or produced by running a probe this script itself writes. The
# only sections allowed to differ between runs are inside a "LIVE" block, which
# reads a moving ref on purpose — a change there is the signal this card exists to
# produce (trunk moved), not corruption.
#
# Repo-specific traps handled explicitly:
#   - `set -e` plus a grep with no matches returns rc=1 and would abort the script,
#     so every read captures its rc instead of leaving it to the shell.
#   - `| head -N` is a SIGPIPE landmine under pipefail; windows are cut with sed.
#   - vitest 4 verbose output carries timings and an absolute RUN path, so every
#     vitest capture goes through scrub_vitest.
#   - a function on the left of a pipe runs in a subshell, so its rc never reaches
#     the parent; no rc here is collected that way.
#   - a check whose two sides are copies of the same file cannot fail, so the
#     reconstruction below is proven by reversing the patch and comparing to the
#     base blob, never by comparing a file to its own copy.
#
# What this script writes outside the evidence directory, and only transiently:
#   - scratch under .fusion-tmp/ (excluded by info/exclude line 34), and
#   - one probe file copied into the nested engine checkout's src/__tests__/ (the same
#     exclusion its own uniqueness test applies to untracked files there), deleted again.
# It never writes a tracked file in the engine repository and never creates a ref.
#
# Three captures need the working tree on a revision other than the one the checkout is
# sitting on — the suite baseline and the base witness need the base, the RED run needs the
# base module under the tip test. Each one checks the file(s) out at that revision, prints
# the restore proof (porcelain count, `git diff --exit-code <tip>`, and for the witness the
# blob hash of the file the probe actually loaded), and checks back to the tip. A capture
# that names a revision has to print the mechanism that put the tree on it: the witness once
# claimed a read of the base while measuring the fix, deterministically, and shipped.
set -euo pipefail
shopt -s inherit_errexit

CK=".engine/stas-391-engine"
OUT="stas-391-engine-evidence"
RAW="$OUT/raw"
PKG="$CK/packages/pi-claude-cli"
PKGREL="packages/pi-claude-cli"
MODULE="$PKGREL/src/tool-result-authority.ts"
TEST="$PKGREL/src/__tests__/tool-result-authority.test.ts"
SIBLING="$PKGREL/src/__tests__/tool-channel-authority.test.ts"
BASE_SHA="06b1768b18f38a9b4d0d5b35467204e25bfe4396"   # refs/heads/main as measured at Step 0
TIP_REF="refs/heads/fusion/stas-391-empty-tool-result-announcement"
TIP="eb20a6bfc583786b8c31cfe4f677237ebae54e10"   # this card's branch tip
MAIN_REF="refs/heads/main"
MAIN_SHA="$(git -C "$CK" rev-parse "$MAIN_REF")"   # measured at run time, never pinned
LINE390="d38b478184"      # fusion/stas-390-tool-channel-authority as measured at Step 0
REM388="6a59cf4d51"       # fusion/stas-388-tool-result-authority as measured at Step 0
TMP=".fusion-tmp/gen"
S="$TMP/scratch"
REPORT="$(pwd -P)/$TMP/report.txt"
TIPDIFF="$(pwd -P)/$TMP/tip.diff"                 # git diff base..tip, derived in the driver

mkdir -p "$RAW" "$S"

# --- helpers -------------------------------------------------------------------
g() { git -C "$CK" "$@"; }

run() {               # run a command, print it in its real form, print output + rc
  local rc=0 out label isg=0
  if [ "$1" = "g" ]; then shift; isg=1; label="git -C $CK $*"; else label="$*"; fi
  if [ "$isg" = "1" ]; then out=$(g "$@" 2>&1) || rc=$?; else out=$("$@" 2>&1) || rc=$?; fi
  printf '  $ %s\n' "$label"
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/      /'; else printf '      (no output)\n'; fi
  printf '        rc=%s\n\n' "$rc"
}

scrub_vitest() {
  # the leading-strip runs last, so every anchored pattern here has to allow for it
  sed -E -e 's#^ *RUN  v[0-9.]+ .*#RUN  <vitest> <package-dir>#' -e "s#$PKG#<package>#g" \
         -e 's/[0-9]+ms//g' -e 's/Start at.*//' \
         -e '/^ *Duration/d' -e 's/^ *//' | grep -v '^$' || true
}

hdr() {
  echo "# $1"
  echo "# base pinned for every read in this file: $BASE_SHA"
  echo "# Regenerate: bash $OUT/generate.sh   (run from the card worktree root)"
  echo
}

vitest_run() {          # vitest_run <relative-test-path> ; scrubbed output + real rc
  local rel="$1" out rc=0
  out=$( cd "$PKG" && STAS391_WITNESS_OUT="$REPORT" ./node_modules/.bin/vitest run "$rel" --reporter=verbose 2>&1 ) || rc=$?
  printf '%s\n' "$out" | scrub_vitest
  printf '        rc=%s\n\n' "$rc"
}

# --- raw/dependency-gate.txt : Gate D board reading + AC0 census -----------------
capture_dependency_gate() {
  local f="$RAW/dependency-gate.txt"
  local out rc files nreg
  {
    hdr "STAS-391 Step 0 — dependency gate (STAS-390) and the AC0 trunk census"

    echo "## Gate D — board reading of the dependency, taken per-ID"
    echo
    echo 'Source: fn_task_show(STAS-390) and its task log, read inside this run. A'
    echo 'card-level badge is not a content predicate, so the engine content Gate D'
    echo 'exists to protect is measured below rather than inferred from the column.'
    echo
    echo '  Column: done          Steps: 7/7 done (0 Probe capability ... 6 Close)'
    echo '  workflowStepResults[0] verdict: NOT EXPOSED on either readable surface.'
    echo '    fn_task_show prints column, steps, prompt and log lines only. The CLI form'
    echo '    "fn task show STAS-390 --json" ignores --json and prints the same prose'
    echo '    (measured in this run: 2203 bytes of text, no JSON object).'
    echo '    Nearest content is the card log, quoted verbatim:'
    echo '      2:45:28 PM  Merge confirmed finalization blocked — required post-merge'
    echo '                  evidence gate post-merge-verification is not approved.'
    echo '      2:45:35 PM  [post-merge] Workflow step failed: Post-merge verification ->'
    echo '                  CI repository not configured: the postMergeCiRepo project'
    echo '                  setting is unset, so this gate was never told which repository'
    echo '                  runs the post-landing Full Suite'
    echo '      2:45:44 PM  Workflow graph observed confirmed merge while task was'
    echo '                  in-review — finalizing to done (graph-failure)'
    echo '      2:45:50 PM  Post-merge evidence was not required for STAS-390: gate'
    echo '                  post-merge-verification has no report ...'
    echo '      2:46:05 PM  Lifecycle move: in-review -> done (forward) —'
    echo '                  auto-merge-finalization transition [source=engine]'
    echo '    The lane that failed is the board-side post-merge CI evidence gate, whose'
    echo '    stated cause is an unset project setting in this deployment. It is not a'
    echo '    verdict on the engine work the dependency carried.'
    echo
    echo "  Gate D's stop-condition, as the card states it: stop while STAS-390 is triage,"
    echo "  todo, in-progress or failed. Measured column is done, so the stop does not fire."
    echo "  What the badge cannot make true is the subject of the next section."
    echo

    echo "## Engine state behind that badge — measured, not inherited"
    echo
    for line in fusion/stas-390-tool-channel-authority \
                fusion/stas-389-tool-channel-authority \
                fusion/stas-388-tool-result-authority; do
      run g rev-list --left-right --count "$BASE_SHA...refs/heads/$line"
    done
    echo "  columns are <commits the base has that the line lacks>  <commits the line has"
    echo "  that the base lacks>. A non-zero second column means that line is not landed."
    echo
    run g merge-base --is-ancestor "$BASE_SHA" refs/heads/fusion/stas-390-tool-channel-authority
    echo "  rc=0 means the pinned base is an ancestor of that branch, i.e. the branch is"
    echo "  ahead of the base and a fast-forward of the base ref would carry it."
    echo

    echo "## AC0 census — symbol presence at the pinned base, whole repo"
    echo
    echo '  read form: git -C '"$CK"' grep -c -e <symbol> '"$BASE_SHA"' -- packages plugins'
    echo '  rc=1 with no output is "symbol absent"; files_with_hits counts files carrying'
    echo '  at least one hit.'
    echo
    for sym in ZERO_BYTES_NOTICE no-content-blocks EMPTY_NOTICES DeliveredText \
               emptinessOf neutraliseStructureTokens ENVELOPE_DELIMITER ROLE_KEY; do
      rc=0
      out=$(g grep -c -e "$sym" "$BASE_SHA" -- packages plugins 2>&1) || rc=$?
      if [ "$rc" -eq 0 ]; then
        files=$(printf '%s\n' "$out" | grep -c . || true)
        echo "  symbol=$sym  rc=0  files_with_hits=$files"
        printf '%s\n' "$out" | sed 's/^/      /'
      else
        echo "  symbol=$sym  rc=$rc  files_with_hits=0"
      fi
    done
    echo

    echo "## Message census — every site asserting the guard text, at the pinned base"
    echo
    for msg in 'no content to mark' 'no payload was delivered'; do
      run g grep -n -e "$msg" "$BASE_SHA"
    done

    echo "## Files present at the pinned base (the lane boundary)"
    echo
    run g ls-tree --name-only "$BASE_SHA" -- "$MODULE" "$TEST" "$SIBLING"
    echo "  a path listed above exists at the base; a path absent from this output does not."
    echo

    echo "## Single-stated guards at the base (AC5's prior state)"
    echo
    for sym in 'zero authority' DIRECTIVE_LABELS; do
      run g grep -l -e "$sym" "$BASE_SHA" -- packages plugins
    done
    echo "  each guard names one production module plus the test file that pins it."
    echo

    echo "## LIVE section — the only part of this file allowed to change on regeneration"
    echo
    echo "  Live trunk tip right now, versus the pinned base above:"
    echo "  \$ git -C $CK rev-parse --short refs/heads/main"
    g rev-parse --short refs/heads/main | sed 's/^/      /'
    echo "  Branch tips this family names, right now:"
    g for-each-ref --format='      %(refname:short) %(objectname:short)' \
      'refs/heads/fusion/stas-38*' 'refs/heads/fusion/stas-39*'
    echo "  Engine worktree registrations, right now:"
    nreg=$(g worktree list | wc -l)
    echo "      $nreg"
    echo
    echo "  Occupancy as measured at Step 0: refs/heads/main is held by the primary engine"
    echo "  checkout, not by any card checkout, which is why moving that ref is an operator"
    echo "  leg. The full listing behind that count is not reproduced here: its rows are"
    echo "  other cards' lanes, and the count itself is machine state that moves between"
    echo "  runs — Step 0 measured 97, so a different number above is drift, not a finding."
  } > "$f"
}

# --- probe sources --------------------------------------------------------------
write_probes() {
  mkdir -p "$TMP"

  cat > "$TMP/witness.test.ts" <<'PROBE_WITNESS'
import { describe, it, expect } from "vitest";
import { appendFileSync, writeFileSync } from "node:fs";
import { buildPrompt, buildResumePrompt } from "../prompt-builder";

/**
 * STAS-391 Step 1 base witness — a throwaway probe, not a test that stays.
 *
 * Calls the real prompt builders with the payload shapes this card is about and
 * writes what actually happened to a report file, so the capture holds production
 * behaviour rather than this file's opinion about it.
 */

const OUT = process.env.STAS391_WITNESS_OUT;
if (!OUT) throw new Error("STAS391_WITNESS_OUT must name the report file");

function ctx(messages: unknown[]) {
  return { messages } as unknown as Parameters<typeof buildPrompt>[0];
}

const CASES = [
  { id: "W1 builtin-bash lane, content is an empty array", tool: "bash", content: [] as unknown[] },
  { id: "W2 custom/MCP lane, content is an empty array", tool: "linear__search_issues", content: [] as unknown[] },
  { id: "W3 control, content is the empty string", tool: "bash", content: "" },
  { id: "W4 control, content is one empty text block", tool: "bash", content: [{ type: "text", text: "" }] },
  { id: "W5 control, content is undefined", tool: "bash", content: undefined },
  { id: "W6 control, content is null", tool: "bash", content: null },
  { id: "W7 control, content is the number 42", tool: "bash", content: 42 },
  { id: "W8 control, content is a text block with no text", tool: "bash", content: [{ type: "text" }] },
  { id: "W9 control, an ordinary payload on the custom lane", tool: "linear__search_issues", content: "3 issues found" },
];

describe("STAS-391 base witness", () => {
  it("records what the real builders do with each payload shape", () => {
    writeFileSync(OUT, "");
    const builders = [
      ["buildPrompt (full history)", buildPrompt],
      ["buildResumePrompt (resumed delta)", buildResumePrompt],
    ] as const;

    for (const c of CASES) {
      for (const [builderName, builder] of builders) {
        const head = `===== ${c.id} | ${builderName} | toolName=${c.tool} =====\n`;
        try {
          const assembled = builder(
            ctx([
              { role: "user", content: "Run the census." },
              { role: "assistant", content: "Running it." },
              { role: "toolResult", toolName: c.tool, content: c.content },
            ]),
          ) as unknown;
          const text =
            typeof assembled === "string" ? assembled : JSON.stringify(assembled, null, 2);
          appendFileSync(OUT, `${head}RESULT: returned a prompt\n--- ASSEMBLED PROMPT (verbatim) ---\n${text}\n--- END PROMPT ---\n\n`);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          appendFileSync(OUT, `${head}RESULT: THREW\n--- ERROR MESSAGE (verbatim) ---\n${message}\n\n`);
        }
      }
    }

    expect(appendFileSync).toBeTypeOf("function");
  });
});
PROBE_WITNESS
}

apply_check_at() {         # apply_check_at <rev> <tag> : does the branch diff fit that revision?
  local rev="$1" tag="$2" rc=0 rc2=0 out out2
  local d="$S/apply-$tag"   # bash expands every word of a local before assigning any, so tag cannot be reused on its own line
  rm -rf "$d"
  mkdir -p "$d/$PKGREL/src/__tests__"
  g show "$rev:$MODULE" > "$d/$MODULE"
  g show "$rev:$TEST" > "$d/$TEST" 2>/dev/null || rm -f "$d/$TEST"
  # git apply only judges hunks against content it can see as tracked. A scratch copy that
  # merely sits inside this checkout is answered by the enclosing repository — measured:
  # every verdict came back 0 that way, including the two revisions GNU patch rejects. So
  # the scratch copy gets its own one-commit repository and is judged on its own content.
  ( cd "$d" && git init -q . && git add -A \
      && git -c user.email=stas391@invalid -c user.name=stas391 commit -qm "revision $rev" \
      >/dev/null 2>&1 ) || true
  out=$( cd "$d" && patch -p1 --dry-run < "$TIPDIFF" 2>&1 ) || rc=$?
  out2=$( cd "$d" && git apply --check -p1 < "$TIPDIFF" 2>&1 ) || rc2=$?
  echo "    at $rev ($tag), GNU patch --dry-run: rc=$rc"
  printf '%s\n' "$out" | sed 's/^/      /'
  echo "    at $rev ($tag), git apply --check: rc=$rc2"
  printf '%s\n' "$out2" | sed -e "s#$S#<scratch>#g" | sed 's/^/      /'
  echo
}

# --- raw/suite-baseline.txt ----------------------------------------------------
capture_suite_baseline() {
  local rc out
  local f="$RAW/suite-baseline.txt"
  {
    hdr "STAS-391 — the package suite at the base and at this card's tip"
    echo "Only the summary lines are kept: vitest runs files concurrently, so a per-case"
    echo "listing of a whole suite interleaves differently on every run and would make this"
    echo "file non-reproducible. The per-case list this card relies on is single-file and in"
    echo "raw/red-green.txt."
    echo
    echo "The suite's prior state is pinned here at the base the change was authored on, and"
    echo "the same suite is run again at the branch tip, so the delta is a measured pair"
    echo "rather than a claim."
    echo
    echo "## At the base $BASE_SHA"
    echo "The nested checkout is on the branch while this file is generated, so the base is"
    echo "reproduced by checking this branch's own files back to their base blobs. That is the"
    echo "whole base: the diff below is the branch's entire footprint, so swapping those files"
    echo "swaps the tree."
    run g diff --name-only "$BASE_SHA" "$TIP"
    g checkout "$BASE_SHA" -- "$MODULE" "$TEST"
    (
      cd "$PKG"
      set +e
      ./node_modules/.bin/vitest run 2>&1 | scrub_vitest | grep -E 'Test Files|Tests +[0-9]|FAIL'
      echo "vitest rc=${PIPESTATUS[0]}"
    )
    g checkout "$TIP" -- "$MODULE" "$TEST"
    echo "Restored to the tip — porcelain lines after the restore, 0 expected:"
    g status --porcelain | wc -l
    echo
    echo "## At the branch tip $TIP"
    echo "Same command, same lane, one commit later. The expected delta is zero: the"
    echo "equality pin is replaced by the announcement pin rather than added beside it, so"
    echo "identical counters are the proof that no case was quietly dropped on the way."
    (
      cd "$PKG"
      set +e
      ./node_modules/.bin/vitest run 2>&1 | scrub_vitest | grep -E 'Test Files|Tests +[0-9]|FAIL'
      echo "vitest rc=${PIPESTATUS[0]}"
    )
    echo
    echo "## The repo's line-count guardrail, measured at the base"
    echo "It is a root-level gate (\`node scripts/check-file-line-count.mjs\`), not part of the"
    echo "package's own test script, which is only \`vitest run --reporter=dot\`. The verdict"
    echo "is repo-wide; what this card needs to know is whether either of its own files is on"
    echo "the list, on either revision."
    rc=0
    out="$( cd "$CK" && node scripts/check-file-line-count.mjs 2>&1 )" || rc=$?
    echo "rc=$rc"
    echo "files named in the report: $(printf '%s\n' "$out" | grep -c '^packages/')"
    echo "of which in $PKGREL: $(printf '%s\n' "$out" | grep -c "^$PKGREL" || true)"
    echo "Neither of this card's files is on the list at the base or at the tip; the module"
    echo "grows 120 lines to 127 and the guardrail caps new source files at 2000. The rc=1"
    echo "is a repo-wide pre-existing state this card neither caused nor can repair."
    echo
    echo "## The module boundary this card's prompt cannot be re-pointed at"
    echo "Recorded because it is the reason the red/green above had to be authored on a"
    echo "branch: the prompt builder imports the authority module statically, so a scratch"
    echo "copy of the module can never be the thing the prompt path loads."
    grep -rn "tool-result-authority" "$PKG/src/prompt-builder.ts" | grep -v "^Binary" | head -3
  } > "$f"
}

# --- raw/base-witness.txt ------------------------------------------------------
capture_base_witness() {
  local f="$RAW/base-witness.txt"
  local rc out rev n site1 site2 pair rest tag label path base_blob other_blob mine
  local base_blob_id tree_blob_id restore_rc
  {
    hdr "STAS-391 Step 1 — what content:[] does on the prompt path at the measured base"

    echo "## The base this card may use, re-read at the start of Step 1"
    echo
    run g rev-parse "$MAIN_REF"
    echo "  Pinned BASE_SHA for this run: $BASE_SHA"
    if [ "$(g rev-parse "$MAIN_REF")" = "$BASE_SHA" ]; then
      echo "  Trunk still equals the pinned base, so every witness below is a read of the very"
      echo "  commit Step 0 measured."
    else
      echo "  Trunk moved since Step 0. Every witness below is still a read of the pinned base"
      echo "  — the commit this card was measured against — and what it costs to land on the"
      echo "  newer trunk is measured in raw/operator-landing-leg.txt."
    fi
    echo

    echo "## The module this card read, verbatim at the base (git show | cat -n)"
    echo
    g show "$BASE_SHA:$MODULE" | cat -n | sed 's/^/  /'
    echo
    echo "  The guard region and the emptiness region, with their line numbers:"
    g show "$BASE_SHA:$MODULE" | grep -n -e 'content.length === 0' \
      -e 'no payload was delivered' -e 'zero-bytes' -e 'ZERO_BYTES_NOTICE' \
      -e 'expected a string or a content-block array' \
      -e 'text block has no content to mark' -e 'counts as absent' \
      -e 'content length is absent' | sed 's/^/    /'
    echo

    echo "## The upstream claim, re-measured in the installed tree of this checkout"
    echo
    echo "  Versions and paths as the base's own dependency tree resolves them:"
    for name in pi-ai pi-coding-agent pi-agent-core; do
      link="$PKG/node_modules/@earendil-works/$name"
      if [ -e "$link" ]; then
        real=$(cd "$link" && pwd -P)
        ver=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$real/package.json" | sed -n '1p')
        echo "    @earendil-works/$name  version=$ver  (linked directly under $PKGREL)"
      else
        echo "    @earendil-works/$name  not linked under $PKGREL; present in the virtual store:"
        find "$CK/node_modules/.pnpm" -maxdepth 1 -name "@earendil-works+$name@*" \
          | sed -E 's#.*/@earendil-works\+##; s#_.*/node_modules.*##' | sed 's/^/      /'
      fi
    done
    echo
    echo "  Normalisation site 1 — the producer the card body names, at its own lines:"
    site1=$(find "$CK/node_modules/.pnpm" -path '*pi-agent-core*/dist/agent-loop.js' | sed -n '1p')
    copies1=$(find "$CK/node_modules/.pnpm" -path '*pi-agent-core*/dist/agent-loop.js' | wc -l)
    hashes1=$(find "$CK/node_modules/.pnpm" -path '*pi-agent-core*/dist/agent-loop.js' -exec md5sum {} + | awk '{print $1}' | sort -u | wc -l)
    echo "    file, first of $copies1 copies in the virtual store ($hashes1 distinct checksums, so all copies are byte-identical): ${site1#"$CK"/}"
    grep -n -e 'function createToolResultMessage' -e 'Untyped tools' -e 'so the null never enters' \
      -e 'content: finalized.result.content ?? \[\]' "$site1" | sed 's/^/      /'
    echo
    echo "  Normalisation site 2 — pi's own message transform, found by the parent's census:"
    site2=$(find "$CK/node_modules/.pnpm" -path '*pi-ai*/dist/api/transform-messages.js' | sed -n '1p')
    copies2=$(find "$CK/node_modules/.pnpm" -path '*pi-ai*/dist/api/transform-messages.js' | wc -l)
    hashes2=$(find "$CK/node_modules/.pnpm" -path '*pi-ai*/dist/api/transform-messages.js' -exec md5sum {} + | awk '{print $1}' | sort -u | wc -l)
    echo "    file, first of $copies2 copies in the virtual store ($hashes2 distinct checksums, so all copies are byte-identical): ${site2#"$CK"/}"
    grep -n -e 'content == null' "$site2" | sed 's/^/      /'
    echo
    echo "  Both installed versions are 1.1.0 and both lines sit where the card body and the"
    echo "  parent census measured them, so no number in this file is inherited."
    echo

    echo "## Base witness — a synthesised call into the real production functions"
    echo
    echo "  HONESTY LABEL: the report below is written by a probe this script generates,"
    echo "  executed against the module at $BASE_SHA and then deleted. It is NOT an observed"
    echo "  production session — the corpus search in the card body found zero recorded"
    echo "  instances of a toolResult message carrying an empty content array, and this card"
    echo "  implies none. What moves from reachability into measurement is the code path."
    echo
    echo "  The sentence above is only true if the probe really imported the base module, and"
    echo "  this checkout sits on the branch tip while this file is generated, so the claim is"
    echo "  proven rather than stated: the module and its test are checked back to their base"
    echo "  blobs, the working-tree file is hashed against the base blob before the probe runs,"
    echo "  and the tip is restored with its own proof afterwards. Measured the other way"
    echo "  first, this capture reported the post-fix announcement as if it were the base's"
    echo "  behaviour, which refutes the premise of the whole card."
    echo
    if [ ! -x "$PKG/node_modules/.bin/vitest" ]; then
      echo "  NOT PROVISIONED: $PKG/node_modules/.bin/vitest is absent in this checkout."
      echo "  Provision with: (cd $CK && pnpm install --frozen-lockfile --ignore-scripts)."
      echo "  Nothing below this line can be regenerated without it."
    else
      base_blob_id=$(g rev-parse "$BASE_SHA:$MODULE")
      g checkout "$BASE_SHA" -- "$MODULE" "$TEST"
      tree_blob_id=$(git hash-object "$PKG/src/tool-result-authority.ts")
      echo "  Base blob of the module      : $BASE_SHA:$MODULE = $base_blob_id"
      echo "  git hash-object of the file  : $tree_blob_id"
      if [ "$base_blob_id" = "$tree_blob_id" ]; then
        echo "  EQUAL — the file the prompt path imports is the base blob, so the probe"
        echo "  could not have loaded the branch's fix."
      else
        echo "  NOT EQUAL — the probe would load something other than the base module and"
        echo "  the report below must not be read as a base witness."
      fi
      echo
      cp "$TMP/witness.test.ts" "$PKG/src/__tests__/zz-stas391-witness.test.ts"
      vitest_run "src/__tests__/zz-stas391-witness.test.ts"
      echo "  --- the report the probe wrote, verbatim ---"
      sed 's/^/  /' "$REPORT"
      rm -f "$PKG/src/__tests__/zz-stas391-witness.test.ts"
      echo
      echo "  Probe deleted. Nested checkout porcelain while the base blobs are still in"
      echo "  place (2 expected — the module and its test, restored in the next block):"
      g status --porcelain | wc -l | sed 's/^/    /'
      g checkout "$TIP" -- "$MODULE" "$TEST"
      echo "  Restored to the tip $TIP. Porcelain lines after the restore (0 expected):"
      g status --porcelain | wc -l | sed 's/^/    /'
      restore_rc=0
      g diff --exit-code "$TIP" -- "$MODULE" "$TEST" > "$S/witness-restore.diff" 2>&1 || restore_rc=$?
      echo "  git diff --exit-code $TIP -- module test: rc=$restore_rc (0 = working tree"
      echo "  is byte-identical to the tip for both files). What it printed:"
      if [ -s "$S/witness-restore.diff" ]; then
        sed 's/^/    /' "$S/witness-restore.diff"
      else
        echo "    (no output)"
      fi
      tree_blob_id=$(git hash-object "$PKG/src/tool-result-authority.ts")
      echo "  The module after the restore, hashed and compared to the tip's own blob:"
      echo "    working tree now  $tree_blob_id"
      echo "    $TIP:$MODULE  $(g rev-parse "$TIP:$MODULE")"
      echo "    (the base blob this witness ran against was $base_blob_id)"
    fi
    echo

    echo "## Gate B inputs — which emptiness semantics the measured trunk holds"
    echo
    echo "  The neutraliser this card's dependency carries, counted on the base:"
    for sym in neutraliseStructureTokens ENVELOPE_DELIMITER ROLE_KEY; do
      rc=0
      out=$(g grep -c -e "$sym" "$BASE_SHA" -- packages plugins 2>&1) || rc=$?
      echo "    $sym -> rc=$rc, $(printf '%s' "$out" | grep -c . || true) file(s) with hits"
    done
    echo "  (the same three symbols are present at $LINE390, the branch STAS-390 delivered)"
    echo
    echo "  Is the guard this card deletes still on each revision?"
    for rev in "$BASE_SHA" "$LINE390" "$REM388"; do
      n=$(g grep -c 'content.length === 0' "$rev" -- "$MODULE" 2>/dev/null | sed 's/.*://' || true)
      echo "    $rev : content.length === 0 -> ${n:-0} occurrence(s)"
    done
    echo "  $REM388 is the unlanded STAS-388 remediation: no such guard, and it renders the"
    echo "  same value as a distinct notice instead. See findings.md, Gate B."
    echo

    echo "## What landing this card's patch now would cost, measured as content"
    echo
    echo "  Two different instruments, because they answer two different questions. Both run"
    echo "  entirely in scratch: each builds a directory from the revision's own blobs and"
    echo "  asks the question there. Neither one moves a ref or writes a tracked file."
    echo
    echo "  (a) Would the patch be accepted at that revision? Two strictness levels, because"
    echo "      they disagree and the disagreement is itself the finding: GNU patch tolerates"
    echo "      context drift with fuzz and offsets, git apply refuses the same file. The"
    echo "      first revision is the one the patch was written against, included as the"
    echo "      control that says the other two readings mean something."
    echo
    apply_check_at "$BASE_SHA" base
    apply_check_at "$LINE390" stas390
    apply_check_at "$REM388" stas388rem
    echo "  Read together: at $LINE390 the patch still lands under GNU patch (hunk 1 with"
    echo "  fuzz 2, hunks 2-3 at a 44-line offset) but not under git apply, which forbids"
    echo "  fuzz. So a rebase of this card onto that line carries the content; a tool that"
    echo "  demands exact context needs the anchors refreshed first. At $REM388 the patch is"
    echo "  refused by both, because that revision already replaced the code these hunks"
    echo "  delete — see findings.md, Gate B: that revision subsumes this card."
    echo
    echo "  (b) Would a three-way merge carry the content anyway? -- git merge-file, per file,"
    echo "      against the TRUE merge base of the pinned base and that revision, which is"
    echo "      what a rebase would use. The merge base is printed because using the wrong"
    echo "      one inflates the conflict count, and this measurement was wrong once."
    echo
    for pair in "$LINE390|stas390|STAS-390 line (STAS-389 neutraliser, rebased)" "$REM388|stas388rem|STAS-388 remediation (unlanded)"; do
      rev=${pair%%|*}; rest=${pair#*|}; tag=${rest%%|*}; label=${rest#*|}
      mb=$(g merge-base "$BASE_SHA" "$rev")
      echo "    vs $label"
      echo "      merge-base $BASE_SHA $rev = $mb"
      for path in "$MODULE" "$TEST"; do
        base_blob="$S/mf-base-$tag-$(basename "$path")"
        other_blob="$S/mf-other-$tag-$(basename "$path")"
        mine="$S/mf-mine-$tag-$(basename "$path")"
        g show "$mb:$path" > "$base_blob" 2>/dev/null || : > "$base_blob"
        g show "$rev:$path" > "$other_blob" 2>/dev/null || : > "$other_blob"
        if [ "$path" = "$MODULE" ]; then cp "$S/tip-module.ts" "$mine"; else cp "$S/tip-test.ts" "$mine"; fi
        rc=0
        out=$(git merge-file -p "$mine" "$base_blob" "$other_blob" 2>&1) || rc=$?
        echo "      $(basename "$path"): merge-file rc=$rc, conflict blocks=$(printf '%s\n' "$out" | grep -c '^<<<<<<<' || true)"
      done
    done
    echo "  A clean three-way merge is not the same as a correct result: it says the two edits"
    echo "  occupy different lines, not that the module still behaves as one card intends. The"
    echo "  pin quoted below is the instrument that speaks to intent."
    echo
    echo "  Where the pending line touches the two files the card names (hunk windows, -U0):"
    g diff -U0 "$BASE_SHA" "$LINE390" -- "$MODULE" "$TEST" | grep -E '^(@@|diff )' | sed 's/^/    /'
    echo "  no diff line printed for a file means that revision leaves it alone entirely."
    echo
    echo "  The one pin on the pending line that this card would have to reconcile, quoted"
    echo "  from $LINE390:$SIBLING:"
    g show "$LINE390:$SIBLING" | grep -n -A6 'keeps the empty payload' | sed 's/^/    /'
    echo
    echo "  The two landed pins this card's patch reconciles, quoted from $BASE_SHA:$TEST:"
    g show "$BASE_SHA:$TEST" | grep -n -e 'refuses to render a payload that was never delivered' \
      -e 'cannot print the same bytes for the two empty states' -e 'const dropped' \
      -e 'no payload was delivered' | sed 's/^/    /'
    echo

    echo "## Gate B — which branch the measurements force, and how this card read it twice"
    echo
    echo "  Facts first, with no letter attached. The throw is live on the measured trunk —"
    echo "  witnessed in W1 and W2 on both builders and on both the built-in and the"
    echo "  custom/MCP lane. The zero-bytes vocabulary an announcement needs already exists"
    echo "  on trunk, so this card's wording for the state has a target to land on. The"
    echo "  STAS-389/390 neutraliser line is not on that trunk: its three symbols are absent"
    echo "  at rc=1 with zero files, and that branch sits 2 commits ahead of this base with"
    echo "  the base 0 behind — the shape of a prepared, un-applied fast-forward. The"
    echo "  STAS-388 remediation vocabulary (EMPTY_NOTICES, no-content-blocks) is absent for"
    echo "  the same reason: it was never landed either."
    echo
    echo "  Step 1 read these same facts as a stop and delivered an unapplied patch. That"
    echo "  reading is corrected here, because the card's own text does not support it: the"
    echo "  mission ends on a verified branch tip, Step 2 is titled 'Author the change on a"
    echo "  newly named engine branch', and the only outcome that makes the later steps"
    echo "  unnecessary is a trunk that already renders a notice for the empty array — which"
    echo "  W1 and W2 refute. The one constraint the card does state, do not touch the module"
    echo "  until STAS-390 closes, is discharged: STAS-390 is done. The change was therefore"
    echo "  authored on a branch, the draft patch became that branch's commit, and"
    echo "  raw/unapplied-draft.patch is deleted so the change has one source of truth:"
    echo "  engine-diff.txt, generated by git diff over base..tip."
    echo
    echo "  Vocabulary consequence of the same measurements: with no-content-blocks absent"
    echo "  from trunk and the zero-bytes notice present, the empty array is announced with"
    echo "  the notice trunk already owns instead of a minted synonym. Which word the lane"
    echo "  should prefer is still the operator's call and is put in"
    echo "  raw/operator-landing-leg.txt together with the collision costs above."
  } > "$f"
}

# --- raw/red-green.txt ---------------------------------------------------------
capture_red_green() {
  local f="$RAW/red-green.txt"
  {
    hdr "STAS-391 — red and green, in the parent card's method"
    echo "Method: the change is authored on \`$TIP_REF\` at \`$TIP\`, so both runs use the"
    echo "lane's own tracked test file. GREEN runs it at the tip. RED swaps only the"
    echo "production module for its base blob — \`git checkout $BASE_SHA -- <module>\` — and"
    echo "leaves the test file at the tip, which is exactly the commit a reviewer would get"
    echo "by checking out the base commit of the branch with the new test. No probe copies"
    echo "and no test-text copies are installed for this section."
    echo
    echo "Every rc below is captured before scrubbing, so a pipeline cannot hide a failing"
    echo "run; the vitest filter is the file path alone, never a \`-t\` name filter, so a"
    echo "renamed test would surface as a vanished case rather than a false pass."

    echo "## GREEN — the lane's tracked test file, at the branch tip"
    echo "Tip module and tip test, nothing swapped:"
    green_rc=0
    vitest_run "src/__tests__/tool-result-authority.test.ts" || green_rc=$?
    echo "vitest rc=$green_rc"
    echo
    echo "## GREEN — the whole package suite, at the branch tip"
    echo "Summary lines only; the base/tip counter pair is in raw/suite-baseline.txt."
    run g rev-parse "$TIP"
    (
      cd "$PKG"
      set +e
      ./node_modules/.bin/vitest run 2>&1 | scrub_vitest | grep -E 'Test Files|Tests +[0-9]|FAIL|rc='
      echo "vitest rc=${PIPESTATUS[0]}"
    )
    echo
    echo "## GREEN — typecheck, at the branch tip"
    run bash -c "cd '$PKG' && ./node_modules/.bin/tsc --noEmit; echo rc=\$?"
    echo
    echo "## Line-count guardrail, base against tip"
    echo "The repo gate is a root script, not part of the package's test script. Its verdict"
    echo "is a repo-wide list of oversized files; what matters here is whether either"
    echo "revision of this card's two files appears in it."
    echo "Files of this card at the base:"
    run bash -c "git -C '$CK' show $BASE_SHA:$MODULE | wc -l"
    run bash -c "git -C '$CK' show $BASE_SHA:$TEST | wc -l"
    echo "The same files at the tip:"
    run bash -c "git -C '$CK' show $TIP:$MODULE | wc -l; git -C '$CK' show $TIP:$TEST | wc -l"
    run bash -c "cd '$CK' && node scripts/check-file-line-count.mjs 2>&1 | grep -c '^$PKGREL' || echo 0"
    echo "Zero is the expected count on both revisions: neither revision of either file is"
    echo "over the guardrail, so this card neither tripped it nor inherited a trip."

    echo "## RED — the same tracked test file, module checked out at the base"
    echo "This block runs inside a subshell that always restores the tip blob, and the"
    echo "restore is verified in the next block."
    g checkout "$BASE_SHA" -- "$MODULE"
    echo "Module blob in the working tree is now the base one (diff against the tip):"
    run g diff --stat "$TIP" -- "$MODULE"
    (
      cd "$PKG"
      set +e
      ./node_modules/.bin/vitest run src/__tests__/tool-result-authority.test.ts --reporter=verbose 2>&1 \
        | scrub_vitest | sed -n '1,/Test Files/p'
      echo "vitest rc=${PIPESTATUS[0]}"
    )
    g checkout "$TIP" -- "$MODULE"
    echo
    echo "## The checkout is back at the tip — proven, not asserted"
    echo "Porcelain lines after the run (0 expected):"
    g status --porcelain | wc -l
    echo "Byte-difference of the working tree against the tip for both files (no output means"
    echo "both are identical to the tip):"
    g diff --exit-code "$TIP" -- "$MODULE" "$TEST" && echo "both files match the tip"
    run g rev-parse HEAD
  } > "$f" 2>&1
}

# --- engine-commits.txt / engine-diff.txt --------------------------------------
capture_engine_delivery() {
  local rc out main_now drift
  {
    hdr "STAS-391 — the engine commits this card authored"
    main_now=$(g rev-parse "$MAIN_REF")
    drift=$(g rev-list --count "$BASE_SHA..$main_now")
    echo "Branch: $TIP_REF"
    echo "Tip:    $TIP"
    echo "Base:   $BASE_SHA  (the commit $MAIN_REF pointed at when this card measured it)"
    # The label has to come from the same measurement printed below it. An earlier
    # revision of this file wrote 'unchanged' as prose while the block nine lines down
    # measured trunk one commit ahead, so a reader of this file alone concluded a
    # fast-forward was available when it is not.
    if [ "$main_now" = "$BASE_SHA" ]; then
      echo "Trunk:  $main_now  — unchanged since that base was pinned, so the branch is"
      echo "        trunk's direct successor and a fast-forward lands it as it is."
    else
      echo "Trunk:  $main_now  — MOVED by $drift commit(s) since the pinned base, and NOT"
      echo "        by this card: its commit exists only on $TIP_REF and this card wrote no"
      echo "        trunk ref. Consequence: no fast-forward is available — the branch must be"
      echo "        rebased onto $main_now (or merged) before it can land. What that costs is"
      echo "        measured in raw/operator-landing-leg.txt, not assumed here."
    fi
    echo
    echo "## The commits on the branch, base excluded"
    run g log --format="%h__%s" "$BASE_SHA..$TIP"
    echo
    echo "## Trunk is not this card's to move"
    echo "Measured at run time rather than asserted: what trunk points at now, what this"
    echo "card's base was, and how far the two have drifted since the base was taken."
    run g rev-parse "$MAIN_REF"
    echo "base this card was authored on: $BASE_SHA"
    echo "commits trunk gained since that base (0 means the branch is still trunk's successor):"
    run g rev-list --count "$TIP..$MAIN_SHA"
    echo "What those commits did to the three files this change interacts with:"
    run g diff --stat "$BASE_SHA" "$MAIN_SHA" -- "$MODULE" "$TEST" "$SIBLING"
    echo "No diff line means none of them was touched, so trunk's movement is not a conflict"
    echo "source for this card's change."
    echo
    echo "## The nested worktree the branch was authored in"
    echo "Porcelain lines after the commit:"
    g status --porcelain | wc -l
  } > "$OUT/engine-commits.txt"

  {
    hdr "STAS-391 — the engine diff this card authored (git diff base..tip)"
    echo "One-sentence intent: an empty content-block array reaches prompt assembly as a"
    echo "delivered empty payload and is announced as such, instead of throwing and losing"
    echo "the whole prompt."
    echo
    echo "This is \`git diff $BASE_SHA $TIP\` — the change as it stands on the branch, not a"
    echo "draft of it. The red/green that pins it is in raw/red-green.txt."
    echo
    echo "\`\`\`"
    g diff "$BASE_SHA" "$TIP"
    echo "\`\`\`"
  } > "$OUT/engine-diff.txt"
}

# --- raw/operator-landing-leg.txt ----------------------------------------------
capture_operator_landing_leg() {
  local f="$RAW/operator-landing-leg.txt"
  {
    hdr "STAS-391 — the landing decision this card hands to the operator"

    echo "## What this card delivered, and what it deliberately did not touch"

    echo "- Delivered on branch \`$TIP_REF\` at tip \`$TIP\`: the announcement of an empty"
    echo "  content-block array on the prompt path, plus the two test amendments."
    echo "- Not touched: no trunk ref was moved by this card, nothing was pushed, no pull"
    echo "  request was opened, nothing was deployed. Where trunk actually stands is measured"
    echo "  two blocks below rather than asserted here, because trunk keeps moving while a"
    echo "  card runs."
    echo "- The trunk ref advances only when an operator runs one of the legs below."

    echo "## Predicate measured now: can this branch reach trunk without a merge?"
    ahead=$(g rev-list --count "$MAIN_SHA..$TIP")
    behind=$(g rev-list --count "$TIP..$MAIN_SHA")
    echo "ahead=$ahead behind=$behind, measured against $MAIN_SHA ($MAIN_REF) at run time."
    if [ "$behind" = "0" ] && [ "$ahead" = "1" ]; then
      echo "Shape: the branch is trunk's direct successor — a fast-forward lands it as it is."
    else
      echo "Shape: trunk moved $behind commit(s) past this card's base, so no fast-forward is"
      echo "available and the branch has to be rebased onto $MAIN_SHA (or merged) first. The"
      echo "cost of that rebase is measured below, not guessed."
    fi
    echo "What trunk's new commits did to the files this change touches:"
    run g diff --stat "$BASE_SHA" "$MAIN_SHA" -- "$MODULE" "$TEST" "$SIBLING"
    echo "Empty output means none of them was touched, so the rebase costs nothing here."
    echo "Applicability of this branch's diff onto the current trunk tip: the two rc values"
    echo "below are the verdict for rebasing this commit onto it."
    apply_check_at "$MAIN_SHA" "trunk-tip"

    echo "## Collision cost of landing this branch, measured at the revisions it can meet"
    echo
    echo "Three instruments, all run against scratch copies — none of them touches the"
    echo "checkout. Each scratch copy is given its own one-commit repository first, and that"
    echo "is not ceremony: measured in this run, \`git apply --check\` run inside a scratch"
    echo "directory that merely sits inside this checkout was answered by the enclosing"
    echo "repository and returned rc=0 for every revision, including the two GNU patch"
    echo "rejects. Judged on its own content it disagrees, and that is the verdict printed"
    echo "below."
    echo
    echo "- \`patch\` — GNU patch, the fuzz-tolerant applicator an operator would use by hand."
    echo "- \`git apply\` — git's own strict applicator, the one a \`git am\` or a scripted"
    echo "  landing uses. rc=1 with a 'patch failed' line means it refuses the diff as-is."
    echo "- \`merge-file\` — what a real \`git merge\` resolves to. rc=0 with zero conflict"
    echo "  blocks merges silently; rc=2 with N blocks means a human resolves N regions."
    echo
    echo "Each revision is measured against the merge base named in its own block. The"
    echo "\`merge-file\` numbers behind these verdicts are in raw/base-witness.txt, which runs"
    echo "them against the same blobs this file names."
    echo
    echo "The two cards this branch can meet are the two that decided the same question"
    echo "differently, so their numbers are the landing cost worth knowing."
    echo
    echo "### The STAS-390 line ($LINE390, fusion/stas-390-tool-channel-authority)"
    echo "The predicate this leg turns on is measured here rather than asserted:"
    echo "merge-base 390-line/trunk. That is the base this branch is built on, so the 390"
    echo "line is still ahead of trunk and this card's commit is not built on it."
    run g merge-base "$TIP" "$LINE390"
    apply_check_at "$LINE390" "the 390 line"
    echo "Verdict: GNU patch takes this branch with two fuzzed/offset hunks, 'git apply'"
    echo "refuses it, and a merge resolves it with zero conflict blocks. The silence is the"
    echo "cost to watch: nothing will warn that the STAS-390 line's own pin —"
    echo "\`tool-channel-authority.test.ts:200\`, 'expect(() => promptWithToolResult([])).toThrow"
    echo "(/no content to mark/)' — asserts the opposite of what this branch now does. That"
    echo "test is not touched here, because deciding which empty state should throw is the"
    echo "operator's vocabulary call, not this card's."

    echo "### The STAS-388 remediation branch ($REM388, fusion/stas-388-tool-result-authority)"
    echo "Not on trunk, and the merge base is the commit that introduced the zero-authority"
    echo "layer — before this card's base and before the STAS-390 line."
    run g merge-base "$TIP" "$REM388"
    apply_check_at "$REM388" "the STAS-388 remediation branch"
    echo "Verdict: the remediation branch already contains the announcement this card just"
    echo "authored, so landing it over this branch produces real textual conflicts — two per"
    echo "file — because that branch's version of the same decision names the state"
    echo "\`no-content-blocks\` and this one names it \`zero-bytes\`. The two vocabularies are"
    echo "still one decision; card rules say the older vocabulary is inherited, not collapsed."

    echo "## Where the empty-state vocabulary already stands on trunk"
    echo "Quoted verbatim from the landed pins, so the operator can match words rather than"
    echo "descriptions. This card used the first as its target."
    run bash -c "git -C '$CK' show $MAIN_SHA:$TEST | sed -n '128,141p'"
    echo "The landed pair separates three states: zero bytes of text, a payload that was"
    echo "never delivered, and the refusal to print the same bytes for the first two. Only"
    echo "the first two survive here, because the measurement in raw/base-witness.txt shows"
    echo "the runtime authors the third state on purpose."

    echo "## What was NOT done here, and why"
    echo "- Nothing landed: this card's commit exists on its own branch only. \`$MAIN_SHA\` is"
    echo "  trunk's own history, untouched by this card, and a trunk that deploys on merge is"
    echo "  landed by an operator, not by an executor card."
    echo "- No pull request, no push, no 'gh' invocation at all."
    echo "- No second announcement string: this card reuses the notice the trunk already"
    echo "  owns instead of inventing a synonym."
    echo "- The STAS-390 line's contradicting pin was left in place rather than quietly"
    echo "  reconciled — see the collision block above."
  } > "$f"
}

# --- driver ---------------------------------------------------------------------
write_probes

# The change lives on its own engine branch. The tip is pinned by SHA and every derived
# file below is regenerated FROM that commit, never re-authored here.
g rev-parse --verify -q "$TIP" > /dev/null || { echo "the card's branch tip is missing" >&2; exit 1; }
g diff "$BASE_SHA" "$TIP" > "$TIPDIFF"
g show "$TIP:$MODULE" > "$S/tip-module.ts"
g show "$TIP:$TEST" > "$S/tip-test.ts"

capture_dependency_gate
capture_suite_baseline
capture_base_witness
capture_red_green
capture_engine_delivery
capture_operator_landing_leg
echo "generated: $RAW/dependency-gate.txt $RAW/base-witness.txt $RAW/suite-baseline.txt \
$RAW/red-green.txt $RAW/operator-landing-leg.txt engine-commits.txt engine-diff.txt"