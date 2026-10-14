import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Task, TaskStore, WorkflowIr } from "@fusion/core";
import kbExtension, {
  __resolveProjectRootForTesting,
  __setCachedStoreForTesting,
  closeCachedStores,
} from "../extension.js";

/*
FNXC:RetryReportCarriesTarget 2026-10-13-06:30 (STAS-299 code review, P1 — found by greptile):
`#2728` found `fn task retry` correctly deciding to retry and then crashing on
`Invalid transition: 'checking' -> 'todo'`, because the CLASSIFIER had been converted to the task's own
workflow while the move TARGET was still the literal. The fix resolved the target — and stopped there.
Both `fn_task_retry` re-queues moved the card to the resolved hold lane and then reported the literal:
`details.newColumn: 'todo'` is machine-readable output another tool can act on, and the `logEntry` line
is the card's audit trail, so a renamed board's operator saw a retry recorded into a lane that board
does not declare. A third branch moves NOTHING and answered the literal `in-review` anyway, naming a
column the card was never in. The board route got this right from the start ("resolve the rebound
destination BEFORE logging"); the tool surface is the one that lagged, and `extension.test.ts` could not
catch it because every one of its retry fixtures sits on `builtin:coding`, whose hold lane IS `todo` — so
pre-fix and post-fix code produce the same bytes there.

This file therefore pins the report to the RESOLVED lane on a board where the two differ. A fake store
keeps it honest: the assertion set is exactly "everything the tool said about where the card went
matches the destination its own `moveTask` call was given", with no lane name written by hand.
*/

interface RegisteredTool {
  name: string;
  execute: (
    toolCallId: string,
    params: Record<string, unknown>,
    signal: AbortSignal | undefined,
    onUpdate: ((update: unknown) => void) | undefined,
    ctx: { cwd: string },
  ) => Promise<{ content?: Array<{ text: string }>; details?: Record<string, unknown>; isError?: boolean }>;
}

function createMockAPI() {
  const tools = new Map<string, RegisteredTool>();
  const api = {
    registerTool: (def: RegisteredTool) => tools.set(def.name, def),
    registerCommand: () => {},
    registerShortcut: () => {},
    registerFlag: () => {},
    on: () => {},
    tools,
  };
  return api as unknown as { tools: Map<string, RegisteredTool> };
}

/**
 * `building` is the WIP lane and `checking` the review lane, so `backlog` is the ONLY legal answer to
 * "where does a Retry re-queue this card" — and it is never spelled `todo`.
 */
const HOLD_COLUMN = "backlog";
const REVIEW_COLUMN = "checking";
const WORKFLOW_ID = "custom:renamed-lanes";

const RENAMED_IR = {
  version: "v2",
  id: WORKFLOW_ID,
  name: "Renamed lanes",
  columns: [
    { id: HOLD_COLUMN, name: "Backlog", traits: [{ trait: "intake" }, { trait: "hold", config: { release: "capacity" } }] },
    { id: "building", name: "Building", traits: [{ trait: "wip", config: { limitSetting: "maxConcurrent" } }] },
    {
      id: REVIEW_COLUMN,
      name: "Checking",
      traits: [{ trait: "merge-blocker" }, { trait: "human-review" }, { trait: "merge" }],
    },
  ],
  nodes: [],
  edges: [],
} as unknown as WorkflowIr;

function fakeRetryStore(task: Task) {
  const moveTask = vi.fn(async () => undefined);
  const logEntry = vi.fn(async () => undefined);
  const updateTask = vi.fn(async (_id: string, patch: Record<string, unknown>) => ({ ...task, ...patch }));
  const store = {
    getTask: vi.fn(async () => task),
    getSettings: vi.fn(async () => ({ autoMerge: true })),
    getTaskWorkflowSelection: vi.fn(() => ({ workflowId: WORKFLOW_ID, stepIds: [] })),
    getTaskWorkflowSelectionAsync: vi.fn(async () => ({ workflowId: WORKFLOW_ID, stepIds: [] })),
    getWorkflowDefinition: vi.fn(async (id: string) => (id === WORKFLOW_ID ? { ir: RENAMED_IR } : undefined)),
    resetTerminalFailureAutoRecoveryBudget: vi.fn(async () => undefined),
    updateTask,
    logEntry,
    moveTask,
  } as unknown as TaskStore;
  return { store, moveTask, logEntry, updateTask };
}

describe("extension fn_task_retry reports the lane it actually resolved", () => {
  let cwd = process.cwd();

  beforeEach(() => {
    // A real directory so the extension's project-root resolution lands on a stable key.
    cwd = mkdtempSync(join(tmpdir(), "fusion-retry-report-"));
    mkdirSync(join(cwd, ".fusion"), { recursive: true });
  });

  afterEach(async () => {
    await closeCachedStores();
  });

  async function retryTask(task: Task) {
    const { store, moveTask, logEntry } = fakeRetryStore(task);
    __setCachedStoreForTesting(__resolveProjectRootForTesting(cwd), store);
    const api = createMockAPI();
    kbExtension(api);
    const result = await api.tools.get("fn_task_retry")!.execute(
      "retry-1",
      { id: task.id },
      undefined,
      undefined,
      { cwd },
    );
    return { result, moveTask, logEntry };
  }

  /** The card every retry branch below starts from: a failed review-lane card. */
  function cardInReviewLane(overrides: Partial<Task> = {}): Task {
    return {
      id: "RN-001",
      title: "A card on a board that renamed its lanes",
      column: REVIEW_COLUMN,
      status: "failed",
      error: "Execution failed: step 3 exited non-zero",
      steps: [{ name: "Preflight", status: "done" }, { name: "Build it", status: "pending" }],
      currentStep: 1,
      ...overrides,
    } as unknown as Task;
  }

  it("re-queues an execution failure to the resolved hold lane and answers with that lane", async () => {
    const { result, moveTask, logEntry } = await retryTask(cardInReviewLane());

    // The destination the fix resolves — established first, because every report is judged against it.
    expect(moveTask).toHaveBeenCalledWith(
      "RN-001",
      HOLD_COLUMN,
      expect.objectContaining({ preserveProgress: true, moveSource: "user", parkOnHold: false }),
    );

    const answered = (value: string) => {
      expect(value).toContain(HOLD_COLUMN);
      expect(value).not.toContain("todo");
    };
    answered(String(result.content?.[0]?.text ?? ""));
    answered(String(result.details?.newColumn));
    answered(String(logEntry.mock.calls[0]?.[1]));
    expect(result.isError).toBeUndefined();
  });

  it("re-queues an unusable-worktree session-start failure and answers with the resolved lane", async () => {
    const missingWorktree = cardInReviewLane({
      status: null as unknown as Task["status"],
      error: "Refusing to start coding agent in missing worktree: /tmp/gone/.fusion/worktrees/rn-001",
      worktree: "/tmp/gone/.fusion/worktrees/rn-001",
      branch: "fusion/RN-001",
    });

    const { result, moveTask, logEntry } = await retryTask(missingWorktree);

    expect(moveTask).toHaveBeenCalledWith(
      "RN-001",
      HOLD_COLUMN,
      expect.objectContaining({ preserveProgress: true, moveSource: "user", parkOnHold: false }),
    );
    expect(String(result.content?.[0]?.text)).toContain(HOLD_COLUMN);
    expect(String(result.details?.newColumn)).toBe(HOLD_COLUMN);
    expect(String(logEntry.mock.calls[0]?.[1])).toContain(`→ ${HOLD_COLUMN},`);
    expect(JSON.stringify(result)).not.toContain("todo");
  });

  it("answers a merge retry that moves nothing with the lane the card is actually sitting in", async () => {
    /*
    Every step is terminal and the failure is a merge failure, so this is the branch that resets merge
    state and deliberately leaves the card where it is. Its old answer named `in-review`, a column this
    board does not declare, in a payload a downstream tool reads as "the card moved here".
    */
    const { result, moveTask } = await retryTask(cardInReviewLane({
      error: "Merge failed: CONFLICT in 2 files",
      mergeRetries: 2,
      steps: [{ name: "Preflight", status: "done" }, { name: "Build it", status: "done" }],
      currentStep: 2,
    }));

    expect(moveTask).not.toHaveBeenCalled();
    expect(String(result.details?.newColumn)).toBe(REVIEW_COLUMN);
    expect(String(result.content?.[0]?.text)).toContain(REVIEW_COLUMN);
    expect(JSON.stringify(result)).not.toContain("in-review");
  });
});
