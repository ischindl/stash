#!/usr/bin/env bash
# STAS-393 - regenerate every evidence capture of this card.
#
# The card authored no product code; its deliverable is this re-runnable verdict. Run it
# from anywhere; it resolves its own card worktree. Requires the nested engine checkout
# (created here if absent) and, for the suite captures, `pnpm install --frozen-lockfile
# --ignore-scripts` at that checkout's root (done here if node_modules is missing).
#
# usage: bash stas-393-engine-evidence/generate.sh [ac0|before|rebase|redgreen|pins|window|after|all]
#
# Refs, not SHAs, are the contract below. Nothing here writes refs/heads/main, pushes, or
# opens a pull request; the fast-forward is the operator's leg.
set -u

CR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENG="$CR/.engine/stas-393-engine"
RAW="$CR/stas-393-engine-evidence/raw"
TMP="$CR/.fusion-tmp"
PKG="$ENG/packages/pi-claude-cli"
CARRIED=refs/heads/fusion/stas-392-empty-tool-result-announcement
DELIVERED=refs/heads/fusion/stas-393-tool-result-stack
NEUTRAL=refs/heads/fusion/stas-390-tool-channel-authority   # 389 tip inside the carried stack
mkdir -p "$RAW" "$TMP"

# ---------------------------------------------------------------- helpers --
q()    { printf '\n  $ %s\n' "$*"; }
emit() {                      # run, indent output, print rc
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [ -z "$out" ]; then printf '      (no output)\n'
  else printf '%s\n' "$out" | sed 's/^/      /'; fi
  printf '        rc=%d\n' "$rc"
}
git_e() { git -C "$ENG" "$@"; }
hdr() {
  echo "# STAS-393 - $1"
  echo "# generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# produced by stas-393-engine-evidence/generate.sh ($2)"
}

provision() {
  [ -d "$ENG/.git" ] || [ -f "$ENG/.git" ] || {
    E=$HOME/git/Fusion
    git --git-dir="$E/.git" worktree add --detach "$ENG" refs/heads/main
  }
  [ -x "$PKG/node_modules/.bin/vitest" ] || {
    (cd "$ENG" && pnpm install --frozen-lockfile --ignore-scripts) 2>&1 | tail -5
  }
}

# ------------------------------------------------------------- ac0 roots --
cap_ac0() {
  provision
  {
    hdr "AC0 - the ground, measured inside the run from the nested engine checkout" \
        "capture: ac0"
    echo "# No memorised SHA appears below; every value was printed by the command above it."
    echo "# Checkout: $ENG - a *linked* worktree of the engine repo."
    echo "# The primary checkout holds refs/heads/main, which is where the operator's"
    echo "# fast-forward must run and what this card is forbidden to move."

    echo; echo "## the checkout is the engine repo and not its parent (toplevel + tracked-file probe)"
    q git -C "$ENG" rev-parse --show-toplevel;               emit git_e rev-parse --show-toplevel
    q git -C "$ENG" ls-files --error-unmatch packages/pi-claude-cli/src/tool-result-authority.ts
    emit git_e ls-files --error-unmatch packages/pi-claude-cli/src/tool-result-authority.ts

    echo; echo "## live trunk"
    q git -C "$ENG" rev-parse refs/heads/main;               emit git_e rev-parse refs/heads/main
    q git -C "$ENG" rev-parse --short refs/heads/main;       emit git_e rev-parse --short refs/heads/main
    q git -C "$ENG" status --porcelain \| wc -l
    printf '      %s\n' "$(git_e status --porcelain | grep -c '' || true)"

    echo; echo "## worktree occupancy, derived live - who holds refs/heads/main and each carried ref"
    q git -C "$ENG" worktree list --porcelain \| awk '/^worktree /{w=\$2} /^branch /{print w\"  \"\$2}' \| grep -E 'refs/heads/main\$|stas-38[89]|stas-39'
    git_e worktree list --porcelain | awk '/^worktree /{w=$2} /^branch /{print "      "w"  "$2}' \
      | grep -E 'refs/heads/main$|stas-38[89]|stas-39'
    echo "      (a carried ref absent from this list has no live holder, so a new branch name"
    echo "       is required for the restack: git refuses to rebase a branch a worktree holds)"

    echo; echo "## carried-ref name DISCOVERED by for-each-ref (a spec's branch name is not a measurement)"
    q git -C "$ENG" for-each-ref --format='%(refname) %(objectname:short) %(subject)' refs/heads/fusion/ \| grep stas-3
    git_e for-each-ref --format='%(refname) %(objectname:short) %(subject)' refs/heads/fusion/ \
      | grep 'stas-3' | sed 's/^/      /'

    echo; echo "## the carried stack and its position against trunk"
    q git -C "$ENG" log --oneline "$CARRIED" \| head -6
    git_e log --oneline "$CARRIED" | head -6 | sed 's/^/      /'
    q git -C "$ENG" merge-base refs/heads/main "$CARRIED";   emit git_e merge-base refs/heads/main "$CARRIED"
    q git -C "$ENG" rev-list --left-right --count "refs/heads/main...$CARRIED"
    emit git_e rev-list --left-right --count "refs/heads/main...$CARRIED"
    echo "      (three dots is the only divergence predicate; rev-list A B without dots is a union)"
    q git -C "$ENG" merge-base --is-ancestor refs/heads/main "$CARRIED"
    emit git_e merge-base --is-ancestor refs/heads/main "$CARRIED"
    echo "      (both directions printed whenever an rc=1 appears; rc=1 alone is ambiguous)"
    q git -C "$ENG" merge-base --is-ancestor "$CARRIED" refs/heads/main
    emit git_e merge-base --is-ancestor "$CARRIED" refs/heads/main

    echo; echo "## conflict predicate (rc=0 with no CONFLICT line = a clean POSITION, remedy is mechanical)"
    mt=$(git_e merge-tree --write-tree --name-only refs/heads/main "$CARRIED" 2>"$TMP/mt-ac0.err"); mt_rc=$?
    printf '%s\n' "$mt" | sed 's/^/      /'
    printf '        rc=%d\n' "$mt_rc"
    printf '      CONFLICT line count = %s\n' "$(printf '%s\n' "$mt" | grep -c 'CONFLICT' || true)"
    [ -s "$TMP/mt-ac0.err" ] && { echo '      stderr NOT empty:'; sed 's/^/      /' "$TMP/mt-ac0.err"; } \
                            || echo '      stderr empty (0 bytes)'

    echo; echo "## git cherry -v (a '+' line is expected and is not a landing predicate)"
    emit git_e cherry -v refs/heads/main "$CARRIED"

    echo; echo "## AC1 gap proof - the canary pair, same command shape on both symbols"
    q git -C "$ENG" grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src
    emit git_e grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src
    q git -C "$ENG" grep -c ZERO_BYTES_NOTICE refs/heads/main -- packages/pi-claude-cli/src
    emit git_e grep -c ZERO_BYTES_NOTICE refs/heads/main -- packages/pi-claude-cli/src
    echo "      (zero for the first beside non-zero for the second is a real gap, not a malformed command)"
  } > "$RAW/ac0-roots.txt" 2>&1
  echo "wrote $RAW/ac0-roots.txt"
}

# ------------------------------------------------------ suite snapshot ----
run_suite() {                    # run_suite <label> <out-json> [testPath]
  local label="$1" out_json="$2" testpath="${3:-src}" raw log sha ref porcelain vt rc
  raw="$TMP/$label.vitest.json"; log="$TMP/$label.vitest.log"
  sha=$(git_e rev-parse HEAD); ref=$(git_e rev-parse --abbrev-ref HEAD)
  porcelain=$(git_e status --porcelain | grep -c '' || true)
  vt=$( (cd "$PKG" && NO_COLOR=1 ./node_modules/.bin/vitest --version) 2>&1 | head -1)
  [ -x "$PKG/node_modules/.bin/vitest" ] || { echo "vitest binary missing: run pnpm install first" >&2; return 3; }
  ( cd "$PKG" && rm -f "$raw" && NO_COLOR=1 ./node_modules/.bin/vitest run "$testpath" \
      --reporter=verbose --reporter=json --outputFile.json="$raw" > "$log" 2>&1 )
  rc=$?
  node - "$raw" "$log" "$out_json" "$label" "$ref" "$sha" "$porcelain" "$vt" "$testpath" "$rc" <<'NODE'
const fs = require('fs');
const [raw, log, out, label, ref, sha, porcelain, vitestVersion, testpath, rc] = process.argv.slice(2);
const j = JSON.parse(fs.readFileSync(raw, 'utf8'));
const perFile = {}, failing = [];
for (const tr of j.testResults || []) {
  const file = (tr.name || '').replace(/^.*\/packages\/pi-claude-cli\//, '');
  perFile[file] = (tr.assertionResults || []).length;
  for (const a of tr.assertionResults || [])
    if (a.status === 'failed') failing.push(file + ' :: ' + (a.fullName || a.title));
}
const summary = {};
for (const line of fs.readFileSync(log, 'utf8').split('\n')) {
  const m = line.trim().match(/^(Test Files|Tests|Errors|Unhandled Errors|Duration)\b(.*)$/);
  if (m) summary[m[1]] = m[2].trim();
}
const all = (j.testResults || []).flatMap(t => t.assertionResults || []);
const snap = {
  card: 'STAS-393', label,
  measuredAtUtc: new Date().toISOString(),
  engineRepo: '/home/schindler/git/Fusion (nested linked worktree .engine/stas-393-engine)',
  measuredRef: ref, measuredSha: sha,
  workingTreePorcelainLines: Number(porcelain),
  command: `(cd packages/pi-claude-cli && NO_COLOR=1 ./node_modules/.bin/vitest run ${testpath} --reporter=verbose --reporter=json --outputFile.json=<transient raw>)`,
  vitestVersion, exitCode: Number(rc),
  counters: { ...summary, assertionResultsFromJson: {
    total: all.length,
    passed: all.filter(a => a.status === 'passed').length,
    skipped: all.filter(a => ['skipped','pending','todo'].includes(a.status)).length,
    failed: failing.length } },
  perFileTestCounts: perFile,
  failingTestNames: failing.sort(),
};
fs.writeFileSync(out, JSON.stringify(snap, null, 2) + '\n');
console.log('wrote ' + out);
console.log('measuredSha=' + sha + ' ref=' + ref + ' exit=' + rc);
console.log('counters=' + JSON.stringify(summary));
console.log('failing=' + failing.length + ' files=' + Object.keys(perFile).length);
NODE
  echo "suite_rc=$rc"
}

cap_before() {
  provision
  echo "# STAS-393 - AC0b - baseline captured at the freshly measured trunk tip"
  echo "# In Step 0 this ran before any ref of this card existed. On regeneration it detaches"
  echo "# the working tree at refs/heads/main, because measuring whatever HEAD happens to be"
  echo "# checked out would silently put the stack's own test counts into the baseline."
  local prev_ref prev_sha trunk
  prev_ref=$(git_e rev-parse --abbrev-ref HEAD)
  prev_sha=$(git_e rev-parse HEAD)
  trunk=$(git_e rev-parse refs/heads/main)
  if [ -n "$(git_e status --porcelain)" ]; then
    echo "REFUSING: the engine working tree is dirty, refusing to switch it" >&2
    exit 1
  fi
  echo "      working tree was at $prev_ref ($prev_sha); detaching at $trunk to measure"
  git_e switch --detach refs/heads/main || { echo "REFUSING: detach failed" >&2; exit 1; }
  [ "$(git_e rev-parse HEAD)" = "$trunk" ] || { echo "REFUSING: HEAD is not the trunk tip" >&2; exit 1; }
  run_suite before-suite "$CR/stas-393-engine-evidence/before-suite.json" src
  if [ "$prev_ref" = "HEAD" ]; then
    git_e switch --detach "$prev_sha" >/dev/null 2>&1
  else
    git_e switch "$prev_ref" >/dev/null 2>&1
  fi
  echo "      working tree restored to $prev_ref ($(git_e rev-parse --short HEAD))"
}

cap_after() {
  provision
  local want
  want=$(git_e rev-parse "$DELIVERED")
  if [ "$(git_e rev-parse HEAD)" != "$want" ]; then
    echo "REFUSING: the after-suite snapshot must be measured at the delivered tip." >&2
    echo "  HEAD is $(git_e rev-parse HEAD), the delivered tip is $want." >&2
    echo "  Check the tree out (git -C $ENG switch ${DELIVERED#refs/heads/}) and re-run." >&2
    exit 1
  fi
  run_suite after-suite "$CR/stas-393-engine-evidence/after-suite.json" src
  echo
  echo "## failing-name set difference, both directions (both must be empty)"
  node - "$CR/stas-393-engine-evidence/before-suite.json" "$CR/stas-393-engine-evidence/after-suite.json" <<'NODE'
const fs = require('fs');
const b = new Set(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')).failingTestNames);
const a = new Set(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')).failingTestNames);
const onlyA = [...a].filter(x => !b.has(x));
const onlyB = [...b].filter(x => !a.has(x));
console.log('added by the stack (after minus before): ' + JSON.stringify(onlyA));
console.log('removed by the stack (before minus after): ' + JSON.stringify(onlyB));
NODE
  echo
  echo "## node scripts/check-file-line-count.mjs, in full (pre-existing repo-wide debt, red at trunk)"
  ( cd "$ENG" && node scripts/check-file-line-count.mjs ) 2>&1 | sed 's/^/      /'
  echo "        rc reported above belongs to that command; either path this stack owns appears only if listed"
  q git -C "$ENG" diff --name-only "$(git_e merge-base refs/heads/main "$DELIVERED")..$DELIVERED"
  emit git_e diff --name-only "$(git_e merge-base refs/heads/main "$DELIVERED")..$DELIVERED"
}

# --------------------------------------------------------- rebase proof ---
cap_rebase() {
  provision
  {
    hdr "AC2/AC6/AC7 - the stack moved by rebase alone, authoring no code, in the only honest order" \
        "capture: rebase"
    old_tip=$(git_e rev-parse "$CARRIED")
    new_tip=$(git_e rev-parse "$DELIVERED")
    mb=$(git_e merge-base refs/heads/main "$DELIVERED")

    echo "## ordering proven BEFORE anything moves (AC7) - the announcement must sit on top"
    q git -C "$ENG" log --oneline "$CARRIED" \| head -4
    git_e log --oneline "$CARRIED" | head -4 | sed 's/^/      /'
    q git -C "$ENG" merge-base --is-ancestor "$NEUTRAL" "$CARRIED"
    emit git_e merge-base --is-ancestor "$NEUTRAL" "$CARRIED"
    echo "      (rc=0 means both STAS-389 commits are ancestors of the announcement commit;"
    echo "       a stack carrying the announcement alone must never be delivered)"

    echo; echo "## conflict predicate BEFORE the rebase"
    mt=$(git_e merge-tree --write-tree --name-only refs/heads/main "$CARRIED" 2>"$TMP/mt-before.err"); rc=$?
    printf '%s\n' "$mt" | sed 's/^/      /'; printf '        rc=%d\n' "$rc"
    printf '      CONFLICT line count = %s\n' "$(printf '%s\n' "$mt" | grep -c 'CONFLICT' || true)"
    [ -s "$TMP/mt-before.err" ] && { echo '      stderr NOT empty:'; sed 's/^/      /' "$TMP/mt-before.err"; } \
                               || echo '      stderr empty (0 bytes)'

    echo; echo "## the two commands this card ran (no --force; a NEW name, never a predecessor ref)"
    echo "      git -C .engine/stas-393-engine switch -c fusion/stas-393-tool-result-stack $CARRIED"
    echo "      git -C .engine/stas-393-engine rebase refs/heads/main"
    echo "      PRE  $old_tip   (the tip carried by $CARRIED)"
    echo "      POST $new_tip   (this card's delivered tip)"
    echo "      onto $(git_e rev-parse refs/heads/main)"
    echo "      merge-base $mb"
    echo
    echo "## git own record of the restack (branch reflog), plus the transcript this run kept"
    q git -C "$ENG" reflog show fusion/stas-393-tool-result-stack
    emit git_e reflog show fusion/stas-393-tool-result-stack
    if [ -f "$TMP/rebase-transcript.txt" ]; then
      echo "      --- transcript this run recorded ---"
      sed 's#^#      #' "$TMP/rebase-transcript.txt"
    fi

    echo; echo "## no-authored-code proof, part 1 - scoped diff over the paths the stack owns"
    q git -C "$ENG" diff --stat "$old_tip" "$new_tip" -- packages/pi-claude-cli/src/tool-result-authority.ts packages/pi-claude-cli/src/__tests__/
    emit git_e diff --stat "$old_tip" "$new_tip" -- packages/pi-claude-cli/src/tool-result-authority.ts packages/pi-claude-cli/src/__tests__/
    echo "      (empty = this card authored nothing inside the engine paths it carries)"

    echo; echo "## no-authored-code proof, part 2 - range-diff; every carried commit must map to '='"
    q git -C "$ENG" range-diff "$mb" "$old_tip" "$new_tip"
    emit git_e range-diff "$mb" "$old_tip" "$new_tip"
    echo "      (a '>' record with an empty left side is a trunk commit that moved under the stack,"
    echo "       not a mismatch; a '!' against a commit this card did not author would stop the card)"

    echo; echo "## conflict predicate AFTER the rebase (an identical result tree restates the same fact)"
    mt2=$(git_e merge-tree --write-tree --name-only refs/heads/main "$DELIVERED" 2>"$TMP/mt-after.err"); rc2=$?
    printf '%s\n' "$mt2" | sed 's/^/      /'; printf '        rc=%d\n' "$rc2"
    printf '      CONFLICT line count = %s\n' "$(printf '%s\n' "$mt2" | grep -c 'CONFLICT' || true)"
    [ -s "$TMP/mt-after.err" ] && { echo '      stderr NOT empty:'; sed 's/^/      /' "$TMP/mt-after.err"; } \
                              || echo '      stderr empty (0 bytes)'
    echo "      result tree before = $(printf '%s\n' "$mt" | head -1)"
    echo "      result tree after  = $(printf '%s\n' "$mt2" | head -1)"

    echo; echo "## collision surface, printed ONLY in the scoped form (what the branch ADDS)"
    q git -C "$ENG" diff --name-only "$mb..$DELIVERED"
    git_e diff --name-only "$mb..$DELIVERED" | sed 's/^/      /'
    echo "      (never judged from refs/heads/main..branch: before a rebase that also pushes the"
    echo "       reverse delta of everything trunk added since the merge-base)"

    echo; echo "## stack shape (AC7) - exactly three commits, neutraliser below announcement"
    q git -C "$ENG" log --oneline "refs/heads/main..$DELIVERED"
    git_e log --oneline "refs/heads/main..$DELIVERED" | sed 's/^/      /'

    echo; echo "## predecessor refs unmoved (AC6)"
    for r in "$NEUTRAL" "$CARRIED" refs/heads/fusion/stas-388-tool-result-authority refs/heads/fusion/stas-389-tool-channel-authority refs/heads/fusion/stas-391-empty-tool-result-announcement; do
      printf '      %-64s %s\n' "$r" "$(git_e rev-parse --short "$r")"
    done
    echo "      (the three 38x/39x tips equal the values printed in raw/ac0-roots.txt for the same refs)"
  } > "$RAW/rebase-proof.txt" 2>&1
  echo "wrote $RAW/rebase-proof.txt"
}

# ------------------------------------------------------------- red-green --
cap_redgreen() {
  provision
  {
    hdr "AC3 - one fresh red-green pair on the new base; nothing recycled from a sibling card" \
        "capture: redgreen"
    tip=$(git_e rev-parse "$DELIVERED")
    echo "GREEN/RED admissible for: $DELIVERED = $tip"
    echo "# Test files are never checked out: tool-channel-authority.test.ts does not exist at"
    echo "# trunk, and restoring it would delete the assertion and leave a run with no test file."

    echo; echo "## GREEN at the delivered tip, both files named, no -t filter anywhere"
    q "(cd packages/pi-claude-cli && NO_COLOR=1 ./node_modules/.bin/vitest run src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts --reporter=verbose)"
    ( cd "$PKG" && NO_COLOR=1 ./node_modules/.bin/vitest run \
        src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts \
        --reporter=verbose ) > "$TMP/green.log" 2>&1; g_rc=$?
    sed 's/^/      /' "$TMP/green.log"
    printf '        rc=%d\n' "$g_rc"

    echo; echo "## RED - only the production module restored to the trunk SHA measured in this run"
    trunk=$(git_e rev-parse refs/heads/main)
    q git -C "$ENG" checkout "$trunk" -- packages/pi-claude-cli/src/tool-result-authority.ts
    emit git_e checkout "$trunk" -- packages/pi-claude-cli/src/tool-result-authority.ts
    q git -C "$ENG" status --porcelain
    emit git_e status --porcelain
    echo "      (the restore is proven by the next two prints: the failing run, then an empty"
    echo "       porcelain after the restore back to the branch tip)"
    q "(cd packages/pi-claude-cli && NO_COLOR=1 ./node_modules/.bin/vitest run both test files, module at trunk)"
    ( cd "$PKG" && NO_COLOR=1 ./node_modules/.bin/vitest run \
        src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts \
        --reporter=verbose ) > "$TMP/red.log" 2>&1; r_rc=$?
    sed 's/^/      /' "$TMP/red.log"
    printf '        rc=%d\n' "$r_rc"
    echo "## the module blob in the working tree during RED is the TRUNK blob, not the fixed one"
    q git -C "$ENG" rev-parse "$trunk:packages/pi-claude-cli/src/tool-result-authority.ts"
    emit git_e rev-parse "$trunk:packages/pi-claude-cli/src/tool-result-authority.ts"
    q git -C "$ENG" hash-object packages/pi-claude-cli/src/tool-result-authority.ts
    emit git_e hash-object packages/pi-claude-cli/src/tool-result-authority.ts

    echo; echo "## restore to the branch tip, then repeat GREEN"
    q git -C "$ENG" checkout HEAD -- packages/pi-claude-cli/src/tool-result-authority.ts
    emit git_e checkout HEAD -- packages/pi-claude-cli/src/tool-result-authority.ts
    q git -C "$ENG" status --porcelain
    emit git_e status --porcelain
    q "(cd packages/pi-claude-cli && NO_COLOR=1 ./node_modules/.bin/vitest run both test files, restored)"
    ( cd "$PKG" && NO_COLOR=1 ./node_modules/.bin/vitest run \
        src/__tests__/tool-channel-authority.test.ts src/__tests__/tool-result-authority.test.ts \
        --reporter=verbose ) > "$TMP/green2.log" 2>&1; g2_rc=$?
    sed 's/^/      /' "$TMP/green2.log"
    printf '        rc=%d\n' "$g2_rc"
    echo "## the blob after the restore is the delivered-tip blob again"
    q git -C "$ENG" hash-object packages/pi-claude-cli/src/tool-result-authority.ts
    emit git_e hash-object packages/pi-claude-cli/src/tool-result-authority.ts
    q git -C "$ENG" rev-parse "$DELIVERED:packages/pi-claude-cli/src/tool-result-authority.ts"
    emit git_e rev-parse "$DELIVERED:packages/pi-claude-cli/src/tool-result-authority.ts"
    echo "# git stash used nowhere in this card."
  } > "$RAW/red-green.txt" 2>&1
  echo "wrote $RAW/red-green.txt"
}

# ----------------------------------------------------------- pin census ---
cap_pins() {
  provision
  {
    hdr "AC4 - the three pins re-censused on the new ground, on the delivered tip and on trunk" \
        "capture: pins"
    echo "# Located by title, never by line number, because line numbers moved when trunk moved."
    echo "# Classification is read off the assertion, never off token presence."
    for ref in "$DELIVERED" refs/heads/main; do
      echo; echo "## $ref  (tip $(git_e rev-parse --short "$ref"))"
      q git -C "$ENG" grep -n 'no content to mark' "$ref" -- packages/pi-claude-cli
      emit git_e grep -n 'no content to mark' "$ref" -- packages/pi-claude-cli
      q git -C "$ENG" grep -n 'no payload was delivered' "$ref" -- packages/pi-claude-cli
      emit git_e grep -n 'no payload was delivered' "$ref" -- packages/pi-claude-cli
      q git -C "$ENG" grep -n 'promptWithToolResult(\[\])' "$ref" -- packages/pi-claude-cli
      emit git_e grep -n 'promptWithToolResult(\[\])' "$ref" -- packages/pi-claude-cli
      q git -C "$ENG" ls-tree -r --name-only "$ref" -- packages/pi-claude-cli/src/__tests__/tool-channel-authority.test.ts
      emit git_e ls-tree -r --name-only "$ref" -- packages/pi-claude-cli/src/__tests__/tool-channel-authority.test.ts
      echo "      (non-empty output = the sibling test file exists on that ref)"
    done
    echo; echo "## pin (a) the dropped loop - does its list still contain the empty array?"
    echo "# The declaration is one line (const dropped: unknown[] = [...]); a range pattern that"
    echo "# expects a standalone closing bracket matches nothing and prints a false zero, so the"
    echo "# probe greps the declaration itself and asks the question of that line."
    for ref in "$DELIVERED" refs/heads/main; do
      line=$(git_e show "$ref:packages/pi-claude-cli/src/__tests__/tool-result-authority.test.ts" | grep -n 'const dropped')
      printf '      %s\n          %s\n' "$ref" "$line"
      case "$line" in
        *"[undefined, null, [],"*) echo '          => the list DOES contain the empty array: an assertion that content:[] throws' ;;
        *) echo '          => the list does NOT contain the empty array: only the four surviving payloads throw' ;;
      esac
    done
    echo "## pin (b) the emptiness it() - the announced pair versus the old throw assertion"
    for ref in "$DELIVERED" refs/heads/main; do
      printf '      %s :\n' "$ref"
      git_e show "$ref:packages/pi-claude-cli/src/__tests__/tool-result-authority.test.ts" \
        | grep -n -E 'it\("(cannot print the same bytes|announces that no content was delivered)' | sed 's#^#          #' || true
      git_e show "$ref:packages/pi-claude-cli/src/__tests__/tool-result-authority.test.ts" \
        | grep -n -E 'promptWithToolResult\(\[\]\)' | sed 's#^#          #' || true
    done
    echo "## pin (c) the anti-sledgehammer pin in the channel-authority suite (no such file at trunk)"
    for ref in "$DELIVERED" refs/heads/main; do
      printf '      %s :\n' "$ref"
      git_e show "$ref:packages/pi-claude-cli/src/__tests__/tool-channel-authority.test.ts" 2>&1 \
        | grep -n -E 'anti-sledgehammer|it\("delivers an untyped|delivered="zero-bytes"|ZERO_BYTES_NOTICE' | sed 's#^#          #' || true
    done
    echo; echo "## per-hit classification, derived by reading the enclosing it() block"
    echo "# empty-array-must-go = the block asserts that promptWithToolResult([]) throws;"
    echo "# retained = a fail-loud the stack deliberately keeps (unreadable payload throws,"
    echo "#   the four surviving dropped-loop entries, the buildPrompt propagation pin built"
    echo "#   on content: undefined); already-reconciled = the block already states the"
    echo "#   announcement instead. AC4 closes when the delivered tip has zero empty-array-must-go."
    for ref in "$DELIVERED" refs/heads/main; do
      echo "### $ref"
      node - "$ENG" "$ref" <<'CLASSIFIER'
const { execFileSync } = require('child_process');
const [eng, ref] = process.argv.slice(2);
const show = (path) => {
  try { return execFileSync('git', ['-C', eng, 'show', `${ref}:${path}`], { encoding: 'utf8' }); }
  catch { return null; }
};
const files = {
  'src/tool-result-authority.ts': 'src/tool-result-authority.ts',
  'src/__tests__/tool-result-authority.test.ts': 'src/__tests__/tool-result-authority.test.ts',
  'src/__tests__/tool-channel-authority.test.ts': 'src/__tests__/tool-channel-authority.test.ts',
};
const patterns = ['no content to mark', 'no payload was delivered', 'promptWithToolResult([])'];
const rows = [];
for (const rel of Object.values(files)) {
  const text = show(`packages/pi-claude-cli/${rel}`);
  if (text === null) { rows.push({ rel, hit: '-', pattern: '(file absent on this ref)', title: '-', cls: 'n/a' }); continue; }
  const lines = text.split('\n');
  const titles = lines.map((l, i) => ({ i, m: l.match(/^\s*it\("([^"]{0,80})/) })).filter(t => t.m);
  for (const pat of patterns) {
    lines.forEach((l, i) => {
      if (!l.includes(pat)) return;
      const before = titles.filter(t => t.i < i).pop();
      const start = before ? before.i : 0;
      const after = titles.find(t => t.i > start);
      const block = lines.slice(start, after ? after.i : lines.length).join('\n');
      const assertsEmptyThrows = /promptWithToolResult\(\[\]\)/.test(block) && /toThrow/.test(block);
      const droppedList = (block.match(/const dropped: unknown\[\] = (\[[^\n]*\];)/) || [])[1];
      const listHasEmpty = droppedList ? /(^|,)\s*\[\s*\]\s*(,|$)/.test(droppedList) : false;
      let cls;
      if (assertsEmptyThrows || listHasEmpty) cls = 'empty-array-must-go';
      else if (!rel.includes('__tests__')) cls = 'retained';
      else if (/propagates a failure inside the marking step/.test(before ? before.m[1] : '')) cls = 'retained';
      else if (/refuses to render a payload that was never delivered/.test(before ? before.m[1] : '')) cls = 'retained';
      else cls = 'already-reconciled';
      rows.push({
        rel,
        hit: `${i + 1}`,
        pattern: pat,
        title: before ? before.m[1] + '...' : '(module level)',
        cls,
      });
    });
  }
}
for (const r of rows) console.log(`  ${r.rel}:${r.hit} [${r.pattern}] it="${r.title}" => ${r.cls}`);
const counts = rows.reduce((a, r) => (a[r.cls] = (a[r.cls] || 0) + 1, a), {});
console.log('  per-class counts: ' + JSON.stringify(counts));
const bad = rows.filter(r => r.cls === 'empty-array-must-go');
console.log(bad.length === 0
  ? '  VERDICT: zero hits assert that content:[] throws on this ref.'
  : `  VERDICT: ${bad.length} hit(s) still assert that content:[] throws on this ref.`);
CLASSIFIER
    done
    echo; echo "## stack shape re-printed so the census file carries it too (AC7)"
    q git -C "$ENG" log --oneline "refs/heads/main..$DELIVERED"
    git_e log --oneline "refs/heads/main..$DELIVERED" | sed 's/^/      /'
  } > "$RAW/pin-census.txt" 2>&1
  echo "wrote $RAW/pin-census.txt"
}

# ---------------------------------------------------------- window print --
cap_window() {
  provision
  {
    hdr "AC1/AC2/AC5 - the landing window, printed in the tick the message went out" \
        "capture: window"
    echo "## AC2 - the fast-forward predicate at the delivered tip"
    q git -C "$ENG" rev-list --left-right --count "refs/heads/main...$DELIVERED"
    emit git_e rev-list --left-right --count "refs/heads/main...$DELIVERED"
    q git -C "$ENG" merge-base --is-ancestor refs/heads/main "$DELIVERED"
    emit git_e merge-base --is-ancestor refs/heads/main "$DELIVERED"
    q git -C "$ENG" log --oneline "refs/heads/main..$DELIVERED"
    emit git_e log --oneline "refs/heads/main..$DELIVERED"
    q git -C "$ENG" diff --shortstat "refs/heads/main...$DELIVERED"
    emit git_e diff --shortstat "refs/heads/main...$DELIVERED"

    echo; echo "## conflict predicate at the delivered tip"
    mt=$(git_e merge-tree --write-tree --name-only refs/heads/main "$DELIVERED" 2>"$TMP/mt-win.err"); rc=$?
    printf '%s\n' "$mt" | sed 's/^/      /'; printf '        rc=%d\n' "$rc"
    printf '      CONFLICT line count = %s\n' "$(printf '%s\n' "$mt" | grep -c 'CONFLICT' || true)"
    [ -s "$TMP/mt-win.err" ] && { echo '      stderr NOT empty:'; sed 's/^/      /' "$TMP/mt-win.err"; } \
                            || echo '      stderr empty (0 bytes)'

    echo; echo "## AC1 - the neutraliser census, delivered tip beside trunk"
    q git -C "$ENG" grep -c neutraliseStructureTokens "$DELIVERED" -- packages/pi-claude-cli/src
    emit git_e grep -c neutraliseStructureTokens "$DELIVERED" -- packages/pi-claude-cli/src
    q git -C "$ENG" grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src
    emit git_e grep -c neutraliseStructureTokens refs/heads/main -- packages/pi-claude-cli/src
    echo "      (the trunk-level predicate belongs to the operator's merge, which this card"
    echo "       may not perform; AC1 is asserted at the delivered tip and never at trunk)"

    echo; echo "## the trunk ref this card never wrote - run start beside run end"
    echo "      run start : $(cat "$TMP/trunk-run-start.sha" 2>/dev/null || echo '(not captured)')"
    q git -C "$ENG" rev-parse refs/heads/main
    emit git_e rev-parse refs/heads/main

    echo; echo "## the instrument deviation, refusal quoted verbatim"
    echo '      fn_send_message(recipient="agent-94d01490", body=<the announcement below>)'
    echo '      -> Tool fn_send_message not found'
    echo '      The identical bytes went out in the same tick as a card log entry and as the'
    echo '      registered artifact titled for the CEO, id 56b4aa74-27ef-4599-82ba-ba28f1497c31.'
    echo '      The prints above are the same commands re-run now; the announcement itself went'
    echo '      out at 2026-11-11T01:38:41Z against trunk c025edb9edf4a5eba16b03279ac9c5ba7805a4ef'
    echo '      and its raw tick prints are kept unchanged in raw/window-tick.txt.'

    echo; echo "## the announced bytes, verbatim from window-message.txt"
    local msg="$CR/stas-393-engine-evidence/window-message.txt"
    if [ -f "$msg" ]; then
      printf '      sha256:%s\n' "$(openssl dgst -sha256 "$msg" | awk '{print $2}')"
      printf '      bytes: %s\n' "$(wc -c < "$msg")"
      echo
      cat "$msg"
    else
      echo '      MISSING: window-message.txt is not beside generate.sh'
    fi
  } > "$RAW/window-announcement.txt" 2>&1
  echo "wrote $RAW/window-announcement.txt"
}

cap_deliver() {
  provision
  git_e log -p refs/heads/main.."$DELIVERED" > "$CR/stas-393-engine-evidence/engine-commits.txt" 2>&1
  git_e diff refs/heads/main.."$DELIVERED"   > "$CR/stas-393-engine-evidence/engine-diff.txt" 2>&1
  echo "wrote engine-commits.txt ($(wc -l < "$CR/stas-393-engine-evidence/engine-commits.txt") lines) and engine-diff.txt ($(wc -l < "$CR/stas-393-engine-evidence/engine-diff.txt") lines)"
}

case "${1:-all}" in
  ac0)      cap_ac0 ;;
  before)   cap_before ;;
  rebase)   cap_rebase ;;
  redgreen) cap_redgreen ;;
  pins)     cap_pins ;;
  window)   cap_window ;;
  deliver)  cap_deliver ;;
  after)    cap_after ;;
  all)      cap_ac0; cap_before; cap_rebase; cap_redgreen; cap_pins; cap_after; cap_window; cap_deliver ;;
  *) echo "unknown capture: $1" >&2; exit 2 ;;
esac
