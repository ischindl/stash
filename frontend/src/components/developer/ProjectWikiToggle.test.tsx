/** A project's clearance is a switch a developer flips, so two things matter: the
 *  state reads correctly from the accessible name alone (screen readers get the
 *  project it belongs to), and clicking it twice while the change is still in
 *  flight cannot fire the route twice. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectWikiToggle from "./ProjectWikiToggle";

afterEach(cleanup);

describe("ProjectWikiToggle", () => {
  it("labels the switch with the project it controls and its state", () => {
    const { unmount } = render(
      <ProjectWikiToggle shareWiki projectName="acme-diesel" onToggle={vi.fn()} />
    );

    expect(screen.getByRole("switch", { name: "Feeds shared wiki: acme-diesel" })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    expect(screen.getByText("Feeds shared wiki")).toBeInTheDocument();

    unmount();
    render(<ProjectWikiToggle shareWiki={false} projectName="acme-diesel" onToggle={vi.fn()} />);

    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("Not cleared")).toBeInTheDocument();
  });

  it("asks for the state it does not have, once per click", () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    render(<ProjectWikiToggle shareWiki projectName="acme-diesel" onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("switch"));

    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  it("ignores a second click while the change is still in flight", async () => {
    let settle: (() => void) | undefined;
    const onToggle = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        })
    );
    render(<ProjectWikiToggle shareWiki projectName="acme-diesel" onToggle={onToggle} />);

    const toggle = screen.getByRole("switch");
    fireEvent.click(toggle);
    fireEvent.click(toggle);

    expect(onToggle).toHaveBeenCalledTimes(1);

    settle?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(toggle);

    expect(onToggle).toHaveBeenCalledTimes(2);
  });
});
