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

function curatorData(
  overrides: Partial<CuratorData["curator"]> = {},
  envelope: Partial<CuratorData> = {},
): CuratorData {
  return {
    curator: {
      name: "Curator",
      schedule_cron: "0 3 * * *",
      curated_through: "2026-01-01T00:00:00Z",
      last_run_at: "2026-01-02T03:00:00Z",
      last_run_outcome: "ran",
      last_run_error: null,
      month_run_count: 0,
      month_run_anchor: "2026-01-01",
      ...overrides,
    },
    next_run_at: "2026-01-03T03:00:00Z",
    // The account's ceiling, which the server sends as null for an account that has
    // no cap. A fixture default of 10 is what a capped free account gets.
    free_runs_per_month: 10,
    prompt: "Read the feed.",
    backfill_prompt: "Read the whole history.",
    instructions: null,
    event_backlog: {
    distinct_events: 3,
    raw_rows: 4,
    distinct_sessions: 2,
    // The closed-drain default: the readout counted from the same instant the row's
    // watermark names, because with no drain open there is only one position.
    read_from: "2026-01-01T00:00:00Z",
    draining: false,
  },
    feeding: [],
    opted_out: [],
    runs: [],
    ...envelope,
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
    // A key is only half the answer: this deployment may be the one that has no
    // cloud key by design, and its remedy is the endpoint its own console connects.
    expect(banner.textContent).toMatch(/local model endpoint/i);
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

  // A spent allowance is a designed skip like a missing model, but it is not the
  // same problem: nothing has to be connected and the lane starts again on its own.
  // Reporting it as "no changes" was the silence this page used to keep.
  it("names a spent allowance as the reason, and says when the runs come back", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData({ last_run_outcome: "skipped_credits", month_run_count: 10 }),
    );

    render(<CuratorRoute />);

    const banner = await screen.findByText(/free runs are used up/i);
    expect(banner.textContent).toMatch(/first day of next month/i);
    expect(screen.queryByText(/no model connected/i)).toBeNull();
  });
});

describe("Curator page allowance line", () => {
  it("states the runs worked against the account's ceiling", async () => {
    vi.mocked(getCurator).mockResolvedValue(curatorData({ month_run_count: 3 }));

    render(<CuratorRoute />);

    // Both numerals come from the response: a ceiling copied into the page would
    // make the sentence wrong in silence.
    expect(await screen.findByText("3 of 10 free runs used this month.")).toBeDefined();
  });

  it("renders no line for an account the allowance does not bound", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData({ month_run_count: 41 }, { free_runs_per_month: null }),
    );

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    // The response says this account has no cap. Any defaulted numeral — zero, ten,
    // or the row's own count — would state a limit it does not have.
    expect(screen.queryByText(/free runs used this month/)).toBeNull();
  });

  it("renders no line for a lane that has never charged the allowance", async () => {
    vi.mocked(getCurator).mockResolvedValue(curatorData({ month_run_count: 0 }));

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    // A curator on the customer's own endpoint is never metered, so "0 of 10 used"
    // would imply a metering that never happened.
    expect(screen.queryByText(/free runs used this month/)).toBeNull();
  });
});

/** Which instant the console is reading from is not a detail of wording: a history
 *  drain can run for a month, keeping its unread backlog BEHIND the nightly watermark,
 *  and the watermark does not move until the drain finishes. A page that shows the
 *  watermark beside that backlog tells the operator the lane has stopped making progress
 *  while its committed passes advance invisibly. The readout names the position it
 *  counted from, and the console shows that one.
 */
describe("Curator page readout position", () => {
  function factLabeled(label: string): HTMLElement {
    const card = screen.getByText(label).parentElement;
    if (!card) throw new Error(`no fact card for ${label}`);
    return card;
  }

  // The same formatter the page uses, so the assertion compares rendered instants
  // rather than a date format this runtime may not produce.
  function instantText(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  const WATERMARK = "2026-08-01T00:00:00Z";
  const DRAIN = "2026-01-05T03:00:00Z";

  it("names the drain as the position, and says the watermark waits for it", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData(
        { curated_through: WATERMARK },
        {
          event_backlog: {
            distinct_events: 3,
            raw_rows: 4,
            distinct_sessions: 2,
            read_from: DRAIN,
            draining: true,
          },
        },
      ),
    );

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    const card = factLabeled("Draining through");
    // The two instants are months apart on purpose: showing the watermark here would
    // be the bug, and a card that could show either one would not fail this.
    expect(card.textContent).toContain(instantText(DRAIN));
    expect(card.textContent).not.toContain(instantText(WATERMARK));
    expect(card.textContent).toMatch(/history drain is in flight/i);
    expect(card.textContent).toMatch(/watermark is unchanged until the drain retires/i);
    // The unread figure and the position it was counted from must not disagree.
    expect(screen.getByText(/^3 still to read/)).toBeDefined();
  });

  it("keeps the watermark sentence whole when no drain is open", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData(
        { curated_through: WATERMARK },
        {
          event_backlog: {
            distinct_events: 3,
            raw_rows: 4,
            distinct_sessions: 2,
            read_from: WATERMARK,
            draining: false,
          },
        },
      ),
    );

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    const card = factLabeled("Reading since");
    expect(card.textContent).toContain(instantText(WATERMARK));
    expect(card.textContent).toBe(
      `Reading since${instantText(WATERMARK)}The point the last run finished reading; the next run starts here.`,
    );
    expect(screen.queryByText(/drain/i)).toBeNull();
  });

  it("renders no instant of its own for a lane that has read nothing yet", async () => {
    vi.mocked(getCurator).mockResolvedValue(
      curatorData(
        { curated_through: WATERMARK },
        {
          event_backlog: {
            distinct_events: 3,
            raw_rows: 4,
            distinct_sessions: 2,
            read_from: null,
            draining: false,
          },
        },
      ),
    );

    render(<CuratorRoute />);
    await screen.findByText("When does the curator run?");

    // `read_from` is the instant the count was taken from, and a lane that has never
    // read anything has none. Falling back to `curated_through` here would put a second
    // position on the page for a client to choose between, so the card shows the
    // absence instead of a numeral it was never given.
    const card = factLabeled("Reading since");
    expect(card.textContent).not.toContain(instantText(WATERMARK));
    expect(card.textContent).toContain("—");
  });
});
