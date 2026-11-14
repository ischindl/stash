import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ResourceShareButton from "./ResourceShareButton";
import {
  getShareState,
  shareObjectByEmail,
  unshareObject,
  updateGeneralAccess,
} from "../../lib/api";

vi.mock("../../lib/api", () => ({
  getShareState: vi.fn(),
  shareObjectByEmail: vi.fn(),
  unshareObject: vi.fn(),
  updateGeneralAccess: vi.fn(),
}));

const currentUser = {
  id: "user-1",
  developer_platform_only: false,
  name: "henry",
  display_name: "Henry Dowling",
  email: "henry@example.com",
  description: "",
  created_at: "2026-05-11T00:00:00Z",
  last_seen: "2026-05-11T00:00:00Z",
  show_tools_and_chat: false,
};

describe("ResourceShareButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    vi.mocked(getShareState).mockResolvedValue({
      shares: [
        {
          principal_type: "user",
          principal_id: "user-2",
          label: "Ada Lovelace",
          email: "ada@example.com",
          permission: "read",
          pending: false,
        },
      ],
      generalAccess: "none",
      mcpUrl: null,
    });
    vi.mocked(shareObjectByEmail).mockResolvedValue(undefined);
    vi.mocked(unshareObject).mockResolvedValue(undefined);
    vi.mocked(updateGeneralAccess).mockImplementation(
      async (_type, _id, permission) => permission,
    );
  });

  afterEach(() => {
    cleanup();
  });

  it("shows file access and copies the canonical file URL", async () => {
    render(
      <ResourceShareButton
        objectType="file"
        objectId="file-1"
        resourceName="launch.png"
        resourceUrlPath="/f/file-1"
        currentUser={currentUser}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Share" }));

    expect(
      await screen.findByRole("dialog", { name: "Share launch.png" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Henry Dowling (you)")).toBeInTheDocument();
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Restricted")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      `${window.location.origin}/f/file-1`,
    );
    expect(await screen.findByText("Link copied.")).toBeInTheDocument();
  });

  it("invites people directly to the resource", async () => {
    vi.mocked(getShareState)
      .mockResolvedValueOnce({ shares: [], generalAccess: "none", mcpUrl: null })
      .mockResolvedValueOnce({
        shares: [
          {
            principal_type: "user",
            principal_id: null,
            label: "ada@example.com",
            email: "ada@example.com",
            permission: "write",
            pending: true,
          },
        ],
        generalAccess: "none",
        mcpUrl: null,
      });

    render(
      <ResourceShareButton
        objectType="table"
        objectId="table-1"
        resourceName="Prospects"
        resourceUrlPath="/tables/table-1"
        currentUser={currentUser}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    await screen.findByRole("dialog", { name: "Share Prospects" });

    fireEvent.change(screen.getByLabelText("Add people"), {
      target: { value: "ada@example.com" },
    });
    await userEvent.click(screen.getByLabelText("Invite permission"));
    await userEvent.click(await screen.findByRole("option", { name: "Can edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));

    await waitFor(() =>
      expect(shareObjectByEmail).toHaveBeenCalledWith(
        "table",
        "table-1",
        "ada@example.com",
        "write",
      ),
    );
    expect(await screen.findByText("ada@example.com")).toBeInTheDocument();
    expect(screen.getByText("Invited")).toBeInTheDocument();
  });

  it("changes an existing person's permission", async () => {
    render(
      <ResourceShareButton
        objectType="page"
        objectId="page-1"
        resourceName="Blog post outline"
        resourceUrlPath="/p/page-1"
        currentUser={currentUser}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    await screen.findByText("Ada Lovelace");

    await userEvent.click(screen.getByLabelText("Change permission"));
    await userEvent.click(await screen.findByRole("option", { name: "Can comment" }));

    await waitFor(() =>
      expect(shareObjectByEmail).toHaveBeenCalledWith(
        "page",
        "page-1",
        "ada@example.com",
        "comment",
      ),
    );
    expect(await screen.findByText("Access updated.")).toBeInTheDocument();
  });

  it("offers the folder's MCP URL to agents once the link is on", async () => {
    vi.mocked(getShareState).mockResolvedValue({
      shares: [],
      generalAccess: "read",
      mcpUrl: "http://localhost:3457/api/v1/mcp/folders/abc123",
    });

    render(
      <ResourceShareButton
        objectType="folder"
        objectId="folder-1"
        resourceName="Research"
        resourceUrlPath="/folders/folder-1"
        currentUser={currentUser}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Share" }));
    await screen.findByRole("dialog", { name: "Share Research" });

    expect(
      await screen.findByText("http://localhost:3457/api/v1/mcp/folders/abc123"),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Copy agent URL" }));

    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        "http://localhost:3457/api/v1/mcp/folders/abc123",
      ),
    );
  });

  it("shows no agent URL while the folder is restricted", async () => {
    render(
      <ResourceShareButton
        objectType="folder"
        objectId="folder-1"
        resourceName="Research"
        resourceUrlPath="/folders/folder-1"
        currentUser={currentUser}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    await screen.findByRole("dialog", { name: "Share Research" });

    expect(screen.queryByText("Agent access (MCP)")).not.toBeInTheDocument();
  });

  // jsdom has no layout, so this proves only what jsdom can prove: that the
  // popover's coordinates come from the viewport-aware calculation and are
  // applied to the element. The browser render is measured separately, in the
  // task's Step 0/6 evidence - never claim this test is that measurement.
  it("keeps the popover inside a 320px viewport anchored mid-screen", async () => {
    vi.mocked(getShareState).mockResolvedValueOnce({
      shares: [],
      generalAccess: "read",
      mcpUrl: "http://localhost:3457/api/v1/mcp/folders/folder-1",
    });
    // An anchor that leaves no room to the right of it: right-0 anchoring would
    // put the panel's left edge at -104.
    const realRect = HTMLElement.prototype.getBoundingClientRect;
    const realInnerWidth = Object.getOwnPropertyDescriptor(window, "innerWidth");
    HTMLElement.prototype.getBoundingClientRect =
      () =>
        ({
          x: 101,
          y: 34,
          left: 101,
          top: 34,
          right: 181,
          bottom: 50,
          width: 80,
          height: 16,
          toJSON: () => ({}),
        }) as DOMRect;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 320 });
    try {
      render(
        <ResourceShareButton
          objectType="folder"
          objectId="folder-1"
          resourceName="Research"
          resourceUrlPath="/folders/folder-1"
          currentUser={currentUser}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Share" }));
      const dialog = await screen.findByRole("dialog", { name: "Share Research" });
      const left = parseFloat(dialog.style.left);
      const top = parseFloat(dialog.style.top);
      const width = parseFloat(dialog.style.width);
      expect([left, top, width].every(Number.isFinite)).toBe(true);
      expect(left).toBeGreaterThanOrEqual(0);
      expect(left + width).toBeLessThanOrEqual(320);
      expect(await screen.findByRole("button", { name: "Copy agent URL" })).toBeInTheDocument();
    } finally {
      HTMLElement.prototype.getBoundingClientRect = realRect;
      if (realInnerWidth) {
        Object.defineProperty(window, "innerWidth", realInnerWidth);
      } else {
        Reflect.deleteProperty(window, "innerWidth");
      }
    }
  });
});
