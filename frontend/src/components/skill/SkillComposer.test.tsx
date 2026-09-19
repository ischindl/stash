import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SkillComposer } from "./SkillComposer";

afterEach(cleanup);

// The panel's CTA converts a folder that already has a name, so the form asks
// for the one thing a conversion cannot infer — when an agent should load it.
// It must not sprout a second publish verb beside that conversion.
describe("SkillComposer in convert mode", () => {
  it("names the folder it converts and asks only for the description", () => {
    render(
      <SkillComposer
        convertFolderName="Brake Shoes"
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("Convert “Brake Shoes” to a skill")).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.queryByText("Publish as skill")).toBeNull();
    // Cancel and the one convert verb — nothing else to press.
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("submits the description it was given, and nothing before it exists", async () => {
    const onSubmit = vi.fn(async () => {});
    render(
      <SkillComposer
        convertFolderName="Brake Shoes"
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Convert to skill" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/this is required/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("When should an agent use it?"), {
      target: { value: "Use when bleeding brakes." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Convert to skill" }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        name: "",
        description: "Use when bleeding brakes.",
      }),
    );
  });
});
