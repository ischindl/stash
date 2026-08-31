/** The console's session list is where a developer decides which projects the
 *  shared wiki may read, so the two things this page must get right are the shape
 *  of the list — project groups, with unfiled and Default material folded into a
 *  trailing Unsorted group that has no switch — and the calls behind the controls:
 *  a clearance change names the project, a move names the row id. */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  assignDeveloperSessions,
  listDeveloperSessions,
  listSessionFolders,
  setDeveloperProjectWiki,
  type DeveloperSession,
} from "@/lib/api";
import DeveloperSessionsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// The gate is its own screen with its own workspace fetch; these tests are about
// the list behind it.
vi.mock("@/components/developer/DeveloperGate", () => ({
  default: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/lib/api", () => ({
  listDeveloperSessions: vi.fn(),
  listSessionFolders: vi.fn(),
  setDeveloperProjectWiki: vi.fn(),
  assignDeveloperSessions: vi.fn(),
  uploadTranscript: vi.fn(),
}));

const folders = [
  { id: "folder-a", name: "acme-diesel", is_default: false, share_wiki: true },
  { id: "folder-b", name: "heavi-sync", is_default: false, share_wiki: false },
  { id: "folder-default", name: "Default", is_default: true, share_wiki: false },
];

function session(overrides: Partial<DeveloperSession> = {}): DeveloperSession {
  return {
    id: "row-1",
    session_id: "s-1",
    agent_name: "claude",
    title: "Session",
    cwd: null,
    event_count: 1,
    started_at: "2026-01-01T00:00:00Z",
    last_event_at: "2026-01-02T00:00:00Z",
    user_id: null,
    user_name: null,
    user_external_id: null,
    session_folder_id: null,
    session_folder_name: null,
    session_folder_is_default: null,
    session_folder_share_wiki: null,
    ...overrides,
  };
}

const sessions: DeveloperSession[] = [
  session({
    id: "row-a1",
    session_id: "s-a1",
    title: "Diesel teardown",
    session_folder_id: "folder-a",
    session_folder_name: "acme-diesel",
    session_folder_is_default: false,
    session_folder_share_wiki: true,
    last_event_at: "2026-03-02T00:00:00Z",
  }),
  session({
    id: "row-u1",
    session_id: "s-u1",
    title: "Payments triage",
    cwd: "/srv/payments-api",
    last_event_at: "2026-03-01T00:00:00Z",
  }),
  session({
    id: "row-d1",
    session_id: "s-d1",
    title: "Onboarding chat",
    cwd: "/srv/onboarding",
    session_folder_id: "folder-default",
    session_folder_name: "Default",
    session_folder_is_default: true,
    session_folder_share_wiki: false,
    last_event_at: "2026-02-01T00:00:00Z",
  }),
];

beforeEach(() => {
  vi.mocked(listDeveloperSessions).mockResolvedValue({ sessions });
  vi.mocked(listSessionFolders).mockResolvedValue({ folders });
  vi.mocked(setDeveloperProjectWiki).mockResolvedValue(folders[0]);
  vi.mocked(assignDeveloperSessions).mockResolvedValue({ ok: true, moved: 1 });
});

afterEach(cleanup);

async function renderPage() {
  render(<DeveloperSessionsPage />);
  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
}

/** Opens one dropdown and scopes queries to it, so an option name is only ever
 *  matched against the control it belongs to. */
function openMenu(triggerName: string) {
  const trigger = screen.getByRole("button", { name: triggerName });
  fireEvent.click(trigger);
  return within(trigger.parentElement as HTMLElement);
}

function unsortedBody() {
  // The upload picker's closed trigger reads "Unsorted" too (it is the default
  // destination), so the group header is found inside the table.
  const table = within(screen.getByRole("table"));
  return table.getByText("Unsorted").closest("tbody") as HTMLElement;
}

/** The project name is also a move control's current-value label, so the switch
 *  in the group header is the unambiguous way into a project's rows. */
function projectBody(projectName: string) {
  return screen.getByRole("switch", { name: new RegExp(projectName) }).closest("tbody") as HTMLElement;
}

describe("developer sessions list", () => {
  it("groups rows by project, with unfiled and Default material last", async () => {
    await renderPage();

    const acme = projectBody("acme-diesel");
    expect(within(acme).getByText("Diesel teardown")).toBeInTheDocument();

    const unsorted = unsortedBody();
    expect(within(unsorted).getByText("Payments triage")).toBeInTheDocument();
    expect(within(unsorted).getByText("Onboarding chat")).toBeInTheDocument();
    // An unfiled row says where it came from rather than repeating the group.
    expect(within(unsorted).getByText("payments-api")).toBeInTheDocument();
  });

  it("offers the shared-wiki switch on projects only", async () => {
    await renderPage();

    expect(screen.getByRole("switch", { name: /acme-diesel/ })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    expect(within(unsortedBody()).queryByRole("switch")).toBeNull();
  });

  it("clears a project through the console route", async () => {
    await renderPage();

    fireEvent.click(screen.getByRole("switch", { name: /acme-diesel/ }));

    await waitFor(() => expect(setDeveloperProjectWiki).toHaveBeenCalledWith("folder-a", false));
  });

  it("moves a session by row id rather than its developer-facing session id", async () => {
    await renderPage();

    const menu = openMenu("Move Payments triage to another project");
    fireEvent.click(menu.getByRole("option", { name: "heavi-sync" }));

    await waitFor(() => expect(assignDeveloperSessions).toHaveBeenCalledWith(["row-u1"], "folder-b"));
  });

  it("lists project folders in the upload picker and leaves Default out", async () => {
    await renderPage();

    const menu = openMenu("Project to file this upload under");

    // A selected option prefixes its label with a check glyph, so names match
    // loosely here — the point is which folders the picker offers.
    expect(menu.getByRole("option", { name: /acme-diesel/ })).toBeInTheDocument();
    expect(menu.getByRole("option", { name: /heavi-sync/ })).toBeInTheDocument();
    expect(menu.queryByRole("option", { name: /Default/ })).toBeNull();
    expect(menu.getByRole("option", { name: /Unsorted/ })).toBeInTheDocument();
  });

  it("states the routing rules the switches sit on top of", async () => {
    await renderPage();

    expect(screen.getByRole("heading", { name: "What feeds which wiki" })).toBeInTheDocument();
    // The two things a lone switch cannot imply: that nothing is shared until a
    // project is opened, and that moving material is not every teammate's power.
    expect(screen.getByText(/every project starts OFF/)).toBeInTheDocument();
    expect(screen.getByText(/cannot move a session/)).toBeInTheDocument();
  });
});
