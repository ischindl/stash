/**
 * Stash extension for Pi 1.x — transport only.
 *
 * Each pi event is serialized and piped into `stash hook run pi <event>` on
 * stdin. The Python handlers shipped inside the stashai package make every
 * Stash call and translate pi's field names; nothing about Stash is decided here.
 *
 * pi loads any `*.ts` in `<agent-dir>/extensions/` (default `~/.pi/agent/extensions`,
 * overridable with `PI_CODING_AGENT_DIR`) at startup, with no registration step.
 * `stash setup --record --agents pi` copies this file there; the installer is the
 * only thing that ever writes it.
 *
 * There is deliberately no try/catch in this file. A Stash failure must reach the
 * user, so every child-process failure is announced on stderr — that is fail-loud,
 * not a swallow. stdout is dropped because pi reads nothing from an extension's
 * child process and a stray write would corrupt its TUI; stderr is inherited so a
 * handler's traceback is visible.
 */

import { spawn } from "node:child_process";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

type Envelope = Record<string, unknown>;

// One promise tail, appended to by every event. Awaiting it keeps pi's own event
// ordering, so `session_shutdown` can never overtake `agent_settled`.
let pending: Promise<unknown> = Promise.resolve();

function announce(problem: string): void {
  process.stderr.write("stash pi hook failed: " + problem + "\n");
}

function runHook(event: string, payload: Envelope): Promise<unknown> {
  const child = spawn("stash", ["hook", "run", "pi", event], {
    stdio: ["pipe", "ignore", "inherit"],
  });
  const settled = new Promise<void>((resolve) => {
    child.on("error", (cause: Error) => {
      announce(event + ": " + cause.message);
      resolve();
    });
    child.on("close", (code: number | null) => {
      if (code !== 0) announce(event + ": exited with " + String(code));
      resolve();
    });
  });
  child.stdin.on("error", (cause: Error) => announce(event + " stdin: " + cause.message));
  child.stdin.write(JSON.stringify(payload));
  child.stdin.end();
  pending = pending.then(() => settled);
  return pending;
}

/** The three values pi exposes on its context object and never on an event. */
function envelope(ctx: ExtensionContext): Envelope {
  return {
    session_id: ctx.sessionManager.getSessionId(),
    cwd: ctx.cwd,
    transcript_path: ctx.sessionManager.getSessionFile(),
  };
}

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => {
    await runHook("on_session_start", { ...envelope(ctx), reason: event.reason });
  });

  // Only a run a user started counts as a prompt. `messageType: "custom"` is an
  // extension-injected message, not something anybody typed.
  pi.on("before_agent_start", async (event, ctx) => {
    if (event.messageType !== "user") return;
    await runHook("on_prompt", { ...envelope(ctx), messageType: event.messageType, prompt: event.prompt });
  });

  // Forwarded from the result side only, so `content` (the tool's answer) is
  // always present; `tool_call` fires before the tool has run.
  pi.on("tool_result", async (event, ctx) => {
    await runHook("on_tool_use", {
      ...envelope(ctx),
      toolName: event.toolName,
      input: event.input,
      content: event.content,
      structuredContent: event.structuredContent,
      isError: event.isError,
    });
  });

  // `agent_settled`, not `agent_end`: it fires once the run has fully settled,
  // with no retry, compaction, or queued continuation still queued.
  pi.on("agent_settled", async (event, ctx) => {
    await runHook("on_stop", { ...envelope(ctx), aborted: event.aborted });
  });

  pi.on("session_shutdown", async (event, ctx) => {
    await runHook("on_session_end", { ...envelope(ctx), reason: event.reason });
  });
}
