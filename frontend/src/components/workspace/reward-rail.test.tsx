import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RewardRail from "./reward-rail";
import type { User } from "@/lib/types";

// The cohort upstream #1128 created: a fresh signup carries BOTH flags true, so
// the shell renders this rail as its only navigation. Everything a new account
// can reach in the product has to be reachable from PRIMARY.
const state = vi.hoisted(() => ({
  pathname: "/reward-models",
  search: "",
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useSearchParams: () => new URLSearchParams(state.search),
  useRouter: () => ({ push: state.push, replace: state.push, prefetch: vi.fn() }),
}));

// next/link routes a click through the router; reproduce just that so clicking a
// rail entry is observable, and keep the href so the entry's target is assertable.
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} onClick={() => state.push(href)} {...props}>
      {children}
    </a>
  ),
}));

function cohortUser(): User {
  return {
    id: "user-1",
    developer_platform_only: true,
    reward_models_enabled: true,
    name: "new-signup",
    display_name: "New Signup",
    email: "signup@example.com",
    description: "",
    created_at: "2026-10-02",
    last_seen: "2026-10-02",
    show_tools_and_chat: false,
  };
}

function renderRail() {
  return render(<RewardRail user={cohortUser()} onLogout={vi.fn()} />);
}

beforeEach(() => {
  state.pathname = "/reward-models";
  state.search = "";
  state.push.mockClear();
});
afterEach(cleanup);

describe("the reward rail gives the new-signup cohort a way into its files", () => {
  it("renders a clickable Files entry", () => {
    renderRail();
    // RED today: PRIMARY is Traces / Reward models / Skills, so a brand-new
    // account has no affordance for the file browser at all — /files exists for
    // them only as a URL someone has to type.
    expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
    expect(screen.getByRole("link", { name: "Files" })).toBeInTheDocument();
  });

  it("opens the files browser from the rail's own navigation, not a typed URL", async () => {
    renderRail();
    const files = screen.getByRole("link", { name: "Files" });
    expect(files).toHaveAttribute("href", "/files");
    await userEvent.click(files);
    expect(state.push).toHaveBeenCalledWith("/files");
  });

  it("is the only Files affordance and keeps the rest of the rail intact", () => {
    renderRail();
    expect(screen.getAllByRole("link", { name: "Files" })).toHaveLength(1);
    const labels = screen.getAllByRole("link").map((link) => link.textContent?.trim());
    expect(labels).toEqual(["Traces", "Reward models", "Files", "Skills", "Settings"]);
  });

  it.each(["/files", "/f/file-1", "/p/page-1", "/folders/folder-1", "/tables/table-1"])(
    "reads as the current section on %s",
    (pathname) => {
      state.pathname = pathname;
      renderRail();
      expect(screen.getByRole("link", { name: "Files" })).toHaveAttribute("aria-current", "page");
    },
  );

  it("is not current on the reward screens", () => {
    state.pathname = "/reward-models/models";
    renderRail();
    expect(screen.getByRole("link", { name: "Files" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Reward models" })).toHaveAttribute("aria-current", "page");
  });
});
