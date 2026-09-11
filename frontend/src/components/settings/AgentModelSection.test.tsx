import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PROBE_ONLY_MODEL, type ModelEndpointsStatus } from "@/lib/api";
import { ConfirmDialogProvider } from "../ConfirmDialog";
import AgentModelSection from "./AgentModelSection";

const listModelEndpoints = vi.fn();
const probeLocalEndpoint = vi.fn();
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

beforeEach(() => {
  vi.clearAllMocks();
  listModelEndpoints.mockResolvedValue(status());
  probeLocalEndpoint.mockResolvedValue({ ok: true, http_status: 200, models: ["new-model:1"] });
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

    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));

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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));

    expect(await screen.findByText("upstream refused the connection")).toBeDefined();
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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Test endpoint" }));
    await screen.findByRole("button", { name: "Add endpoint" });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));

    await waitFor(() => expect(listModelEndpoints).toHaveBeenCalledTimes(2));
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
  });
});
