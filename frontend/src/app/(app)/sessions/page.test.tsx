import { cleanup, render as renderBase, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionsPage from "./page";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";
import type { SessionSummary } from "@/lib/api";
import type { User } from "@/lib/types";

function render(ui: ReactNode) {
  return renderBase(ui, { wrapper: ConfirmDialogProvider });
}

const auth = vi.hoisted(() => ({
  user: null as unknown,
  loading: false,
}));

const api = vi.hoisted(() => ({
  API_BASE: "",
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  deleteSession: vi.fn(),
  listAgentNames: vi.fn(),
  listMySessions: vi.fn(),
  listSessionFolders: vi.fn(),
  getToken: vi.fn(() => null),
  getMe: vi.fn(),
  clearToken: vi.fn(),
  revokeStoredApiKey: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/hooks/useAuth", () => ({ useAuth: () => auth }));
vi.mock("@/components/BreadcrumbContext", () => ({ useBreadcrumbs: vi.fn() }));
vi.mock("@/lib/api", () => api);

vi.mock("@/lib/pins", () => ({
  usePins: () => ({
    pinnedIds: [],
    pinnedSet: new Set<string>(),
    isPinned: () => false,
    toggle: vi.fn(),
  }),
}));

const USER: User = {
  id: "user-1",
  developer_platform_only: false,
  name: "founder",
  display_name: "Founder",
  email: "founder@example.test",
  description: "",
  created_at: "2026-01-01T00:00:00Z",
  last_seen: "2026-09-20T00:00:00Z",
  show_tools_and_chat: false,
};

function session(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: "sess-1",
    id: "row-1",
    title: "Morning deploy debug",
    linear_tickets: [],
    owner_user_id: "user-1",
    user_name: "founder",
    agent_name: "claude",
    session_folder_name: null,
    event_count: 42,
    started_at: "2026-09-20T09:00:00Z",
    last_event_at: "2026-09-20T09:30:00Z",
    ...overrides,
  };
}

describe("Sessions list width cap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = USER;
    auth.loading = false;
    api.listMySessions.mockResolvedValue({
      sessions: [session()],
      hasMore: false,
    });
    api.listSessionFolders.mockResolvedValue({ folders: [] });
    api.listAgentNames.mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("caps the page at the 1440px wide-surface convention, not the old max-w-5xl", async () => {
    const { container } = render(<SessionsPage />);
    await screen.findByText("Morning deploy debug");

    const page = container.querySelector("div.mx-auto");
    expect(page).not.toBeNull();
    expect(page!.className).toContain("max-w-[1440px]");
    expect(page!.className).not.toContain("max-w-5xl");
  });

  it("keeps the table min-width invariant the widened container still satisfies", async () => {
    const { container } = render(<SessionsPage />);
    await screen.findByText("Morning deploy debug");

    // The row/header grids keep their md:min-w floor; the 1440px cap leaves
    // 1344px of content column, so the grid never needs sideways scroll here.
    expect(
      container.querySelectorAll('[class*="md:min-w-[1016px]"]').length
    ).toBeGreaterThan(0);
  });
});
