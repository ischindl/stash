import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type Curator } from "@/lib/api";
import { ConfirmDialogProvider } from "../ConfirmDialog";
import CuratorsSection from "./CuratorsSection";

const listCurators = vi.fn();
const listModelEndpoints = vi.fn();
const listSessionFolders = vi.fn();
const patchCurator = vi.fn();
const createCurator = vi.fn();
const deleteCurator = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listCurators: () => listCurators(),
    listModelEndpoints: () => listModelEndpoints(),
    listSessionFolders: () => listSessionFolders(),
    patchCurator: (...args: unknown[]) => patchCurator(...args),
    createCurator: (...args: unknown[]) => createCurator(...args),
    deleteCurator: (...args: unknown[]) => deleteCurator(...args),
  };
});

// Two boxes: the server returns them oldest-first, so an unpinned curator resolves
// against box-one and only box-one.
const BOX_ONE = { id: "cred-1", name: "box-one", base_url: "http://box-one:11434/v1", models: ["llama3.1:8b", "qwen2:7b"] };
const BOX_TWO = { id: "cred-2", name: "box-two", base_url: "http://box-two:11434/v1", models: ["gemma3:4b"] };

function curator(over: Partial<Curator>): Curator {
  return {
    id: "cur-x",
    name: "Curator",
    model_provider: null,
    model_id: null,
    credential_id: null,
    digest_provider: null,
    digest_model_id: null,
    run_mode: "scheduled",
    schedule_cron: "0 2 * * *",
    curator_wiki: "internal",
    curator_folder_id: null,
    curated_through: null,
    last_run_at: null,
    last_run_outcome: null,
    last_run_error: null,
    next_run_at: "2026-09-09T02:00:00Z",
    ...over,
  };
}

// The workspace pair every account can have (the shared-wiki one only exists on the
// developer platform, so the section must cope with it being absent too) plus two
// project curators: one pinned to box-one and scheduled, one pinned to box-two and
// idle.
const WORKSPACE = curator({ id: "cur-ws", next_run_at: "2026-09-09T02:00:00Z" });
const EXTERNAL = curator({ id: "cur-ex", name: "External curator", curator_wiki: "external", schedule_cron: "0 5 * * *" });
const ATLAS = curator({
  id: "cur-a",
  name: "Curator: Project Atlas",
  curator_folder_id: "f-atlas",
  folder_name: "Project Atlas",
  model_provider: "local",
  credential_id: "cred-1",
  model_id: "llama3.1:8b",
  schedule_cron: "0 4 * * *",
  event_backlog: { distinct_events: 7, raw_rows: 9, distinct_sessions: 2 },
});
const BEANIE = curator({
  id: "cur-b",
  name: "Curator: Beanie",
  curator_folder_id: "f-beanie",
  folder_name: "Beanie",
  model_provider: "local",
  credential_id: "cred-2",
  model_id: "gemma3:4b",
  digest_provider: "local",
  digest_model_id: "gemma3:4b",
  schedule_cron: null,
  next_run_at: null,
});

function renderSection() {
  return render(
    <ConfirmDialogProvider>
      <CuratorsSection />
    </ConfirmDialogProvider>,
  );
}

async function rowNamed(name: string): Promise<HTMLElement> {
  const row = (await screen.findByText(name)).closest("li");
  if (!row) throw new Error(`no curator row for ${name}`);
  return row;
}

async function picker(label: string, row: HTMLElement) {
  const all = await screen.findAllByRole("combobox", { name: label });
  return all.find((el) => row.contains(el));
}

function optionValues(select: HTMLElement): string[] {
  return within(select as HTMLElement).getAllByRole("option").map((o) => o.textContent ?? "");
}

beforeEach(() => {
  vi.clearAllMocks();
  listCurators.mockResolvedValue([WORKSPACE, EXTERNAL, ATLAS, BEANIE]);
  listModelEndpoints.mockResolvedValue({ connected: ["local"], endpoints: [BOX_ONE, BOX_TWO], local: null });
  listSessionFolders.mockResolvedValue({
    folders: [
      { id: "f-default", name: "Default", is_default: true, share_wiki: false },
      { id: "f-atlas", name: "Project Atlas", is_default: false, share_wiki: false },
      { id: "f-beanie", name: "Beanie", is_default: false, share_wiki: false },
      { id: "f-notes", name: "Notes", is_default: false, share_wiki: true },
    ],
  });
  patchCurator.mockImplementation(async (_id: string, patch: Record<string, unknown>) => ({
    ...ATLAS,
    ...patch,
  }));
  createCurator.mockResolvedValue(ATLAS);
  deleteCurator.mockResolvedValue({ ok: true });
});

describe("CuratorsSection rows", () => {
  it("names every curator, workspace ones and project ones alike", async () => {
    renderSection();

    expect(await screen.findByText("Workspace curator")).toBeDefined();
    expect(screen.getByText("Shared-wiki curator")).toBeDefined();
    expect(screen.getByText("Project Atlas")).toBeDefined();
    expect(screen.getByText("Beanie")).toBeDefined();
  });

  it("says so when the project behind a curator is gone", async () => {
    listCurators.mockResolvedValue([
      curator({ id: "cur-gone", curator_folder_id: "f-gone", folder_name: null }),
    ]);

    renderSection();

    expect(await screen.findByText("Curator of a deleted project")).toBeDefined();
  });

  it("shows the backlog the server counted and the schedule state it reports", async () => {
    renderSection();

    // The row also carries a switch labelled "Idle", so the state chip is picked out
    // by its own element rather than by the shared word.
    const atlas = await rowNamed("Project Atlas");
    expect(within(atlas).getByText("7 unread events")).toBeDefined();
    expect(within(atlas).queryByText("Idle", { selector: "span.rounded-full" })).toBeNull();
    expect(within(atlas).getByRole("switch").getAttribute("aria-checked")).toBe("false");

    const beanie = await rowNamed("Beanie");
    expect(within(beanie).getByText("Idle", { selector: "span.rounded-full" })).toBeDefined();
  });
});

describe("CuratorsSection model picker", () => {
  it("writes a choice as the local provider plus the box and the model", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");

    fireEvent.change((await picker("Model", atlas))!, {
      target: { value: "cred-2⋄gemma3:4b" },
    });

    await waitFor(() =>
      expect(patchCurator).toHaveBeenCalledWith("cur-a", {
        model_provider: "local",
        credential_id: "cred-2",
        model_id: "gemma3:4b",
      }),
    );
  });

  it("clears all three pins explicitly on inherit, which is what inheritance means", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");

    fireEvent.change((await picker("Model", atlas))!, { target: { value: "" } });

    await waitFor(() =>
      expect(patchCurator).toHaveBeenCalledWith("cur-a", {
        model_provider: null,
        model_id: null,
        credential_id: null,
      }),
    );
  });

  it("offers the model list grouped by box and keeps an orphaned model visible", async () => {
    listCurators.mockResolvedValue([
      curator({
        id: "cur-orphan",
        curator_folder_id: "f-atlas",
        folder_name: "Project Atlas",
        model_provider: "local",
        credential_id: "cred-1",
        model_id: "removed-last-night:1",
      }),
    ]);

    renderSection();
    const atlas = await rowNamed("Project Atlas");
    const select = (await picker("Model", atlas))!;

    // The stored model is still what this curator runs, so it must read as the
    // current choice rather than falling back to an inheritance label.
    expect((select as HTMLSelectElement).value).toBe("cred-1⋄removed-last-night:1");
    expect(optionValues(select)).toContain("removed-last-night:1 (not on this endpoint)");
    expect(within(select as HTMLElement).getByText("qwen2:7b")).toBeDefined();
    expect(within(select as HTMLElement).getByText("gemma3:4b")).toBeDefined();
  });

  it("words inheritance the way each row kind actually resolves", async () => {
    renderSection();
    const workspace = await rowNamed("Workspace curator");
    const atlas = await rowNamed("Project Atlas");

    expect(optionValues((await picker("Model", workspace))!)[0]).toBe(
      "Default (oldest connected endpoint)",
    );
    expect(optionValues((await picker("Model", atlas))!)[0]).toBe("Inherit the default curator");
  });
});

describe("CuratorsSection digest picker", () => {
  it("offers a digest on project curators and never on a workspace one", async () => {
    renderSection();
    await screen.findByText("Project Atlas");

    expect(screen.getAllByRole("combobox", { name: "Digest" })).toHaveLength(2);
    const workspace = await rowNamed("Workspace curator");
    const external = await rowNamed("Shared-wiki curator");
    expect(within(workspace).queryByRole("combobox", { name: "Digest" })).toBeNull();
    expect(within(external).queryByText(/Digest/)).toBeNull();
  });

  it("offers only what the box that curator pins serves", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");
    const beanie = await rowNamed("Beanie");

    // Atlas pins box-one, Beanie pins box-two: a digest inherits that same pin, so
    // the two rows must not see each other's models.
    expect(optionValues((await picker("Digest", atlas))!)).toEqual([
      "None (single-phase)",
      "llama3.1:8b",
      "qwen2:7b",
    ]);
    expect(within(atlas).getByText("on box-one")).toBeDefined();
    expect(optionValues((await picker("Digest", beanie))!)).toEqual([
      "None (single-phase)",
      "gemma3:4b",
    ]);
    expect(within(beanie).getByText("on box-two")).toBeDefined();
  });

  it("writes a digest as the local provider plus the model, and clears both on none", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");
    fireEvent.change((await picker("Digest", atlas))!, { target: { value: "qwen2:7b" } });
    await waitFor(() =>
      expect(patchCurator).toHaveBeenCalledWith("cur-a", {
        digest_provider: "local",
        digest_model_id: "qwen2:7b",
      }),
    );

    const beanie = await rowNamed("Beanie");
    fireEvent.change((await picker("Digest", beanie))!, { target: { value: "" } });
    await waitFor(() =>
      expect(patchCurator).toHaveBeenCalledWith("cur-b", {
        digest_provider: null,
        digest_model_id: null,
      }),
    );
  });
});

describe("CuratorsSection schedule", () => {
  it("saves an edited cron and refuses to send a blank one", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");
    const field = within(atlas).getByLabelText("Schedule") as HTMLInputElement;

    expect(field.value).toBe("0 4 * * *");
    fireEvent.change(field, { target: { value: " 15 6 * * * " } });
    fireEvent.click(within(atlas).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchCurator).toHaveBeenCalledWith("cur-a", { schedule_cron: "15 6 * * *" }));

    fireEvent.change(field, { target: { value: "   " } });
    expect((within(atlas).getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("has no idle switch on a workspace curator, which the server refuses", async () => {
    renderSection();
    await screen.findByText("Project Atlas");

    expect(screen.getAllByRole("switch")).toHaveLength(2);
    const workspace = await rowNamed("Workspace curator");
    expect(within(workspace).queryByRole("switch")).toBeNull();
  });

  it("clears the schedule to null when the switch is turned on", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");

    fireEvent.click(within(atlas).getByRole("switch"));

    await waitFor(() => expect(patchCurator).toHaveBeenCalledWith("cur-a", { schedule_cron: null }));
  });

  it("installs the nightly cron when a curator is switched off idle", async () => {
    renderSection();
    const beanie = await rowNamed("Beanie");
    const idleSwitch = within(beanie).getByRole("switch");

    expect(idleSwitch.getAttribute("aria-checked")).toBe("true");
    expect(within(beanie).queryByRole("button", { name: "Save" })).toBeNull();
    expect((within(beanie).getByLabelText("Schedule") as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(idleSwitch);

    await waitFor(() => expect(patchCurator).toHaveBeenCalledWith("cur-b", { schedule_cron: "0 3 * * *" }));
  });

  it("renders the server's refusal instead of a silent no-op", async () => {
    patchCurator.mockRejectedValue(new ApiError(400, "invalid schedule_cron"));

    renderSection();
    const atlas = await rowNamed("Project Atlas");
    fireEvent.click(within(atlas).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("invalid schedule_cron")).toBeDefined();
    expect(listCurators).toHaveBeenCalledTimes(1);
  });
});

describe("CuratorsSection create and delete", () => {
  it("offers only projects that have no curator and no Default catch-all", async () => {
    renderSection();
    const select = (await screen.findByRole("combobox", { name: "Project" }))!;

    expect(optionValues(select as HTMLElement)).toEqual(["Choose a project", "Notes"]);
  });

  it("creates a curator for the chosen project and reloads the list", async () => {
    renderSection();
    const select = (await screen.findByRole("combobox", { name: "Project" }))!;

    fireEvent.change(select, { target: { value: "f-notes" } });
    fireEvent.click(screen.getByRole("button", { name: "Add curator" }));

    await waitFor(() => expect(createCurator).toHaveBeenCalledWith({ folder_id: "f-notes" }));
    await waitFor(() => expect(listCurators).toHaveBeenCalledTimes(2));
  });

  it("deletes a project curator only after a confirmation", async () => {
    renderSection();
    const atlas = await rowNamed("Project Atlas");

    fireEvent.click(within(atlas).getByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(deleteCurator).not.toHaveBeenCalled();

    fireEvent.click(within(atlas).getByRole("button", { name: "Delete" }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete curator" }));

    await waitFor(() => expect(deleteCurator).toHaveBeenCalledWith("cur-a"));
    await waitFor(() => expect(listCurators).toHaveBeenCalledTimes(2));
  });

  it("offers no delete on the permanent workspace rows", async () => {
    renderSection();
    await screen.findByText("Project Atlas");

    // Atlas and Beanie are the only deletable rows; the two workspace rows refuse deletion.
    expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
  });
});
