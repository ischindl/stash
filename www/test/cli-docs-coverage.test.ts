import { describe, expect, it } from "vitest";

import { deriveRegistry, documentedCommands, readCliMain, readDocsPage } from "./lib/cli-registry";

const registry = deriveRegistry(readCliMain());
const documented = new Set(
  documentedCommands(readDocsPage(), registry)
    .map((entry) => entry.path)
    .filter((path) => path !== ""),
);
const paths = [...registry.commands.keys()].sort();

describe("every command the CLI exposes is documented", () => {
  it("derives the registry from cli/main.py, not from a list someone maintains", () => {
    // The floor exists so a deriver that silently stops matching fails loudly instead of
    // vacuously passing every per-command test below.
    expect(paths.length).toBeGreaterThanOrEqual(85);
    expect(registry.commands.has("files add-page")).toBe(true); // named in the decorator
    expect(registry.commands.has("import-history")).toBe(true); // hyphenated from the function name
    expect(registry.commands.has("hook run")).toBe(false); // a hidden group is not user-facing
    expect(registry.commands.get("memory")?.runnableGroup).toBe(true); // runs without a subcommand
    expect(registry.commands.has("workspace")).toBe(false); // bare `stash workspace` errors, so it is not a command
    expect(registry.commands.get("files add-page")?.flags.has("--content")).toBe(true); // typer derives it from the parameter
    expect(registry.commands.get("rm")?.flags.has("--permanent")).toBe(true); // named explicitly
    expect(registry.commands.get("vfs")?.passthrough).toBe(true); // typer is told to accept unknown args
  });

  for (const path of paths) {
    // Full parity is the contract: a command that ships without a docs entry fails here
    // until the reference page grows the entry (or the command is removed from the CLI).
    it(`the CLI Reference documents \`stash ${path}\``, () => {
      expect(documented.has(path), `\`stash ${path}\` has no <CommandRef> entry`).toBe(true);
    });
  }
});
