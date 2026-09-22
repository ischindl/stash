import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PROBE_ONLY_MODEL, type ModelEndpointsStatus } from "@/lib/api";
import { ConfirmDialogProvider } from "../ConfirmDialog";
import AgentModelSection from "./AgentModelSection";

const listModelEndpoints = vi.fn();
const probeLocalEndpoint = vi.fn();
const probeSavedEndpoint = vi.fn();
const connectLocalEndpoint = vi.fn();
const deleteLocalEndpoint = vi.fn();
const disconnectAgentCredential = vi.fn();

// Mocked at the module boundary on purpose: the two PROBE_ONLY_MODEL pins below
// read the arguments that actually leave this component, which is the only place
// a wrong model literal on the probe path (or a right one on the store path)
// would be visible.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listModelEndpoints: () => listModelEndpoints(),
    probeLocalEndpoint: (...args: unknown[]) => probeLocalEndpoint(...args),
    probeSavedEndpoint: (...args: unknown[]) => probeSavedEndpoint(...args),
    connectLocalEndpoint: (...args: unknown[]) => connectLocalEndpoint(...args),
    deleteLocalEndpoint: (...args: unknown[]) => deleteLocalEndpoint(...args),
    disconnectAgentCredential: (...args: unknown[]) => disconnectAgentCredential(...args),
  };
});

const FIRST = {
  id: "cred-1",
  name: "box-one",
  base_url: "http://box-one:11434/v1",
  models: ["llama3.1:8b", "qwen2:7b"],
};
const SECOND = {
  id: "cred-2",
  name: "box-two",
  base_url: "http://box-two:11434/v1",
  models: ["gemma3:4b"],
};
// The server names only one endpoint's key — the one an unpinned run resolves to,
// which is the OLDEST connected row. Nothing else in the UI may show a key.
const DEFAULT_DOC = { base_url: FIRST.base_url, model: "llama3.1:8b", api_key: "sk-local-42" };

function status(over: Partial<ModelEndpointsStatus> = {}): ModelEndpointsStatus {
  return { connected: ["local"], endpoints: [FIRST, SECOND], local: DEFAULT_DOC, ...over };
}

function renderSection() {
  return render(
    <ConfirmDialogProvider>
      <AgentModelSection />
    </ConfirmDialogProvider>,
  );
}

async function removeFirstEndpoint() {
  const row = (await screen.findByText("box-one")).closest("li");
  return within(row!).getByRole("button", { name: "Remove" });
}

async function typeDraft(url: string, key?: string) {
  fireEvent.change(screen.getByLabelText("Endpoint base URL"), { target: { value: url } });
  if (key !== undefined) {
    fireEvent.change(screen.getByLabelText("Endpoint key"), { target: { value: key } });
  }
}

// A test result renders as one line: its leading span names the outcome, and on a
// failure the endpoint's own words follow it verbatim. Reading the whole line is
// how these tests pin that the verdict is NAMED, not merely coloured.
function verdictLine(): HTMLElement {
  const label = screen.getByText(
    /^(Unreachable:|Auth failed \(HTTP \d+\):|Reachable and authenticated)/,
  );
  return label.closest("p")!;
}

async function typeAndTest() {
  await typeDraft("http://box-three:11434/v1", "sk-test");
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  listModelEndpoints.mockResolvedValue(status());
  probeLocalEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: ["new-model:1"] });
  probeSavedEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: ["served:1"] });
  connectLocalEndpoint.mockResolvedValue({ id: "cred-3", connected: ["local"] });
  deleteLocalEndpoint.mockResolvedValue({ ok: true, connected: ["local"] });
  disconnectAgentCredential.mockResolvedValue([]);
});

describe("AgentModelSection local endpoint list", () => {
  it("lists every connected endpoint with the models it serves", async () => {
    renderSection();

    expect(await screen.findByText("box-one")).toBeDefined();
    expect(screen.getByText("box-two")).toBeDefined();
    const row = screen.getByText("box-one").closest("li");
    expect(within(row!).getByText("llama3.1:8b")).toBeDefined();
    expect(within(row!).getByText("qwen2:7b")).toBeDefined();
    expect(within(row!).getByText(FIRST.base_url)).toBeDefined();
  });

  it("keeps an unreachable box in the list and shows the server's own reason", async () => {
    listModelEndpoints.mockResolvedValue(
      status({
        endpoints: [
          { ...FIRST, models: [], probe_error: "connect: connection refused" },
          SECOND,
        ],
      }),
    );

    renderSection();

    const row = (await screen.findByText("box-one")).closest("li");
    expect(within(row!).getByText("connect: connection refused")).toBeDefined();
  });

  it("tests before it stores, and offers only the models that test answered with", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1", "sk-test");
    // Nothing can be stored off an untested address: the model pick does not exist
    // until the box has answered for the address currently typed.
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await screen.findByRole("button", { name: "Add endpoint" });
    const picker = screen.getByLabelText("Model");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "new-model:1",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));

    await waitFor(() =>
      expect(connectLocalEndpoint).toHaveBeenCalledWith(
        "http://box-three:11434/v1",
        "new-model:1",
        "sk-test",
      ),
    );
  });

  it("retires a successful test as soon as the address changes", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });

    await typeDraft("http://box-four:11434/v1");

    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("shows the probe's own failure verbatim and keeps what was typed", async () => {
    probeLocalEndpoint.mockResolvedValue({
      ok: false,
      http_status: 502,
      error_detail: "upstream refused the connection",
    });

    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1", "sk-test");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("upstream refused the connection")).toBeDefined();
    // A 502 is the box refusing to serve, not a key being refused: it is named
    // unreachable, and the endpoint's own words are kept exactly as they arrived.
    expect(verdictLine().textContent).toBe("Unreachable: upstream refused the connection");
    expect((screen.getByLabelText("Endpoint base URL") as HTMLInputElement).value).toBe(
      "http://box-three:11434/v1",
    );
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("keeps the typed address and key when the store is refused", async () => {
    connectLocalEndpoint.mockRejectedValue(new ApiError(400, "endpoint probe failed: box is down"));

    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1", "sk-test");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));

    expect(await screen.findByText("endpoint probe failed: box is down")).toBeDefined();
    expect((screen.getByLabelText("Endpoint base URL") as HTMLInputElement).value).toBe(
      "http://box-three:11434/v1",
    );
  });

  it("names the placeholder the probe contract demands and nothing else", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1", "sk-test");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(connectLocalEndpoint).toHaveBeenCalled());

    expect(probeLocalEndpoint).toHaveBeenCalledWith(
      "http://box-three:11434/v1",
      "sk-test",
      PROBE_ONLY_MODEL,
    );
  });

  it("never lets the probe placeholder reach a store request", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(connectLocalEndpoint).toHaveBeenCalled());

    for (const call of connectLocalEndpoint.mock.calls) {
      expect(call).not.toContain(PROBE_ONLY_MODEL);
    }
    expect(connectLocalEndpoint.mock.calls[0][1]).toBe("new-model:1");
  });

  it("surfaces the agents that pin an endpoint instead of claiming it was removed", async () => {
    deleteLocalEndpoint.mockRejectedValue(
      new ApiError(409, "API error 409", {
        detail: {
          message: "agents still pin this endpoint — re-pin or unpin them first",
          agents: [
            { id: "a1", name: "Memory curator" },
            { id: "a2", name: "Wiki curator — Project Atlas" },
          ],
        },
      }),
    );
    listModelEndpoints.mockResolvedValue(status());

    renderSection();
    fireEvent.click(await removeFirstEndpoint());
    fireEvent.click(await screen.findByRole("button", { name: "Remove endpoint" }));

    expect(await screen.findByText(/Wiki curator — Project Atlas/)).toBeDefined();
    expect(screen.getByText(/agents still pin this endpoint/)).toBeDefined();
    // Refused means nothing changed, so the list is not re-fetched as if it had.
    expect(listModelEndpoints).toHaveBeenCalledTimes(1);
  });

  it("removes an endpoint through the by-id route after a confirmation", async () => {
    renderSection();

    fireEvent.click(await removeFirstEndpoint());
    fireEvent.click(await screen.findByRole("button", { name: "Remove endpoint" }));

    await waitFor(() => expect(deleteLocalEndpoint).toHaveBeenCalledWith("cred-1"));
    await waitFor(() => expect(listModelEndpoints).toHaveBeenCalledTimes(2));
  });

  it("seeds the add-form with the address of the endpoint you just removed", async () => {
    renderSection();
    fireEvent.click(await removeFirstEndpoint());
    fireEvent.click(await screen.findByRole("button", { name: "Remove endpoint" }));

    // Reconnecting the same box must never mean retyping its address.
    await waitFor(() =>
      expect((screen.getByLabelText("Endpoint base URL") as HTMLInputElement).value).toBe(
        FIRST.base_url,
      ),
    );
    // The key stays absent — it is never held client-side — and a seed on its own
    // cannot store: the address still has to answer a test before a model exists.
    expect((screen.getByLabelText("Endpoint key") as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
    expect(connectLocalEndpoint).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("leaves the add-form blank when the removal is refused", async () => {
    deleteLocalEndpoint.mockRejectedValue(
      new ApiError(409, "API error 409", {
        detail: { message: "agents still pin this endpoint — re-pin or unpin them first", agents: [] },
      }),
    );

    renderSection();
    fireEvent.click(await removeFirstEndpoint());
    fireEvent.click(await screen.findByRole("button", { name: "Remove endpoint" }));
    expect(await screen.findByText(/agents still pin this endpoint/)).toBeDefined();

    // Nothing was removed, so nothing may be remembered: seeding on a refusal
    // would offer to re-add an endpoint that is still connected.
    expect((screen.getByLabelText("Endpoint base URL") as HTMLInputElement).value).toBe("");
  });

  it("retires a test result that belonged to the endpoint just removed", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft(FIRST.base_url);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });

    fireEvent.click(await removeFirstEndpoint());
    fireEvent.click(await screen.findByRole("button", { name: "Remove endpoint" }));

    // The seed brings the same address back, but the box it was tested against is
    // gone: the store button must not survive on the old test's strength.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull(),
    );
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("reveals the key only on the endpoint the server named as the default", async () => {
    renderSection();
    await screen.findByText("box-two");

    const reveal = screen.getAllByRole("button", { name: "Show key" });
    expect(reveal).toHaveLength(1);
    expect(reveal[0]!.closest("li")?.textContent).toContain("box-one");

    fireEvent.click(reveal[0]!);
    expect(await screen.findByText(/sk-local-42/)).toBeDefined();
  });

  it("offers no key control at all when the server sent no default endpoint", async () => {
    listModelEndpoints.mockResolvedValue(status({ local: null }));

    renderSection();
    await screen.findByText("box-one");

    expect(screen.queryByRole("button", { name: "Show key" })).toBeNull();
  });

  it("reloads the list after an endpoint is added", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));

    await waitFor(() => expect(listModelEndpoints).toHaveBeenCalledTimes(2));
  });
});

describe("AgentModelSection Test connection verdicts", () => {
  it("names a healthy box reachable and authenticated, and counts what it serves", async () => {
    probeLocalEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: ["a:1", "b:2"] });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    expect(
      await screen.findByText("Reachable and authenticated — serving 2 model(s)"),
    ).toBeDefined();
    // Success reads as success the way the connected pills do, so a green line
    // does not have to be parsed to be understood.
    expect(verdictLine().className).toContain("text-[var(--color-success)]");
    // The verdict is a pre-save answer only: the picker appears, nothing is stored.
    await screen.findByRole("button", { name: "Add endpoint" });
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("names a refused key an auth failure and shows the box's own words", async () => {
    probeLocalEndpoint.mockResolvedValue({
      ok: false,
      http_status: 401,
      error_detail: "token_not_found_in_db",
    });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    await screen.findByText("token_not_found_in_db");
    expect(verdictLine().textContent).toBe("Auth failed (HTTP 401): token_not_found_in_db");
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
    expect((screen.getByLabelText("Endpoint base URL") as HTMLInputElement).value).toBe(
      "http://box-three:11434/v1",
    );
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("names a 403 refusal the same auth failure a 401 is", async () => {
    probeLocalEndpoint.mockResolvedValue({
      ok: false,
      http_status: 403,
      error_detail: "key rejected by proxy",
    });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    await screen.findByText("key rejected by proxy");
    expect(verdictLine().textContent).toBe("Auth failed (HTTP 403): key rejected by proxy");
    expect(connectLocalEndpoint).not.toHaveBeenCalled();
  });

  it("names a box that never answers unreachable, never an auth failure", async () => {
    probeLocalEndpoint.mockResolvedValue({
      ok: false,
      http_status: null,
      error_detail: "connection failed: connection refused",
    });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    await screen.findByText("connection failed: connection refused");
    expect(verdictLine().textContent).toBe("Unreachable: connection failed: connection refused");
    // A dead box must never be dressed up as a key problem — that is the sentence
    // that sends a user hunting for a typo in a key that is fine.
    expect(screen.queryByText(/auth failed/i)).toBeNull();
    expect(verdictLine().className).not.toContain("text-[var(--color-success)]");
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
  });

  it("says the endpoint gave no reason when the probe sends none, instead of going blank", async () => {
    probeLocalEndpoint.mockResolvedValue({ ok: false, http_status: null });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    await screen.findByText("Unreachable:");
    expect(verdictLine().textContent).toBe("Unreachable: the endpoint gave no reason");
  });

  it("keeps the plain sentence when a reachable box serves no models", async () => {
    probeLocalEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: [] });

    renderSection();
    await screen.findByText("box-one");
    await typeAndTest();

    expect(
      await screen.findByText(
        "The endpoint answered but listed no models, so there is nothing to run.",
      ),
    ).toBeDefined();
    expect(screen.queryByText(/Reachable and authenticated/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
  });

  it("retires the reachable verdict the moment the address changes", async () => {
    renderSection();
    await screen.findByText("box-one");

    await typeDraft("http://box-three:11434/v1");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText(/Reachable and authenticated/);

    await typeDraft("http://box-four:11434/v1");

    // One source of truth: the verdict retires with the picker it unlocked, so no
    // stale "reachable" can survive for a box that was never dialled.
    expect(screen.queryByText(/Reachable and authenticated/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Add endpoint" })).toBeNull();
  });
});

describe("AgentModelSection key and OAuth providers", () => {
  it("still renders the three provider cards with their own controls", async () => {
    listModelEndpoints.mockResolvedValue(status({ connected: ["anthropic", "local"] }));

    renderSection();

    expect(await screen.findByText("Claude Code")).toBeDefined();
    expect(screen.getByText("Codex")).toBeDefined();
    expect(screen.getByText("OpenRouter")).toBeDefined();
    // Claude is already connected (so it offers Disconnect instead), Codex offers
    // both paths, and OpenRouter is key-only.
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDefined();
    expect(screen.getAllByRole("button", { name: "Sign in" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "API key" })).toHaveLength(2);
  });

  it("disconnects a key provider by provider and never sends local there", async () => {
    listModelEndpoints.mockResolvedValue(status({ connected: ["anthropic", "local"] }));

    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(disconnectAgentCredential).toHaveBeenCalledWith("anthropic"));
    for (const call of disconnectAgentCredential.mock.calls) {
      expect(call).not.toContain("local");
    }
  });

  it("has no trace of the single-endpoint flow it replaced", async () => {
    renderSection();
    await screen.findByText("box-one");

    expect(screen.queryByRole("button", { name: "Connect endpoint" })).toBeNull();
    expect(screen.queryByText("Local model")).toBeNull();
    // One action, one name. The retired label is matched by pattern rather than
    // spelled out, so this file holds no copy of it while still pinning that no
    // spelling of it can come back, and the operator's word is what renders.
    expect(screen.queryByRole("button", { name: /test\s*endpoint/i })).toBeNull();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDefined();
  });
});

// A saved row is only as fresh as the last list load, and the list dials EVERY box,
// so refreshing one row meant reloading all of them. These tests pin the per-row
// re-test: it dials the by-id route (the stored key never reaches the browser, so a
// browser-side probe would dial keyed boxes keyless and call healthy endpoints
// refused), it answers with the same named verdicts the add-form test answers with,
// and it changes nothing else on the page.
describe("AgentModelSection per-endpoint re-test", () => {
  // With several rows on screen the only globally-unique verdict line is the
  // add-form's, so a row's own verdict is always read inside its own <li>.
  function rowOf(name: string): HTMLElement {
    return screen.getByText(name).closest("li")!;
  }

  function rowVerdict(row: HTMLElement): HTMLElement {
    const label = within(row).getByText(
      /^(Unreachable:|Auth failed \(HTTP \d+\):|Reachable and authenticated|The endpoint answered)/,
    );
    return label.closest("p")!;
  }

  // Awaiting the row here is what makes the click land on a rendered list rather
  // than on the loading state, so no test has to remember to do it first.
  async function retest(name: string): Promise<HTMLElement> {
    const row = (await screen.findByText(name)).closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: `Test connection for ${name}` }));
    return row;
  }

  it("dials the by-id route for exactly the row that was tested, and nothing else", async () => {
    renderSection();
    await screen.findByText("box-two");

    const row = await retest("box-two");
    await waitFor(() => expect(probeSavedEndpoint).toHaveBeenCalledTimes(1));

    // The id is the only thing that may leave: the pre-save probe route needs a
    // base_url plus the PROBE_ONLY_MODEL placeholder, and a stored row has to be
    // re-dialled server-side with a key this page never holds.
    expect(probeSavedEndpoint.mock.calls[0]).toEqual(["cred-2"]);
    expect(probeLocalEndpoint).not.toHaveBeenCalled();
    // And a re-check is a question, not an edit: no reload (which would re-dial
    // every other box) and no row mutation.
    expect(listModelEndpoints).toHaveBeenCalledTimes(1);
    await within(row).findByText(/Reachable and authenticated/);
  });

  it("names a stored key that has stopped working, in the box's own words", async () => {
    probeSavedEndpoint.mockResolvedValue({
      ok: false,
      http_status: 401,
      error_detail: "token_not_found_in_db: no token with that hash",
    });
    renderSection();

    const row = await retest("box-two");
    await waitFor(() =>
      expect(rowVerdict(row).textContent).toBe(
        "Auth failed (HTTP 401): token_not_found_in_db: no token with that hash",
      ),
    );
    expect(rowVerdict(row).className).toContain("text-error");
    expect(rowVerdict(row).className).not.toContain("text-[var(--color-success)]");
  });

  it("calls a box that cannot be reached unreachable, never an auth failure", async () => {
    probeSavedEndpoint.mockResolvedValue({ ok: false, http_status: 0, error_detail: "no route to host" });
    renderSection();

    const row = await retest("box-one");
    await waitFor(() => expect(rowVerdict(row).textContent).toBe("Unreachable: no route to host"));
    expect(rowVerdict(row).className).toContain("text-error");
  });

  it("says so out loud when the box refuses to give a reason", async () => {
    probeSavedEndpoint.mockResolvedValue({ ok: false, http_status: 502 });
    renderSection();

    const row = await retest("box-one");
    await waitFor(() =>
      expect(rowVerdict(row).textContent).toBe("Unreachable: the endpoint gave no reason"),
    );
  });

  it("renders the honest no-models answer for a box that serves nothing", async () => {
    probeSavedEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: [] });
    renderSection();

    const row = await retest("box-one");
    await within(row).findByText(
      "The endpoint answered but listed no models, so there is nothing to run.",
    );
    expect(within(row).queryByText(/Reachable and authenticated/)).toBeNull();
  });

  it("styles a reachable box as a success, the same way the add-form test does", async () => {
    probeSavedEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: ["a:1", "b:2"] });
    renderSection();

    const row = await retest("box-two");
    await waitFor(() =>
      expect(rowVerdict(row).textContent).toBe("Reachable and authenticated — serving 2 model(s)"),
    );
    expect(rowVerdict(row).className).toContain("text-[var(--color-success)]");
  });

  it("reports a refused id through the error path, on that row only", async () => {
    probeSavedEndpoint.mockRejectedValue(new ApiError(404, "no such local endpoint"));
    renderSection();
    await screen.findByText("box-two");

    const row = await retest("box-two");

    // An ApiError is the route refusing the request, not the box refusing a dial:
    // the message renders and no verdict is claimed for a probe that never ran.
    await within(row).findByText("no such local endpoint");
    expect(within(row).queryByText(/^(Unreachable:|Auth failed|Reachable)/)).toBeNull();
    expect(within(rowOf("box-one")).queryByText("no such local endpoint")).toBeNull();
  });

  it("allows one dial per row and locks only that row's own button", async () => {
    let answerProbe!: (result: unknown) => void;
    probeSavedEndpoint.mockReturnValue(new Promise((resolve) => (answerProbe = resolve)));
    renderSection();
    await screen.findByText("box-two");

    const row = await retest("box-two");
    fireEvent.click(within(row).getByRole("button", { name: "Test connection for box-two" }));
    await waitFor(() => expect(probeSavedEndpoint).toHaveBeenCalledTimes(1));

    expect(within(row).getByRole("button", { name: "Test connection for box-two" })).toBeDisabled();
    // A slow box must not hold the other rows or the row's own Remove hostage.
    expect(
      within(rowOf("box-one")).getByRole("button", { name: "Test connection for box-one" }),
    ).not.toBeDisabled();
    expect(within(row).getByRole("button", { name: "Remove" })).not.toBeDisabled();

    answerProbe({ ok: true, http_status: 200, models: ["a:1"] });
    await within(row).findByText(/Reachable and authenticated/);
  });

  it("retires a row's verdict when a list reload brings fresher probe data", async () => {
    listModelEndpoints
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(
        status({ endpoints: [{ ...FIRST, models: ["freshly-probed:1"] }, SECOND] }),
      );
    renderSection();
    await screen.findByText("box-one");

    const row = await retest("box-one");
    await within(row).findByText(/Reachable and authenticated/);

    // Storing another endpoint reloads the list, and that load live-probes every
    // box — by definition fresher than a verdict clicked against an earlier load.
    await typeDraft("http://box-three:11434/v1", "sk-test");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add endpoint" }));
    await waitFor(() => expect(listModelEndpoints).toHaveBeenCalledTimes(2));

    expect(within(row).queryByText(/Reachable and authenticated/)).toBeNull();
    expect(within(row).getByText("freshly-probed:1")).toBeDefined();
  });
});
