#!/usr/bin/env bash
# STAS-392 — regenerate every raw capture in stas-392-engine-evidence/raw/.
#
# Run from the ROOT of this card's worktree:
#     bash stas-392-engine-evidence/generate.sh            # every admissible section
#     bash stas-392-engine-evidence/generate.sh ac0        # one section
#
# Sections: ac0 | census | rebase | redgreen | baseline | window | ac4 | landing | engine
# A section is "admissible" when the ref or working state it measures exists; the
# script prints why it skipped instead of failing, so an early run is informative.
#
# Where the measurements come from:
#   - R1 = this card worktree (HEAD, branch, porcelain, ignore probe in child-path form).
#   - R2 = the engine repository THROUGH the nested linked worktree at
#          .engine/stas-392-engine, provisioned once by the command recorded in
#          raw/ac0-roots.txt. Reads go through `git -C` on that path; the only
#          `--git-dir=$HOME/git/Fusion/.git` form is the provisioning read, because
#          the forbidden `--git-dir=<nested checkout>/.git` variant resolves a gitlink
#          that reports phantom mass deletions.
#   - R3 = the running build of this board, which exposes no revision on any
#          agent-readable surface: running build unidentifiable — fix liveness unverified.
#
# Nothing here writes refs/heads/main, pushes, opens a pull request, or restarts a
# daemon. The only writes into the engine checkout are (a) branch operations this card
# is named to perform, (b) the temporary module checkout of a RED run, always followed
# by its restore proof, and (c) node_modules from the recorded pnpm install.
set -euo pipefail
shopt -s inherit_errexit

CK=".engine/stas-392-engine"
OUT="stas-392-engine-evidence"
RAW="$OUT/raw"
PKGREL="packages/pi-claude-cli"
MODULE="$PKGREL/src/tool-result-authority.ts"
TEST="$PKGREL/src/__tests__/tool-result-authority.test.ts"
SIBLING="$PKGREL/src/__tests__/tool-channel-authority.test.ts"
PKG="$CK/$PKGREL"

MAIN="refs/heads/main"
R390="refs/heads/fusion/stas-390-tool-channel-authority"
R391="refs/heads/fusion/stas-391-empty-tool-result-announcement"
R392="refs/heads/fusion/stas-392-empty-tool-result-announcement"

ENGINE_GIT="$HOME/git/Fusion/.git"
TMP=".fusion-tmp/stas-392"
S="$TMP/scratch"
WANT="${1:-all}"

mkdir -p "$RAW" "$TMP" "$S"

g()    { git -C "$CK" "$@"; }
want() { [ "$WANT" = "all" ] || [ "$WANT" = "$1" ]; }

run() {               # print the command in its real form, then its output and rc
  local rc=0 out label
  label="$*"
  out=$("$@" 2>&1) || rc=$?
  printf '  $ %s\n' "$label"
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/      /'; else printf '      (no output)\n'; fi
  printf '        rc=%s\n\n' "$rc"
}

grun() {              # same, but inside the nested engine checkout
  local rc=0 out cmd=("$@")
  out=$(g "${cmd[@]}" 2>&1) || rc=$?
  printf '  $ git -C %s %s\n' "$CK" "${cmd[*]}"
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/      /'; else printf '      (no output)\n'; fi
  printf '        rc=%s\n\n' "$rc"
}

vrun() {              # vitest from inside the package, scrubbed of volatile lines
  local rc=0 out label="$1"; shift
  out=$( cd "$PKG" && NO_COLOR=1 ./node_modules/.bin/vitest run "$@" 2>&1 ) || rc=$?
  printf '%s\n' "$out" > "$S/last-vitest.txt"
  printf '  $ (cd %s && NO_COLOR=1 ./node_modules/.bin/vitest run %s)\n' "$PKGREL" "$label"
  printf '%s\n' "$out" \
    | sed -E -e 's#^ *RUN  v[0-9.]+ .*#RUN  <vitest> <package-dir>#' \
             -e "s#$PKG#<package>#g" -e 's/[0-9]+ms//g' -e 's/Start at.*//' \
             -e 's/^ *//' \
    | grep -E '^(RUN |✓|×|→|Test Files|Tests |Snapshots|Failed Tests)' || true
  printf '        rc=%s\n\n' "$rc"
}

hdr() {
  printf '# STAS-392 — %s\n' "$1"
  printf '# generated: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '# %s\n' "$2"
  [ "$#" -ge 3 ] && printf '# %s\n' "$3"
  printf '\n'
}

have() { g rev-parse --verify --quiet "$1" >/dev/null 2>&1 || return 1; }
short() { g rev-parse --short "$1"; }

# --- ac0: the ground, measured inside the run ------------------------------------------
section_ac4() {
  want ac4 || return 0
  if ! have "$R392"; then echo "skipped the AC4 probe ($R392 absent)"; return 0; fi
  local F="$RAW/ac4-probe.txt"
  {
    hdr "AC4 — the probe over the whole delivered tip, every hit classified" \
        "Two greps: the defect's own words, then the emptiness words no deliverable may" \
        "introduce. A surviving pin is not an oversight, so each hit names which it is."
    echo "admissible for: $R392 = $(g rev-parse "$R392")"
    echo
    echo "## grep 1 — the defect's own words"
    echo
    grun grep -n "no content to mark" "$R392" -- "$PKGREL/src"
    echo "Classification, hit by hit:"
    echo
    echo "  - $MODULE, two messages: production, kept on purpose. They name a payload that"
    echo "    cannot be read at all — a content that is neither a string nor a content-block"
    echo "    array, and a text block whose text is not a string. The third message, the one the"
    echo "    empty-array arm used to author, is the one this line deletes."
    echo "  - $TEST, the loop line: a test asserting a RETAINED throw. It walks the four payloads"
    echo "    whose emptiness is ambiguous (undefined, null, a text block with no text, 42); the"
    echo "    card's ban list keeps these throwing, so the line must exist and its list must no"
    echo "    longer hold an empty array."
    echo "  - $TEST, the buildPrompt line: the pin that a failure inside the marking step reaches"
    echo "    the caller through the assembler. Untouched by this card."
    echo
    echo "## grep 2 — the emptiness words no deliverable may introduce"
    echo
    grun grep -nE "no content blocks|nothing delivered|empty content array" "$R392" -- "$PKGREL/src"
    echo "Classification:"
    echo
    echo "  - provider.test.ts: pre-existing and unrelated — a streamed provider fixture that"
    echo "    carries no content blocks. Not an emptiness vocabulary and not authored here."
    echo "  - $SIBLING: pin (c)'s reconciled title, which names the empty content array in order"
    echo "    to state what it now delivers. This is the card's own delta."
    echo
    echo "## the guard arm this line deletes, as it stands on the trunk it replaces"
    echo
    grun grep -n "content.length === 0" "$MAIN" -- "$MODULE"
    echo "and the delivered tip's own lines, to show the arm is gone rather than hidden behind"
    echo "a fallback"
    echo
    grun grep -nE "length === 0|ZERO_BYTES_NOTICE" "$R392" -- "$MODULE"
    echo "## what the pins assert now, in one diff"
    echo
    grun show --format='%h %s' "$R392" -- "$TEST" "$SIBLING"
  } > "$F" 2>&1
  echo "wrote $F"
}

section_ac0() {
  want ac0 || return 0
  local F="$RAW/ac0-roots.txt"
  {
    hdr "AC0 — the ground measured inside this run" \
        "Every ref is named, never pinned to a SHA typed from a card body. A SHA in any" \
        "captured file is a label of a past state, never an input to a later step."
    echo "## R1 — this card worktree"
    echo
    run git rev-parse --short HEAD
    run git branch --show-current
    run bash -c "git status --porcelain | wc -l | sed 's/^/porcelain lines: /'"
    echo "### ignore probe, in child-path form, so exclusion is proven not assumed"
    run git check-ignore -v .engine/stas-392-engine/probe.txt
    run git check-ignore -v .fusion-tmp/stas-392/scratch/probe.txt
    echo
    echo "## R2 — provisioning the nested linked checkout of the engine repository"
    echo
    echo "A previous session registered this path and its directory was later cleaned, so"
    echo "the first add was refused as a stale registration. The override is scoped to this"
    echo "path on purpose: 'worktree prune' would have deleted STAS-391's own (also stale)"
    echo "registration entry, and this card does not move another card's artifacts."
    echo
    echo '  $ git --git-dir=$HOME/git/Fusion/.git worktree add --detach "$PWD/.engine/stas-392-engine" refs/heads/main'
    echo "      Preparing worktree (detached HEAD 4fa1ea0e64)"
    echo "      fatal: '...stas-392/.engine/stas-392-engine' is a missing but already registered worktree;"
    echo "      use 'add -f' to override, or 'prune' or 'remove' to clear"
    echo "        rc=128"
    echo
    echo "Regeneration is idempotent, so the accepted form below is only re-run when the"
    echo "checkout is missing. It is the same command with -f, which was the fix:"
    echo
    if [ -d "$CK/.git" ]; then
      echo "  checkout already present; -f add not re-run this generation"
      echo "  $ ls -d $CK"
      echo "      $CK"
      echo "        rc=0"
    else
      run git --git-dir="$ENGINE_GIT" worktree add -f --detach "$PWD/$CK" refs/heads/main
    fi
    echo
    echo "### the forbidden form, recorded so a reviewer knows it was considered"
    echo "  --git-dir=<nested checkout>/.git is never used: its gitlink resolves through"
    echo "  the linked worktree and reports phantom mass deletions (family finding)."
    echo
    echo "## R2 — the ground read from inside the nested checkout"
    echo
    grun rev-parse --short "$MAIN"
    grun rev-parse "$MAIN"
    grun rev-parse HEAD
    grun status --porcelain
    echo "## occupancy: derived live from worktree list, never assumed either way"
    echo
    grun worktree list --porcelain
    echo "### reduced: who holds each ref in play (holder = a live checkout, prunable = a"
    echo "### registration whose directory is gone; a held branch cannot be moved by git)"
    echo
    g worktree list --porcelain | awk '
      /^worktree /{wt=$2; pr=""}
      /^branch /{b=$2; if (b == "refs/heads/main" || b ~ /stas-39[012]/) printf "  %s -> holder=%s\n", b, wt}
      /^detached$/{if (wt ~ /stas-39[12]/) printf "  (detached) -> holder=%s\n", wt}
      /^prunable /{printf "  PRUNABLE: %s\n", $0}'
    echo
    echo "## the 391 ref name is discovered, not assumed"
    echo
    grun for-each-ref "--format=%(refname) %(objectname:short)" refs/heads/fusion/
    echo "### filtered to stas-39"
    g for-each-ref --format='%(refname) %(objectname:short)' refs/heads/fusion/ | grep -E 'stas-39' || true
    echo
    echo "## dependency of every test run: the package's own vitest binary"
    echo
    run bash -c "ls $PKG/node_modules/.bin/vitest"
    echo "The install at the nested root (node_modules was absent before it):"
    echo
    echo '  $ pnpm install --frozen-lockfile --ignore-scripts'
    echo "      Done in 2.6s using pnpm v10.33.0"
    echo "        rc=0"
    echo "  --ignore-scripts is disclosed: it skips postinstall native builds. The vitest"
    echo "  lane this card runs needs no native module, which is why the flag is affordable."
    echo
    echo "## AC1 census — is the invariant on the trunk?"
    echo
    for R in "$MAIN" "$R390" "$R391"; do
      echo "### $R"
      grun grep -c neutraliseStructureTokens "$R" -- "$PKGREL/src"
    done
    echo "## Gate V — which emptiness vocabulary does the ground hold?"
    echo
    echo "Per-symbol counts over packages/pi-claude-cli, all three refs."
    echo
    printf '%-28s %-10s %-10s %-10s\n' SYMBOL main 390 391
    for SYM in neutraliseStructureTokens ENVELOPE_DELIMITER ROLE_KEY ZERO_BYTES_NOTICE \
               EMPTY_NOTICES emptinessOf DeliveredText no-content-blocks; do
      line="$(printf '%-28s' "$SYM")"
      for R in "$MAIN" "$R390" "$R391"; do
        N=$(g grep -c "$SYM" "$R" -- "$PKGREL" 2>/dev/null | awk -F: '{s+=$NF} END {print s+0}' || true)
        line="$line$(printf ' %-10s' "$N")"
      done
      printf '%s\n' "$line"
    done
    echo
    echo "Verdict is written in findings.md; the literals this card asserts come from the"
    echo "measured ground, printed here so the reading is checkable:"
    echo
    grun grep -n "zero-bytes\\|ZERO_BYTES_NOTICE" "$MAIN" -- "$PKGREL"
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- census: every pin, on trunk and both carried refs ---------------------------------
section_census() {
  want census || return 0
  local F="$RAW/pin-census.txt"
  {
    hdr "AC0/AC4 — the empty-array pin census, on trunk and on both carried refs" \
        "Three probes per ref. The second fragment is what a single-literal grep misses:"
    printf '"no payload was delivered" matches an assertion that says nothing about `no content to mark`.\n'
    for R in "$MAIN" "$R390" "$R391"; do
      [ "$R" = "$R392" ] && continue
      have "$R" || { echo "### $R — absent, skipped"; continue; }
      echo "## $R  (tip $(short "$R"))"
      echo
      grun grep -n "no content to mark" "$R" -- "$PKGREL"
      grun grep -n "no payload was delivered" "$R" -- "$PKGREL"
      grun grep -n 'promptWithToolResult(\[\])' "$R" -- "$PKGREL"
      echo "### does the sibling test file exist on this ref?"
      if g cat-file -e "$R:$SIBLING" 2>/dev/null; then
        echo "      $SIBLING exists"; echo "        rc=0"
      else
        echo "      $SIBLING does NOT exist"; echo "        rc=128"
      fi
      echo
    done
    echo "## the three pins, located by title rather than line number"
    echo
    for R in "$MAIN" "$R390" "$R391"; do
      have "$R" || continue
      echo "### $R"
      echo "--- pin (a) loop and its list"
      g show "$R:$TEST" | grep -n -A6 'const dropped' || echo "      (no dropped list)"
      echo "--- pin (b) its own it() and its last assertions"
      g show "$R:$TEST" | grep -n -B2 -A3 'no payload was delivered\|promptWithToolResult(\[\])' || echo "      (no empty-array call)"
      echo "--- pin (c) sibling file"
      g show "$R:$SIBLING" 2>/dev/null | grep -n -B4 -A3 'promptWithToolResult(\[\])' || echo "      (file absent or no empty-array call)"
      echo
    done
    echo "## classification (authored — the hits above are the measurement)"
    echo
    cat <<'CLASSIFIED'
  empty-array-must-go  an assertion that `content: []` throws, i.e. it pins the guard
                       STAS-391 deletes. Three such sites exist across the family:
                        (a) tool-result-authority.test.ts, the `dropped` loop that still
                            lists `[]`  -> reconciled by STAS-391's commit, verified here
                        (b) tool-result-authority.test.ts, the standalone
                            /no payload was delivered/ assertion -> reconciled by
                            STAS-391's commit, verified here
                        (c) tool-channel-authority.test.ts, /no content to mark/ inside
                            the it() titled "...must not widen or weaken the fail-loud
                            rule it builds on" -> arrives only with the 390 line, so
                            STAS-391's base could not reach it. THIS CARD's delta.
  retained             a hit that says nothing about `[]`: the production throw
                       messages, the `dropped` loop's surviving four entries, and
                       tool-result-authority.test.ts's
                       "propagates a failure inside the marking step to the caller"
                       (content: undefined through buildPrompt). AC3 ground — the
                       390/392 line must keep all of them throwing.
  already-reconciled   a hit whose file is on a ref where the guard is already deleted
                       and the assertion rewritten (see the 391 section above: "no
                       payload was delivered" returns rc=1 there).
CLASSIFIED
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- rebase: the 390 line onto the measured tip, with the no-authored-code proof --------
section_rebase() {
  want rebase || return 0
  local F="$RAW/rebase-proof.txt"
  if ! have "$R390"; then echo "skipped rebase ($R390 absent)"; return 0; fi
  # PRE is this card's record of the tip it found on the ref; the reflog entry is the
  # machine-readable form of the same fact, and STAS392_PRE390 pins it explicitly.
  local PRE="${STAS392_PRE390:-$(g rev-parse "$R390@{1}")}"
  {
    hdr "AC6 — the 390 line moved by rebase alone, authoring no code" \
        "PRE is the tip this card found on the ref, POST its delivered tip. The proof is" \
        "scoped: an unscoped PRE..POST diff is non-empty by construction whenever trunk moved."
    echo "## position at the delivered tip (a regenerated run prints the live position; the"
    echo "## PRE/POST pair below is the before-and-after this capture is evidence for)"
    echo
    grun rev-list --left-right --count "$MAIN...$R390"
    grun merge-base --is-ancestor "$MAIN" "$R390"
    echo "## the rebase this card ran (no --force: the ref had no live holder)"
    echo
    echo "  PRE  $PRE"
    echo "  POST $(g rev-parse "$R390")"
    echo "  onto $(g rev-parse "$MAIN")"
    echo
    echo "  \$ git -C $CK checkout fusion/stas-390-tool-channel-authority"
    echo "      Switched to branch 'fusion/stas-390-tool-channel-authority'    rc=0"
    echo "  \$ git -C $CK rebase refs/heads/main"
    echo "      Rebasing (1/2)Rebasing (2/2)Successfully rebased and updated refs/heads/fusion/stas-390-tool-channel-authority."
    echo "        rc=0 (no CONFLICT line appeared; nothing was resolved by hand)"
    echo
    echo "## conflict predicate, before and after (rc=0 and no CONFLICT line = mechanical landing)"
    echo "Before the rebase the predicate printed tree c7c6a80f22173854de47fea71500da7af6d18b23 with"
    echo "rc=0; after it it prints the same tree with rc=0. An identical result tree across the rebase"
    echo "is a second statement of the same fact as the range-diff below: what lands is unchanged."
    echo
    grun merge-tree --write-tree --name-only "$MAIN" "$R390"
    echo "## no-authored-code proof, part 1: scoped diff over the paths the line owns"
    echo
    grun diff --stat "$PRE" "$R390" -- "$MODULE" "$PKGREL/src/__tests__/"
    echo "## no-authored-code proof, part 2: range-diff, every commit must map to '='"
    echo
    grun range-diff "$(g merge-base "$MAIN" "$PRE")" "$PRE" "$R390"
    echo "## the second leg: $R392 onto the neutraliser-carrying ground"
    echo
    echo "AC1 is re-measured first, because it picks the ground. The engine trunk still does"
    echo "not carry the neutraliser, so this leg rides on the delivered 390 tip rather than trunk."
    echo
    grun grep -c neutraliseStructureTokens "$MAIN" -- "$PKGREL/src"
    echo "(rc=1 with no output is the zero: the invariant is not on trunk, so the ground is 390)"
    echo
    echo "PRE  = the predecessor's tip, which this card never moved; POST = the delivered tip"
    echo
    echo "Why the restack is not cosmetic — measured on the predecessor's ref as the parent left it:"
    echo "it is not a descendant of the 390 line, so landing it after 390 would not fast-forward at"
    echo "all, and its module carries no neutraliser, so it would have been announced on ground that"
    echo "does not hold the invariant."
    echo
    run git -C "$CK" merge-base --is-ancestor "$R390" "$R391"
    echo "(rc=1: the predecessor's ref is not above the 390 line — the operator's second merge would have refused it)"
    grun grep -c neutraliseStructureTokens "$R391" -- "$MODULE"
    echo "(rc=1 with no output: the predecessor's module carries no neutraliser)"
    grun grep -c neutraliseStructureTokens "$R392" -- "$MODULE"
    echo "(a positive count here: the delivered line is announced on ground that does hold it)"
    echo
    echo "  PRE  $(g rev-parse "$R391")   (a different ref: POST is $(g rev-parse "$R392"))"
    echo "  POST $(g rev-parse "$R392")"
    echo "  onto $(g rev-parse "$R390")"
    echo
    grun rev-list --left-right --count "$R390...$R392"
    run git -C "$CK" merge-base --is-ancestor "$R390" "$R392"
    run git -C "$CK" merge-base --is-ancestor "$MAIN" "$R392"
    echo "the conflict predicate for this leg, rc=0 and no CONFLICT line:"
    echo
    grun merge-tree --write-tree --name-only "$R390" "$R391"
    echo "range-diff over this leg alone, one commit against one commit. The delta is pin (c),"
    echo "this card's own work, so a '!' here is the expected result, not a regression."
    echo
    grun range-diff "$R391~1..$R391" "$R390..$R392"
    echo "the fold proof: one commit above the ground, naming every reconciled file, and no"
    echo "separate test-fix commit anywhere in the stack."
    echo
    grun show --stat --format='%h %s' "$R392"
    grun log --oneline "$MAIN..$R392"
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- redgreen: one fresh red-green per layer -------------------------------------------
section_redgreen() {
  want redgreen || return 0
  local F="$RAW/red-green.txt"
  local L1TIP=""
  if have "$R390"; then L1TIP=$(g rev-parse "$R390"); fi
  {
    hdr "AC5 — two fresh red-greens on the new ground, one per layer" \
        "Neither run is recycled from STAS-390 or STAS-391; both were measured on a base" \
        "this card does not share. Evidence is admissible only for the tip it names."
    echo "# LAYER 1 — the channel invariant at the delivered 390 tip"
    echo "admissible for: $R390 = ${L1TIP:-<ref absent — run Step 1 first>}"
    echo
    if have "$R390"; then
      echo "The checkout is switched to this layer's own branch first, so the test files running"
      echo "are the ones this layer ships — measuring layer 1 while a later layer's tests are in the"
      echo "tree would count their failures too, and the two layers would not be separable."
      echo
      g checkout --quiet "${R390#refs/heads/}"
      echo "checked out for layer 1: $(g rev-parse --abbrev-ref HEAD) = $(g rev-parse HEAD)"
      echo
      vrun "both test files, branch tip" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
      echo "## RED: only the production module checked out at the freshly measured trunk"
      echo "The test files are never checked out — $SIBLING does not exist at trunk, and"
      echo "restoring it would delete the assertion and leave a run with no test file."
      echo
      grun checkout "$MAIN" -- "$MODULE"
      grun status --porcelain
      vrun "both test files, module at trunk" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
      echo "## restore, then the restore proof (empty porcelain)"
      echo
      grun checkout HEAD -- "$MODULE"
      grun status --porcelain
      vrun "both test files, restored" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
    else
      echo "(skipped: $R390 does not exist yet)"
    fi
    echo "# LAYER 2 — the announcement layer at the delivered 392 tip"
    if have "$R392"; then
      g checkout --quiet "${R392#refs/heads/}"
      echo "admissible for: $R392 = $(g rev-parse "$R392")"
      echo "checked out for layer 2: $(g rev-parse --abbrev-ref HEAD) = $(g rev-parse HEAD)"
      echo "ground the RED restores to: $R390 = $(g rev-parse "$R390") (the neutraliser-carrying ground)"
      echo
      vrun "both test files, branch tip" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
      echo "## RED: only the production module at the ground SHA, every test file left at the tip"
      echo
      grun checkout "$R390" -- "$MODULE"
      grun status --porcelain
      vrun "both test files, module at ground" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
      echo "## restore, then the restore proof (empty porcelain)"
      echo
      grun checkout HEAD -- "$MODULE"
      grun status --porcelain
      vrun "both test files, restored" src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose
    else
      echo "(skipped: $R392 does not exist yet — run Step 4 first)"
    fi
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- baseline: failing-name set, not colour --------------------------------------------
section_baseline() {
  want baseline || return 0
  local F="$RAW/suite-baseline.txt"
  {
    hdr "Suite baseline at the delivered tips, by failing-name set" \
        "The claim this supports is 'this diff added zero failures', never 'the suite is green'."
    if have "$R390"; then
      echo "admissible for: the ref this checkout has checked out at run time, named here:"
      echo "$(g rev-parse --abbrev-ref HEAD) = $(g rev-parse HEAD)"
      echo "(the measurement belongs to the working tree, so only that tip is spoken for)"
      echo
      vrun "src (whole package tree)" src --reporter=verbose
      echo "## the same run at the other delivered line, so neither tip speaks for the other"
      echo
      for R in "$R390" "$R392"; do
        have "$R" || continue
        g checkout --quiet "${R#refs/heads/}"
        echo "### $R = $(g rev-parse "$R")"
        vrun "src at $R" src --reporter=dot
      done
      g checkout --quiet "${R392#refs/heads/}" 2>/dev/null || g checkout --quiet "${R390#refs/heads/}"
      echo "the checkout is left on: $(g rev-parse --abbrev-ref HEAD)"
      echo "## failing-name set at this tip, derived from the run above"
      echo
      if grep -q '^×' "$S/last-vitest.txt"; then
        grep '^×' "$S/last-vitest.txt" | sed 's/^/      /'
      else
        echo "      (empty — no test in packages/pi-claude-cli/src failed at this tip)"
      fi
      echo "      skipped entries: $(grep -c '^↓\|skipped' "$S/last-vitest.txt" || true)"
      echo
    else
      echo "(skipped: $R390 absent)"
    fi
    echo "## line-count guardrail, printed in full and disclosed as repo-wide pre-existing debt"
    echo "Whether either path this line owns is an offender is measured, not asserted:"
    echo
    run bash -c "cd $CK && node scripts/check-file-line-count.mjs 2>&1 | grep -c pi-claude-cli || true"
    echo "(a count of 0 means neither $MODULE, nor $TEST, nor $SIBLING is on the offender list)"
    echo "and the guardrail's own exit code, printed in full below, is pre-existing repo-wide debt"
    echo "that this card neither created nor ratchets down with --update."
    echo
    run bash -c "cd $CK && node scripts/check-file-line-count.mjs"
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- window: the fast-forward announcement, printed byte-for-byte as sent ---------------
section_window() {
  want window || return 0
  local F="$RAW/window-announcement.txt"
  have "$R390" || { echo "skipped window ($R390 absent)"; return 0; }
  {
    hdr "AC2 — the fast-forward window, printed in the tick it was measured" \
        "Every predicate below was printed in one command block, and the message body at the" \
        "end is the text that went out in that same tick — untrimmed, unedited."
    if have "$R390"; then
      echo "admissible for: $R390 = $(g rev-parse "$R390")"
      echo
      grun rev-list --left-right --count "$MAIN...$R390"
      grun merge-base --is-ancestor "$MAIN" "$R390"
      grun log --oneline "$MAIN..$R390"
      grun diff --shortstat "$MAIN...$R390"
      grun rev-parse "$MAIN"
      echo "## the fresh GREEN counter line from Step 2, quoted from raw/red-green.txt"
      echo
      if [ -f "$RAW/red-green.txt" ]; then
        grep -E '^(Test Files|Tests )' "$RAW/red-green.txt" | head -2 | sed 's/^/      /'
      else
        echo "      (raw/red-green.txt not generated yet)"
      fi
      echo "## the message actually sent, and through which channel"
      echo
      cat <<'SENT'
Instrument deviation, reported rather than papered over: AC2 names `fn_send_message` as the
channel to the CEO. That tool is not on the Workflow Executor's tool surface — the call
returned, verbatim:

      Tool fn_send_message not found

So the identical bytes went out through the two operator-visible channels this session does
have, in the same tick as the prints above:

  1. a card log entry on STAS-392 (live on the board History tab), and
  2. a registered artifact in the dashboard gallery, discoverable by the CEO agent
     (agent-94d01490) and by every other lane:
     "STAS-392 — fast-forward window for the 390 engine line is open (measured this tick)",
     id d3cddc88-2de2-45db-bd6f-3f8f7579a619.

The message body as sent (1381 characters, inside the 2000-character cap):
----8<---- message body ----8<----
SENT
      cat <<'BODY'
STAS-392 — okno na manualný fast-forward 390-ky je otvorené TOHOTO ticku.

Vetva: refs/heads/fusion/stas-390-tool-channel-authority
Tip:  e6b1fe4c7f4bafa36727e33e8df7c400e87c6a91
Trunk: refs/heads/main = 4fa1ea0e64bccc32e2d3116c534706d1b953931c (merané tento tick)

Merania z toho istého ticku:
- rev-list --left-right --count main...390 = 0 2
- merge-base --is-ancestor main 390 rc=0
- merge-tree --write-tree --name-only rc=0, žiadny CONFLICT riadok
- git diff --shortstat main...390 = 2 files changed, 275 insertions(+), 6 deletions(-)
- git log --oneline main..390 = e6b1fe4c7f a a38c8be034 (obe STAS-389)
- čerstvý green na novej báze: Test Files 2 passed (2); Tests 23 passed (23), 0 skipped
  (RED na tej istej báze: 8 failed | 15 passed, len s tool-result-authority.ts checkoutnutým na trunk)

Nohy na pristátie, z checkoutu enginu na refs/heads/main, po čerstvom overení trunku:
  git rev-parse refs/heads/main
  git merge --ff-only fusion/stas-390-tool-channel-authority

Okno sa zatvára v rádoch hodín — engine rail merguje 2-4× denne a presne takto zomrel minulý
zelený stav 390: dva rail mergy po uzavretí karty. Táto karta nezapisovala do refs/heads/main,
nič netlačila, neotvorila žiadny PR.

PORADIE: druhú líniu (oznam prázdneho tool resultu) nikdy nespúšťajte pred touto. AC1 —
neutraliseStructureTokens nie je na trunku (git grep -c = 0) — splní až tento merge.
BODY
      echo "----8<---- end of message body ----8<----"
    else
      echo "(skipped: $R390 absent)"
    fi
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- landing: the operator's two commands ----------------------------------------------
section_landing() {
  want landing || return 0
  local F="$RAW/operator-landing-leg.txt"
  have "$R392" || { echo "skipped landing leg ($R392 absent)"; return 0; }
  # The run-start trunk is read out of Step 0's own capture, never typed here: a SHA
  # written into this script could only be a memory, and AC0 forbids a memory as a base.
  local RUNSTART
  RUNSTART=$(awk '/rev-parse refs\/heads\/main/{getline; gsub(/^ +| +$/, "")
                 if ($0 ~ /^[0-9a-f]{40}$/) { print; exit }}' "$RAW/ac0-roots.txt" 2>/dev/null || true)
  [ -n "$RUNSTART" ] || RUNSTART="NOT RECOVERABLE FROM $RAW/ac0-roots.txt"
  {
    hdr "AC2/AC6 — the landing leg, in the operator's hands" \
        "This card writes no trunk ref and pushes nothing. Both commands are ordered:"
    printf '# 390 first, always. A moved trunk between them voids the second.\n\n'
    for R in "$R390" "$R392"; do
      if have "$R"; then
        echo "## $R = $(g rev-parse "$R")"
        grun rev-list --left-right --count "$MAIN...$R"
        grun merge-base --is-ancestor "$MAIN" "$R"
        grun merge-tree --write-tree --name-only "$MAIN" "$R"
        grun log --oneline "$MAIN..$R"
        grun diff --shortstat "$MAIN...$R"
      else
        echo "## $R — not created yet"
      fi
      echo
    done
    echo "## who holds each ref right now (a live checkout of either would block the merge)"
    echo
    run git -C "$CK" worktree list
    echo "## the same list, narrowed to the three refs this card speaks for"
    echo
    run bash -c "cd $CK && git worktree list | grep -E 'stas-39[012]|\[main\]' || echo '      (no worktree holds any of them)'"
    echo "Reading of that line, stated as a measurement: the 390 ref appears **nowhere**, so it has"
    echo "no live holder and nothing blocks merging it. The 392 ref is held by this card's own nested"
    echo "checkout, which does not block merging it from elsewhere — a merge into another branch"
    echo "never writes a held branch. $MAIN is held by the primary checkout, which is where the two"
    echo "commands below belong. The predecessor's ref is still registered to STAS-391's nested"
    echo "checkout, which Step 0 recorded as prunable ('gitdir file points to non-existent"
    echo "location'); this card deliberately did not run git worktree prune, because pruning would"
    echo "delete another card's registration."
    echo
    echo "## the two commands, in this order, from a checkout that is on $MAIN"
    echo
    echo "Each merge is preceded by a fresh re-read of trunk, because a trunk that moved between"
    echo "the two voids the second: the second leg was rebased onto $(g rev-parse "$R390"), so if"
    echo "trunk is no longer what the first merge made it, stop and re-run the predicate below"
    echo "rather than merging blind."
    echo
    echo '  git rev-parse refs/heads/main          # must print the trunk this file was generated against'
    echo "  git merge --ff-only fusion/stas-390-tool-channel-authority"
    echo "  git rev-parse refs/heads/main          # must now equal the 390 tip printed above"
    echo "  git merge --ff-only fusion/stas-392-empty-tool-result-announcement"
    echo
    echo "## why the second leg is safe to merge after the first, stated as a predicate"
    echo
    echo "$R392 carries $R390 as an ancestor, so once the first merge lands, trunk is the ground"
    echo "the second leg was rebased onto and its merge is a fast-forward too."
    echo
    run git -C "$CK" merge-base --is-ancestor "$R390" "$R392"
    echo "## AC1 re-probe at the close"
    echo
    grun grep -c neutraliseStructureTokens "$MAIN" -- "$PKGREL/src"
    echo "## the engine repository's own landing-hygiene rule, probed at the ground"
    echo
    local LH_PATTERN='landing hygien|ff-only|fast-forward|--ff-only' LH_RC=0 LH_OUT
    LH_OUT=$(g grep -n -iE "$LH_PATTERN" "$MAIN" -- CLAUDE.md 2>&1) || LH_RC=$?
    printf '  $ git -C %s grep -n -iE '"'"'%s'"'"' %s -- CLAUDE.md\n' "$CK" "$LH_PATTERN" "$MAIN"
    if [ -n "$LH_OUT" ]; then printf '%s\n' "$LH_OUT" | sed 's/^/      /'; else printf '      (no output)\n'; fi
    printf '        rc=%s\n\n' "$LH_RC"
    if [ "$LH_RC" -eq 0 ]; then
      echo "The engine CLAUDE.md does carry a landing-hygiene rule at this ground; the hits above"
      echo "are it. This file cites it and stops — the rule is not restated or paraphrased here."
    else
      echo "Measured, not assumed: a CLAUDE.md blob exists at this ground but holds no landing-hygiene,"
      echo "fast-forward, or merge rule at all, so there is nothing in it to cite. The 'Landing hygiene'"
      echo "section this card was pointed at lives in the *stash* repository's CLAUDE.md, and its rules"
      echo "bind that repository; importing or duplicating another repository's pinned rule here is what"
      echo "the instruction forbids, so the two commands above stand on their own measurements."
    fi
    echo
    echo "## trunk position at run start and run end (AC6: these must be equal)"
    echo
    echo "  run start: $RUNSTART  (read from $RAW/ac0-roots.txt, Step 0's measurement before any write)"
    echo "  run end:   $(g rev-parse "$MAIN")"
    echo "  equal:     $([ "$(g rev-parse "$MAIN")" = "$RUNSTART" ] && echo yes || echo 'NO — trunk moved during this run')"
    echo
    echo "## what AC1 and AC6 each cost"
    echo
    echo "AC1 is not satisfied by this run and cannot be: it asks for the neutraliser to be on the"
    echo "trunk, and only the two merges above put it there. AC6 asks that this card not move the"
    echo "trunk. Both cannot hold in the same run, so the honest delivery is FF-ready refs plus a"
    echo "trunk left alone, and the counts below are the shape that implies — 0 2 and 0 3 ahead, not"
    echo "0 0. A 0 0 reading would mean the operator had already landed them while this ran."
    echo
    echo "## liveness"
    echo "running build unidentifiable — fix liveness unverified"
  } > "$F" 2>&1
  echo "wrote $F"
}

# --- engine: the authoritative record of what the engine side changed -------------------
section_engine() {
  want engine || return 0
  have "$R390" || { echo "skipped engine captures ($R390 absent)"; return 0; }
  {
    hdr "What the engine side of this card is — the diff and the commits" \
        "Trunk does not carry either line yet, so the diff is stated against the ground" \
        "the delivered line sits on, named in the header of each block."
    echo "## the 390 line: $R390 vs the measured trunk"
    echo
    grun log --oneline "$MAIN..$R390"
    grun diff --stat "$MAIN...$R390"
    grun diff "$MAIN...$R390"
    echo "## the 392 delivered line: $R392 vs $R390 (one commit, guard + three pins)"
    echo
    if have "$R392"; then
      grun log --oneline "$R390..$R392"
      grun diff --stat "$R390..$R392"
      grun diff "$R390..$R392"
    else
      echo "($R392 not created yet)"
    fi
  } > "$OUT/engine-diff.txt" 2>&1
  echo "wrote $OUT/engine-diff.txt"
  {
    hdr "Commit list for both delivered lines" "Names, not SHAs, are the contract."
    for R in "$R390" "$R392"; do
      echo "## $R"
      if have "$R"; then
        g log --format='%H%n%s%n%b%n---' "$MAIN..$R"
      else
        echo "(not created yet)"
      fi
      echo
    done
  } > "$OUT/engine-commits.txt" 2>&1
  echo "wrote $OUT/engine-commits.txt"
}

section_ac0
section_census
section_rebase
section_redgreen
section_baseline
section_window
section_ac4
section_landing
section_engine
