/** The console's curator page is where a workspace owner goes to find out why the
 *  wiki stopped growing. A curator with no model to run on skips every single tick,
 *  and a designed skip is a *resolved* outcome — it never writes `last_run_error` —
 *  so a page that reads only the error reports a permanent block as a healthy run.
 *  These pin the blocked banner and, just as important, that a real failure keeps
 *  its own banner beside it rather than being replaced.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getCurator } from "@/lib/api";
import CuratorRoute from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// The gate is its own screen with its own workspace fetch; these tests are about
// what the curator page does with the state it is handed.
vi.mock("@/components/developer/DeveloperGate", () => ({
  default: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/api", () => ({
  getCurator: vi.fn(),
  runCuratorNow: vi.fn(),
  backfillCurator: vi.fn(),
  updateCuratorInstructions: vi.fn(),
}));

type CuratorData = Awaited<ReturnType<typeof getCurator>>;

function curatorData(overrides: Partial<CuratorData["curator"]> = {}): CuratorData {
  return {
    curator: {
      name: "Curator",
      schedule_cron: "0 3 * * *",
      curated_through: "2026-01-01T00:00:00Z",
      last_run_at: "2026-01-02T03:00:00Z",
      last_run_outcome: "ran",
      last_run_error: null,
      ...overrides,
    },
    next_run_at: "2026-01-03T03:00:00Z",
    prompt: "Read the feed.",
    backfill_prompt: "Read the whole history.",
    instructions: null,
    event_backlog: { distinct_events: 3, raw_rows: 4, distinct_sessions: 2 },
    feeding: [],
    opted_out: [],
    runs: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("Curator page blocked state", () => {
  it("states the block and names the fix when no model could run", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData({ last_run_outcome: "skipped_no_credential" }),
    );

    render(<CuratorRoute />);

    const banner = await screen.findByText(/no model connected/i);
    expect(banner.textContent).toMatch(/ANTHROPIC_API_KEY/);
  });

  it("says nothing once a run has resolved", async () => {
    vi.mocked(getCurator).mockResolvedValue(curatorData({ last_run_outcome: "ran" }));

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    expect(screen.queryByText(/no model connected/i)).toBeNull();
  });

  it("keeps a real failure's own banner beside it", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData({ last_run_outcome: "failed", last_run_error: "opencode error" }),
    );

    render(<CuratorRoute />);

    expect(await screen.findByText(/Last run failed: opencode error/)).toBeDefined();
    expect(screen.queryByText(/no model connected/i)).toBeNull();
  });
});
