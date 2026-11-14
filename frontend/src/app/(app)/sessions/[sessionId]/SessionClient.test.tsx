import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionViewerPage from "./SessionClient";
import { SessionDetailSkeleton, SessionsListSkeleton } from "@/components/SkeletonStates";
import type { SessionDetail, SessionEventsPage } from "@/lib/api";
import type { User } from "@/lib/types";

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
  fetchAuthed: vi.fn(),
  getSessionDetail: vi.fn(),
  getSessionEventsPage: vi.fn(),
  listSkills: vi.fn(),
  materializeSession: vi.fn(),
  renameSession: vi.fn(),
  trashItem: vi.fn(),
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
vi.mock("@/components/ConfirmDialog", () => ({ useConfirm: () => vi.fn() }));
vi.mock("@/components/ShellChromeContext", () => ({ useShareAction: vi.fn() }));
vi.mock("@/lib/scope-store", () => ({ getScope: () => null }));
vi.mock("@/lib/workspace-store", () => ({ useTabTitle: vi.fn() }));
vi.mock("@/components/share/ResourceShareButton", () => ({ default: () => null }));
vi.mock("@/components/DownloadMenu", () => ({ default: () => null }));
vi.mock("@/components/content/EditableTitle", () => ({ default: () => null }));
vi.mock("@/lib/api", () => api);

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

function detail(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "det-1",
    owner_user_id: "user-1",
    session_id: "sess-1",
    title: "Morning deploy debug",
    agent_name: "claude",
    cwd: null,
    files_touched: [],
    linear_tickets: [],
    started_at: "2026-09-20T09:00:00Z",
    finished_at: null,
    created_by: null,
    artifacts: [],
    ...overrides,
  };
}

const EVENTS_PAGE: SessionEventsPage = {
  events: [
    {
      id: "e-1",
      role: "assistant",
      agent_name: "claude",
      content: "hello transcript",
      tool_name: null,
      created_at: "2026-09-20T09:01:00Z",
    },
  ],
  total: 1,
  has_more: false,
};

// Same pattern as PageClient.test.tsx: the capped wrapper is the grid that
// holds the transcript <main>.
function layoutWrapper(container: HTMLElement): HTMLElement {
  const main = container.querySelector("main.min-w-0");
  if (!main?.parentElement) throw new Error("layout wrapper not found");
  return main.parentElement;
}

class TestIntersectionObserver {
  observe() {}
  disconnect() {}
}

describe("Session detail width cap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = USER;
    auth.loading = false;
    api.getSessionDetail.mockResolvedValue(detail());
    api.getSessionEventsPage.mockResolvedValue(EVENTS_PAGE);
    vi.stubGlobal(
      "IntersectionObserver",
      TestIntersectionObserver as unknown as typeof IntersectionObserver
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("caps the transcript grid at the 1440px wide-surface convention with the aside intact", async () => {
    render(<SessionViewerPage sessionId="sess-1" />);
    await screen.findByText("hello transcript");

    const wrapper = layoutWrapper(document.body);
    expect(wrapper.className).toContain("max-w-[1440px]");
    expect(wrapper.className).not.toContain("max-w-[1100px]");
    // The sidebar column stays — only the cap widens.
    expect(wrapper.className).toContain("lg:grid-cols-[minmax(0,1fr)_260px]");
  });
});

describe("Sessions skeletons mirror the widened cap", () => {
  afterEach(() => {
    cleanup();
  });

  it("caps the list skeleton at 1440px so loading and loaded share one layout", () => {
    const { container } = render(<SessionsListSkeleton />);
    const page = container.querySelector("div.mx-auto");
    expect(page).not.toBeNull();
    expect(page!.className).toContain("max-w-[1440px]");
    expect(page!.className).not.toContain("max-w-5xl");
  });

  it("caps the detail skeleton at 1440px with the aside template intact", () => {
    const { container } = render(<SessionDetailSkeleton />);
    const page = container.querySelector("div.mx-auto");
    expect(page).not.toBeNull();
    expect(page!.className).toContain("max-w-[1440px]");
    expect(page!.className).not.toContain("max-w-[1100px]");
    expect(page!.className).toContain("lg:grid-cols-[minmax(0,1fr)_260px]");
  });
});
