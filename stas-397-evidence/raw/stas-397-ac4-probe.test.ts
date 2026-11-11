/*
STAS-397 AC4 PROBE — not a deliverable, and never committed to the engine branch.

AC4 asks for PRINTED outcomes, not asserted ones: the four states, each stated as `released` or
`refused`, plus the literal audit record a release writes when it throws ignored content away.
The guard file asserts all of that; this file runs the same entry point over the same fixture and
CONSOLE.LOGS the answer, so the capture holds the record verbatim instead of a passing test name.

Run it by copying it into the engine checkout as
  packages/engine/src/__tests__/stas-397-ac4-probe.test.ts
and deleting it afterwards:

  ./node_modules/.bin/vitest run --project=engine-default --reporter=dot \
    src/__tests__/stas-397-ac4-probe.test.ts

Fixture and entry point are the guard file's, unchanged: a real repository, a real linked worktree,
a real integration ref, `removeWorktree` with the reason the recovery sweep passes, no mock.
*/
import { exec } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile, access, constants } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";
import { RemovalReason, removeWorktree } from "../worktree/worktree-backend.js";
import type { GitAuditInput, RunAuditor } from "../util/run-audit.js";

const execAsync = promisify(exec);

async function run(command: string, cwd: string): Promise<string> {
  const { stdout } = await execAsync(command, { cwd, encoding: "utf-8" });
  return stdout.trim();
}

const tempDirs: string[] = [];
const IGNORED_ARTIFACTS = [".venv/lib/sitecustomize.py", "stashai.egg-info/PKG-INFO"];

async function createFixture(lane: string): Promise<{ repoDir: string; worktreePath: string; auditEvents: GitAuditInput[] }> {
  const repoDir = await mkdtemp(path.join(tmpdir(), "stas-397-probe-"));
  tempDirs.push(repoDir);
  await run('git init -q -b main', repoDir);
  await run('git config user.email "engine-test@example.com" && git config user.name "Engine Test"', repoDir);
  await writeFile(path.join(repoDir, ".gitignore"), ".venv/\n*.egg-info/\n.env\n", "utf-8");
  await writeFile(path.join(repoDir, "README.md"), "# trunk\n", "utf-8");
  await run("git add -A && git commit -qm init", repoDir);
  await run("git branch fusion/wedged", repoDir);
  await writeFile(path.join(repoDir, "LANDED.md"), "# the lane work, landed on trunk\n", "utf-8");
  await run("git add LANDED.md && git commit -qm 'land the lane work on trunk'", repoDir);
  await mkdir(path.join(repoDir, ".worktrees"), { recursive: true });
  const worktreePath = path.join(repoDir, ".worktrees", lane);
  await run(`git worktree add -q ${JSON.stringify(worktreePath)} fusion/wedged`, repoDir);
  for (const artifact of IGNORED_ARTIFACTS) {
    const target = path.join(worktreePath, artifact);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, "regenerable build output\n", "utf-8");
  }
  const auditEvents: GitAuditInput[] = [];
  return { repoDir, worktreePath, auditEvents };
}

async function exists(target: string): Promise<boolean> {
  try {
    await access(target, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

async function outcome(fixture: { repoDir: string; worktreePath: string; auditEvents: GitAuditInput[] }): Promise<string> {
  try {
    const result = await removeWorktree({
      rootDir: fixture.repoDir,
      worktreePath: fixture.worktreePath,
      settings: {},
      taskId: "STAS-397",
      reason: RemovalReason.SelfHealingBranchConflict,
      audit: { git: async (event: GitAuditInput) => { fixture.auditEvents.push(event); } } as unknown as RunAuditor,
    });
    return `released (removed=${String(result.removed)}), directory still on disk = ${String(await exists(fixture.worktreePath))}`;
  } catch (error) {
    return `refused (${(error as Error).name}: ${(error as Error).message})`;
  }
}

describe("STAS-397 AC4 probe - printed outcomes of the four states", () => {
  it("prints released/refused for each fixture and the discard record for the released one", async () => {
    console.log("AC4-PRINT ============== released/refused, printed, not asserted ==============");

    const ignoredOnly = await createFixture("ignored-only");
    console.log(`AC4-PRINT state=ignored-only                 outcome=${await outcome(ignoredOnly)}`);
    const discard = ignoredOnly.auditEvents.find((event) => event.type === "worktree:removal-discarded-ignored-content");
    console.log(`AC4-PRINT discard-record-type=${discard ? discard.type : "ABSENT"}`);
    console.log(`AC4-PRINT discard-record-metadata=${JSON.stringify(discard?.metadata ?? null)}`);

    const tracked = await createFixture("tracked-modified");
    await writeFile(path.join(tracked.worktreePath, "README.md"), "# trunk\n\nwork in progress\n", "utf-8");
    const trackedOutcome = await outcome(tracked);
    const keptTracked = await readFile(path.join(tracked.worktreePath, "README.md"), "utf-8");
    console.log(`AC4-PRINT state=tracked-modified             outcome=${trackedOutcome}`);
    console.log(`AC4-PRINT state=tracked-modified             content-preserved=${String(keptTracked.includes("work in progress"))}`);

    const untracked = await createFixture("untracked");
    await writeFile(path.join(untracked.worktreePath, "notes.md"), "unfinished work\n", "utf-8");
    const untrackedOutcome = await outcome(untracked);
    const keptUntracked = await readFile(path.join(untracked.worktreePath, "notes.md"), "utf-8");
    console.log(`AC4-PRINT state=untracked                    outcome=${untrackedOutcome}`);
    console.log(`AC4-PRINT state=untracked                    content-preserved=${String(keptUntracked === "unfinished work\n")}`);

    const quoted = await createFixture("quoted-untracked");
    await writeFile(path.join(quoted.worktreePath, 'we"ird.md'), "unfinished work\n", "utf-8");
    const porcelain = await run("git status --porcelain --untracked-files=all", quoted.worktreePath);
    const quotedOutcome = await outcome(quoted);
    const keptQuoted = await readFile(path.join(quoted.worktreePath, 'we"ird.md'), "utf-8");
    console.log(`AC4-PRINT state=quoted-untracked             porcelain=${porcelain}`);
    console.log(`AC4-PRINT state=quoted-untracked             outcome=${quotedOutcome}`);
    console.log(`AC4-PRINT state=quoted-untracked             content-preserved=${String(keptQuoted === "unfinished work\n")}`);

    console.log("AC4-PRINT ================================================================");
    await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
    expect(true).toBe(true);
  }, 120_000);
});
