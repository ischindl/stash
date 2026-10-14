import { afterEach, describe, expect, it, vi } from "vitest";
import {
  __applyAgentPolicyGateForTesting,
  __clearExtensionStoreBootStateForTesting,
  __getStoreForTesting,
  __resolveProjectRootForTesting,
  __setExtensionStoreBootFactoryForTesting,
  buildStoreBootStallResult,
  clearHostTaskStores,
  closeCachedStores,
  isTransientStoreBootFailure,
  TaskStoreBootTransientError,
  wrapExtensionToolExecute,
} from "../extension.js";

/*
FNXC:BootTimeoutTransience 2026-10-13-03:05 (STAS-299, requirement 3 + Invariant B):
STAS-281's agent tried to unstick a card its review session died behind and got:
`fn_task_retry denied: the agent permission policy could not be resolved (fn extension TaskStore boot
timed out after 30000ms). Failing closed — ask the operator to run this tool.` There was no permission
problem. The store open was merely slow, and the sentence both misnamed the cause and forbade the one
action that works — calling again, which is served from the boot already in flight.

Invariant B: NO store-backed extension call may answer `denied` for a transient boot stall, while a
real boot failure and a real policy denial stay fail-closed. These tests pin the type that carries the
difference and the answer each surface gives, on both operator-visible fingerprints of the stall:
the wall-clock expiry itself, and the backoff-window caller that arrives while the orphan is booting.
*/

const PROJECT_ROOT = __resolveProjectRootForTesting(process.cwd());
const TINY_BUDGET_MS = 20;
const IMMEDIATE_MS = 50;

type BootFactoryArg = Parameters<typeof __setExtensionStoreBootFactoryForTesting>[0];

function bootFactory(factory: () => Promise<unknown>): BootFactoryArg {
  return factory as unknown as BootFactoryArg;
}

function stalledForever(): Promise<never> {
  return new Promise<never>(() => {});
}

async function elapsedRejecting(promise: Promise<unknown>): Promise<{ elapsedMs: number; error: unknown }> {
  const started = Date.now();
  const error = await promise.then(() => undefined).catch((caught: unknown) => caught);
  return { elapsedMs: Date.now() - started, error };
}

afterEach(async () => {
  await closeCachedStores();
  __clearExtensionStoreBootStateForTesting();
  clearHostTaskStores();
  vi.restoreAllMocks();
});

describe("a stalled TaskStore boot is a typed stall, not a failure verdict (STAS-299 req 3)", () => {
  it("carries the wall-clock expiry as a transient error while keeping the shipped message", async () => {
    __setExtensionStoreBootFactoryForTesting(bootFactory(stalledForever));

    const { error } = await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, TINY_BUDGET_MS));

    expect(error).toBeInstanceOf(TaskStoreBootTransientError);
    expect(isTransientStoreBootFailure(error)).toBe(true);
    /* RUFU-377's operator-visible fingerprint must survive the typing: the bare sentence can only
       come from the wall-clock race, which is what separates a slow boot from a broken one. */
    expect((error as Error).message).toMatch(/fn extension TaskStore boot timed out after 20ms/);
    expect((error as TaskStoreBootTransientError).bootTimeoutMs).toBe(TINY_BUDGET_MS);
  });

  it("types the backoff-window caller transient too, and still answers it immediately", async () => {
    __setExtensionStoreBootFactoryForTesting(bootFactory(stalledForever));

    await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, TINY_BUDGET_MS));
    const second = await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, TINY_BUDGET_MS));

    /* STAS-251's shipped contract: the second caller must not re-pay the budget. */
    expect(second.elapsedMs).toBeLessThan(IMMEDIATE_MS);
    /* STAS-299's addition: it must not be mistaken for a fresh failure either — the orphan is still
       booting and will serve the next caller, so the answer is still "retry". */
    expect(isTransientStoreBootFailure(second.error)).toBe(true);
    expect((second.error as Error).message).toContain("recently failed (cooldown");
  });

  it("leaves a real boot failure un-typed, so the fail-closed denial keeps its honest cause (RUFU-275)", async () => {
    __setExtensionStoreBootFactoryForTesting(
      bootFactory(async () => {
        throw new Error("canceling statement due to lock timeout");
      }),
    );

    const { error } = await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, 500));

    expect(isTransientStoreBootFailure(error)).toBe(false);
    expect((error as Error).message).toMatch(/lock timeout/);
  });

  it("types the pool's own lock-expiry sentence at the boot boundary, the one place allowed to read it", async () => {
    /*
    STAS-299's review asked that the classification live ONLY here. The pool giving up on a contended
    lock arrives as a rejected boot, not as an expired budget, and RUFU-377's real stall looked exactly
    like this sentence — so it must join the typed stall class even though nothing downstream matches
    text any more. The counter-case above is the pair that makes this mean something: a lock failure
    that names no expired budget stays a real failure with its honest cause.
    */
    __setExtensionStoreBootFactoryForTesting(
      bootFactory(async () => {
        throw new Error("connection timed out after 20242ms (canceling statement due to lock timeout)");
      }),
    );

    const { error } = await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, 500));

    expect(error).toBeInstanceOf(TaskStoreBootTransientError);
    expect(isTransientStoreBootFailure(error)).toBe(true);
    expect((error as Error).message).toMatch(/connection timed out after 20242ms/);
    expect((error as TaskStoreBootTransientError).bootTimeoutMs).toBe(500);
  });

  it("answers a stall with a retry instruction and never with a denial", () => {
    const stall = new TaskStoreBootTransientError("fn extension TaskStore boot timed out after 20ms", 20);

    const result = buildStoreBootStallResult("fn_task_retry", stall);
    const text = result.content[0]?.text ?? "";

    expect(result.isError).toBe(true);
    expect(result.details).toMatchObject({
      transient: "taskstore-boot-timeout",
      retryable: true,
      bootTimeoutMs: 20,
      tool: "fn_task_retry",
    });
    expect(text).toContain("Retry this same call");
    /* RUFU-377's KTD-2 attribution survives the retry hint: tool prefix first, store-boot subject as
       its own line, so an operator can still tell a slow store from a slow tool. */
    expect(text).toMatch(/^fn_task_retry failed: fn extension TaskStore boot timed out after 20ms\n/);
    /* The two sentences that made the incident unrecoverable: a denial verdict and a referral. The
       sentence here says the opposite of a verdict, so match the verdict shape, not the word. */
    expect(text).not.toMatch(/denied:/i);
    expect(text).not.toMatch(/failing closed/i);
    expect(text).toContain("Nothing was denied on policy grounds");
    expect(result.details?.deniedFor).toBeUndefined();
  });
});

/*
FNXC:BootTransienceTypeOnly 2026-10-13-06:30 (STAS-299 code review, P0):
Narrowing the stall classification to the TYPE is only safe if the type still reaches every surface that
has to answer it. `wrapExtensionToolExecute` wraps the WHOLE tool call, so the error it catches is
either the tool's own budget expiry or whatever the tool's body raised — which for these tools is a
`getStore` rejection. THIS is the stall half of the incident (`fn_task_logs_read` died twice on the raw
boot sentence), so the retry answer must still arrive here.
*/
describe("a stalled boot met inside a tool call still answers as the stall class (Invariant B)", () => {
  it("carries the retry answer through the tool wrapper whose other job is the tool's own budget", async () => {
    __setExtensionStoreBootFactoryForTesting(bootFactory(stalledForever));
    const execute = vi.fn(async (_id: string, _params: unknown, _signal?: AbortSignal) => {
      await __getStoreForTesting(PROJECT_ROOT, TINY_BUDGET_MS);
      return { content: [{ type: "text" as const, text: "never reached" }], details: {} };
    });

    const result = await wrapExtensionToolExecute("fn_task_logs_read", execute, 5_000)("id", {}, undefined);

    expect(result).toMatchObject({
      isError: true,
      details: {
        transient: "taskstore-boot-timeout",
        retryable: true,
        bootTimeoutMs: TINY_BUDGET_MS,
        tool: "fn_task_logs_read",
      },
    });
    expect(String((result as { content: Array<{ text: string }> }).content[0].text)).toContain("Retry this same call");
  });
});

describe("the policy gate answers a stalled boot as retryable instead of denying (Invariant B)", () => {
  function gateContext() {
    return { cwd: PROJECT_ROOT, agentId: "agent-boot-stall" } as never;
  }

  async function gateAnswerForStalledBoot() {
    /* Drive the real seam: a genuine wall-clock expiry first, so the gate's own store open meets the
       backoff window the incident produced — no simulated error class involved. */
    __setExtensionStoreBootFactoryForTesting(bootFactory(stalledForever));
    await elapsedRejecting(__getStoreForTesting(PROJECT_ROOT, TINY_BUDGET_MS));
    return await __applyAgentPolicyGateForTesting("fn_task_retry", { id: "STAS-281" }, gateContext());
  }

  it("does not deny a gated tool for a stalled boot and executes nothing", async () => {
    const gated = await gateAnswerForStalledBoot();

    expect(gated).not.toBeNull();
    expect(gated?.isError).toBe(true);
    expect(gated?.details?.deniedFor).toBeUndefined();
    expect(gated?.details).toMatchObject({ transient: "taskstore-boot-timeout", retryable: true });
    expect(String(gated?.content[0]?.text)).toContain("Retry this same call");
  });

  it("still denies a real boot failure under its own honest cause", async () => {
    __setExtensionStoreBootFactoryForTesting(
      bootFactory(async () => {
        throw new Error("database is unreachable");
      }),
    );

    const gated = await __applyAgentPolicyGateForTesting("fn_task_retry", { id: "STAS-281" }, gateContext());

    expect(gated?.details?.deniedFor).toBe("taskstore-boot-unavailable");
    expect(String(gated?.details?.error)).toContain("database is unreachable");
  });
});
