import { describe, expect, it } from "vitest";

import {
  deriveRegistry,
  mentionsOnPage,
  readCliMain,
  readDocsPage,
  stalePageReferences,
  undocumentedFlags,
} from "./lib/cli-registry";

const mainPy = readCliMain();
const page = readDocsPage();
const registry = deriveRegistry(mainPy);

const stale = stalePageReferences(page, registry);
const staleNames = [
  ...new Set([
    ...stale.entries.map((entry) => entry.raw),
    ...stale.mentions.map((mention) => mention.raw),
  ]),
].sort();

describe("the CLI Reference names only commands that exist", () => {
  // No snapshot here: the names are either real or the page is wrong, and cli/main.py
  // decides which. The failure message prints every offending line.
  it("has no reference to a command the CLI does not expose", () => {
    expect(staleNames).toEqual([]);
  });
});

describe("the CLI Reference documents only flags that exist", () => {
  it("has no documented flag its command does not accept", () => {
    expect(undocumentedFlags(page, registry)).toEqual([]);
  });
});

describe("the guards read the page honestly", () => {
  it("flags a reference entry whose command is gone", () => {
    const synthetic = `<CommandRef command="stash files pages" description="List pages." params={[]} />`;
    expect(stalePageReferences(synthetic, registry).entries.map((entry) => entry.raw)).toEqual([
      "files pages",
    ]);
  });

  it("does not accept a stale subcommand because its group runs alone", () => {
    // `stash sessions` is runnable, so matching mentions by prefix would excuse this entry.
    const synthetic = `<CommandRef command="stash sessions query" args="[--agent X]" params={[]} />`;
    expect(stalePageReferences(synthetic, registry).entries.map((entry) => entry.raw)).toEqual([
      "sessions query",
    ]);
  });

  it("does not read .stash, a file the CLI writes, as the CLI being invoked", () => {
    const synthetic = "Run `stash setup` to write .stash and add Stash instructions to CLAUDE.md.";
    expect(mentionsOnPage(synthetic, registry).map((mention) => mention.raw)).toEqual(["setup"]);
  });

  it("flags a documented flag its command never declared", () => {
    const synthetic = `<CommandRef command="stash rm" args="<path> [--made-up-flag X]" params={[{ name: "--made-up-flag", type: "string", desc: "Nope." }]} />`;
    const violations = undocumentedFlags(synthetic, registry);
    expect(violations.length).toBeGreaterThan(0);
    expect(violations[0].flag).toBe("--made-up-flag");
    expect(violations[0].path).toBe("rm");
  });

  it("accepts a documented --no-flag when the command declares the boolean pair", () => {
    // Typer writes a switch as one literal — `"--record/--no-record"` — and both halves work.
    const synthetic = `<CommandRef command="stash setup" args="[--no-record]" params={[{ name: "--no-record", type: "flag", desc: "Skip recording." }]} />`;
    expect(undocumentedFlags(synthetic, registry)).toEqual([]);
  });

  it("accepts a documented flag the command derives from its parameter name", () => {
    const synthetic = `<CommandRef command="stash files add-page" args="<name> [--content '...']" params={[{ name: "--content", type: "string", desc: "Body." }]} />`;
    expect(undocumentedFlags(synthetic, registry)).toEqual([]);
  });
});
