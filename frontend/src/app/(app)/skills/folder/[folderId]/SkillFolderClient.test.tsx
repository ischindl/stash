import { cleanup, render as renderBase, screen, waitFor } from "@testing-library/react";
import { useEffect, useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SkillFolderClient from "./SkillFolderClient";
import { getFolderContents, listSkills, type FolderBackedSkill, type SkillPublishInfo } from "@/lib/api";
import { useBreadcrumbs } from "@/components/BreadcrumbContext";
import { useShareAction } from "@/components/ShellChromeContext";
import { ConfirmDialogProvider } from "@/components/ConfirmDialog";

function render(ui: ReactNode) {
  return renderBase(ui, { wrapper: ConfirmDialogProvider });
}

/**
 * Stands in for the shell chrome: the panel publishes its share action to
 * `useShareAction`, and only the chrome renders it. Re-reads the latest action
 * until the test's assertion holds, so a publish that lands a moment after the
 * folder contents is waited for rather than guessed at with a fixed delay.
 */
function ShareActionHost() {
  const [action, setAction] = useState<ReactNode>(null);
  useEffect(() => {
    const id = setInterval(() => {
      setAction(vi.mocked(useShareAction).mock.calls.at(-1)?.[0] ?? null);
    }, 10);
    return () => clearInterval(id);
  }, []);
  return <>{action}</>;
}

const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

const params = vi.hoisted(() => ({
  folderId: "folder-sub",
}));

vi.mock("next/navigation", () => ({
  useParams: () => params,
  useRouter: () => router,
}));

vi.mock("@/lib/api", () => ({
  getFolderContents: vi.fn(),
  listSkills: vi.fn(),
  trashItem: vi.fn(),
}));

vi.mock("@/lib/skillNavigationCache", () => ({
  refreshSidebar: vi.fn(() => Promise.resolve()),
}));

vi.mock("@/components/BreadcrumbContext", () => ({
  useBreadcrumbs: vi.fn(),
}));

vi.mock("@/components/ShellChromeContext", () => ({
  useShareAction: vi.fn(),
}));

vi.mock("@/components/share/ResourceShareButton", () => ({
  default: ({ resourceUrlPath }: { resourceUrlPath?: string }) => (
    <button data-share-url={resourceUrlPath}>Share resource</button>
  ),
}));

vi.mock("@/components/skill/SkillShareButton", () => ({
  default: () => <button>Share</button>,
}));

vi.mock(
  "@/components/content/file-browser/FileBrowser",
  () => ({
    default: ({ folderHrefBase }: { folderHrefBase?: string }) => (
      <div data-testid="file-browser" data-href-base={folderHrefBase} />
    ),
  }),
);

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => ({
    user: { id: "user-1", name: "henry", display_name: "Henry" },
    loading: false,
  }),
}));

const PUBLISH: SkillPublishInfo = {
  id: "skill-1",
  slug: "launch-plan",
  mcp_url: "http://localhost:3456/api/v1/mcp/skills/launch-plan",
  discoverable: true,
  cover_image_url: null,
  icon_url: null,
  view_count: 3,
};

function folderSkill(published: SkillPublishInfo | null): FolderBackedSkill {
  return {
    backing: "folder",
    folder_id: "folder-root",
    source_ref: null,
    source_id: null,
    source_name: null,
    name: "Launch Plan",
    description: "",
    when_to_use: "",
    version: "1.0",
    mcp_exposed: true,
    file_count: 2,
    updated_at: "2026-08-25T00:00:00Z",
    has_instructions: true,
    published,
  };
}

// The skill's own root folder — the only place the publish affordances live.
function mockSkillRoot() {
  vi.mocked(getFolderContents).mockResolvedValue({
    folder: {
      id: "folder-root",
      name: "Launch Plan",
      parent_folder_id: null,
      is_skill: true,
    },
    breadcrumbs: [{ id: "folder-root", name: "Launch Plan", is_skill: true }],
    subfolders: [],
    pages: [],
    files: [],
    tables: [],
  });
}

describe("SkillFolderClient", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    params.folderId = "folder-sub";
    vi.mocked(getFolderContents).mockResolvedValue({
      folder: {
        id: "folder-sub",
        name: "research",
        parent_folder_id: "folder-root",
        is_skill: false,
      },
      breadcrumbs: [
        { id: "folder-top", name: "Projects", is_skill: false },
        { id: "folder-root", name: "Launch Plan", is_skill: true },
        { id: "folder-sub", name: "research", is_skill: false },
      ],
      subfolders: [],
      pages: [],
      files: [],
      tables: [],
    });
    vi.mocked(listSkills).mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("roots breadcrumbs at Skills and trails from the skill folder", async () => {
    render(<SkillFolderClient folderId="folder-sub" />);

    await screen.findByTestId("file-browser");

    const crumbs = vi.mocked(useBreadcrumbs).mock.calls.at(-1)?.[0];
    // Crumbs point at /skills/folder/<id>, not /skills/<id> — the latter is
    // the published-slug route and renders "Skill not found" for a folder id.
    expect(crumbs).toEqual([
      { label: "Skills", href: "/skills" },
      { label: "Launch Plan", href: "/skills/folder/folder-root" },
      { label: "research" },
    ]);
    // Ancestors above the skill root (plain folders) stay out of the trail.
    expect(crumbs?.some((c: { label: string }) => c.label === "Projects")).toBe(false);
  });

  it("keeps folder navigation on the skill browse route", async () => {
    render(<SkillFolderClient folderId="folder-sub" />);

    const browser = await screen.findByTestId("file-browser");
    // FileBrowser builds `${folderHrefBase}/${id}`, so a subfolder inside a
    // skill must resolve to /skills/folder/<id>.
    expect(browser).toHaveAttribute("data-href-base", "/skills/folder");
  });

  it("bounces non-skill folders back to the Files route", async () => {
    vi.mocked(getFolderContents).mockResolvedValue({
      folder: {
        id: "folder-sub",
        name: "plain",
        parent_folder_id: null,
        is_skill: false,
      },
      breadcrumbs: [{ id: "folder-sub", name: "plain", is_skill: false }],
      subfolders: [],
      pages: [],
      files: [],
      tables: [],
    });

    render(<SkillFolderClient folderId="folder-sub" />);

    await waitFor(() =>
      expect(router.replace).toHaveBeenCalledWith("/folders/folder-sub"),
    );
  });

  // The Share dialog turns this path into the link the user copies. Pointing
  // it at /skills/<folderId> hands the recipient a "Skill not found" page.
  it("shares the skill with a link the recipient can open", async () => {
    mockSkillRoot();

    render(<SkillFolderClient folderId="folder-root" />);
    await screen.findByTestId("file-browser");

    const action = vi.mocked(useShareAction).mock.calls.at(-1)?.[0];
    render(<>{action}</>);

    expect(screen.getByText("Share resource")).toHaveAttribute(
      "data-share-url",
      "/skills/folder/folder-root",
    );
  });

  // Once a skill is published the panel's remaining job is to point at the
  // live page: the title is the page heading, the link is the published URL.
  it("links the panel to the published page once the skill is live", async () => {
    mockSkillRoot();
    vi.mocked(listSkills).mockResolvedValue([folderSkill(PUBLISH)]);

    render(<SkillFolderClient folderId="folder-root" />);
    render(<ShareActionHost />);
    await screen.findByTestId("file-browser");

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Public page" })).toHaveAttribute(
        "href",
        `/skills/${PUBLISH.slug}`,
      ),
    );
    // The panel is never a second place to be told to publish.
    expect(screen.queryByText("Convert to Skill")).toBeNull();
  });

  // Unpublishing deletes the URL, so the link has to leave with it.
  it("keeps the page link off a skill that is not published", async () => {
    mockSkillRoot();
    vi.mocked(listSkills).mockResolvedValue([folderSkill(null)]);

    render(<SkillFolderClient folderId="folder-root" />);
    render(<ShareActionHost />);
    await screen.findByTestId("file-browser");

    // Past the point where a published skill would have had its link, there is none.
    await waitFor(() => expect(screen.getByText("Share resource")).toBeTruthy());
    expect(screen.queryByRole("link", { name: "Public page" })).toBeNull();
  });
});
