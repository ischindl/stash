import { describe, expect, it } from "vitest";

import {
  argsContractViolations,
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

describe("the CLI Reference documents the invocation its command accepts", () => {
  // Existence was not enough: an entry can name every real flag and still tell the reader an
  // option they must pass is optional, or an argument they must type does not exist. The
  // requiredness and positional rules are checked against cli/main.py's typer signatures.
  it("has no entry whose args string misstates requiredness or positionals", () => {
    expect(
      argsContractViolations(page, registry).map(
        (violation) => `:${violation.line} stash ${violation.path} — ${violation.kind}: ${violation.detail}`,
      ),
    ).toEqual([]);
  });
});

describe("the registry carries each command's requiredness and positionals", () => {
  // Derived from cli/main.py at run time, never copied from the docs page: these are the
  // sentinels that prove `typer.Option(...)` reads as required and a default reads as
  // optional, and that Arguments are collected in declaration order.
  const command = (path: string) => {
    const found = registry.commands.get(path);
    if (!found) throw new Error(`cli/main.py no longer exposes \`stash ${path}\``);
    return found;
  };

  it("reads a required Option from its ellipsis, not from the page", () => {
    expect(command("sessions push").requiredFlags.has("--session")).toBe(true);
    expect(command("skills create").requiredFlags.has("--description")).toBe(true);
    expect(command("files edit-folder").requiredFlags.has("--name")).toBe(true);
  });

  it("reads an Option with a default as optional", () => {
    expect(command("tables import").requiredFlags.size).toBe(0);
    expect(command("tables import").flags.has("--file")).toBe(true);
    expect(command("files edit-page").requiredFlags.size).toBe(0);
  });

  it("records the positional contract in declaration order", () => {
    expect(command("sessions push").positionals).toEqual([{ name: "content", required: true }]);
    expect(command("tables import").positionals).toEqual([{ name: "table_id", required: true }]);
    expect(command("ls").positionals).toEqual([{ name: "path", required: false }]);
    expect(command("shares add").positionals.map((positional) => positional.required)).toEqual([
      true,
      true,
      true,
    ]);
  });

  it("finds requiredness in the multi-line Option form too", () => {
    // `--source` is declared across three lines; a line-based parser would miss that it is required.
    expect([...command("skills snapshot-source").requiredFlags].sort()).toEqual(["--path", "--source"]);
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

describe("each entry's args string mirrors the signature the CLI declares", () => {
  // A rule that can never fire proves nothing, so each one is shown reding a synthetic lie and
  // staying silent on the synthetic truth. The commands are real; only the args= shape is invented.
  const entry = (command: string, args: string, params: string[] = []) =>
    `<CommandRef command="stash ${command}" args="${args}" params={[${params.join(", ")}]} />`;
  const param = (name: string, required = false) =>
    `{ name: "${name}", type: "string", desc: "Fixture.",${required ? " required: true," : ""} }`;
  const kinds = (page: string) => argsContractViolations(page, registry).map((violation) => violation.kind);

  it("redes a required option the entry brackets as optional", () => {
    const lie = entry("files edit-folder", "<folder_id> [--name NAME]", [
      param("<folder_id>", true),
      param("--name"),
    ]);
    expect(kinds(lie)).toEqual(["required-flag-bracketed"]);
  });

  it("redes a required option the entry leaves out altogether", () => {
    const lie = entry("sessions push", "<content> [--agent cli] [--attach FILE]", [param("<content>", true)]);
    expect(kinds(lie)).toEqual(["required-flag-missing"]);
  });

  it("redes a required option the params table does not mark required", () => {
    const lie = entry("files edit-folder", "<folder_id> --name NAME", [
      param("<folder_id>", true),
      param("--name"),
    ]);
    expect(kinds(lie)).toEqual(["required-flag-param-not-required"]);
  });

  it("redes an option the CLI defaults but the entry shows as required", () => {
    const lie = entry("files edit-page", "<page_id> --content '...'", [
      param("<page_id>", true),
      param("--content"),
    ]);
    expect(kinds(lie)).toEqual(["optional-flag-unbracketed"]);
  });

  it("redes a params row that calls a defaulted option required", () => {
    const lie = entry("files edit-page", "<page_id> [--content '...]'", [
      param("<page_id>", true),
      param("--content", true),
    ]);
    expect(kinds(lie)).toEqual(["optional-param-marked-required"]);
  });

  it("redes a combined short+long params row that calls a defaulted option required", () => {
    // The row shows as `-x, --long`, so `--long` is the flag it documents — the same extraction
    // the existence guard already uses. Its silence is indistinguishable from the truth fixture's,
    // so this red is the proof that the requiredness rule sees combined names at all.
    const lie = entry("files edit-page", "<page_id> [--content '...]'", [
      param("<page_id>", true),
      param("-x, --long", true),
    ]);
    expect(kinds(lie)).toEqual(["optional-param-marked-required"]);
  });

  it("redes a combined row that marks a real defaulted option required", () => {
    // Same lie with a flag `files edit-page` really declares and defaults (`--content`), so the
    // proof does not rest on a fictional flag.
    const lie = entry("files edit-page", "<page_id> [--content '...]'", [
      param("<page_id>", true),
      param("-c, --content", true),
    ]);
    expect(kinds(lie)).toEqual(["optional-param-marked-required"]);
  });

  it("redes an argument the entry shows positionally that the CLI only takes as an option", () => {
    const lie = entry("tables import", "<table_id> <file> [--format csv|json]", [
      param("<table_id>", true),
      param("<file>", true),
    ]);
    expect(kinds(lie)).toEqual(["required-positionals-mismatch"]);
  });

  it("redes an optional argument the entry demands", () => {
    const lie = entry("ls", "<path> [--depth N]", [param("<path>", true)]);
    expect(kinds(lie)).toEqual(["required-positionals-mismatch", "optional-positionals-mismatch"]);
  });

  it("stays silent on the corrected shape of a required option", () => {
    const truth = entry("sessions push", "<content> --session ID [--agent cli] [--attach FILE]", [
      param("<content>", true),
      param("--session", true),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a required option that is unbracketed and marked required", () => {
    const truth = entry("files edit-folder", "<folder_id> --name NAME", [
      param("<folder_id>", true),
      param("--name", true),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a defaulted option the entry brackets", () => {
    const truth = entry("files edit-page", "<page_id> [--content '...']", [
      param("<page_id>", true),
      param("--content"),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a mutually-exclusive group, whose exclusivity typer cannot describe", () => {
    const truth = entry("tools add", "<name> (--url URL | --command CMD) [--header K=V]", [
      param("<name>", true),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on the truth twin of the combined short+long lie", () => {
    const truth = entry("files edit-page", "<page_id> [--content '...']", [
      param("<page_id>", true),
      param("-x, --long"),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a combined row whose real defaulted option is unmarked", () => {
    const truth = entry("files edit-page", "<page_id> [--content '...']", [
      param("<page_id>", true),
      param("-c, --content"),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a required option documented under a combined short+long name", () => {
    // The `--name` flag is genuinely demanded here; documenting it as `-n, --name` marked required
    // is the truth. The exact-equality companion predicate used to miss the long flag inside that
    // combined row name and cry a false `required-flag-param-not-required`.
    const truth = entry("files edit-folder", "<folder_id> --name NAME", [
      param("<folder_id>", true),
      param("-n, --name", true),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a short-only params row, which names no long flag", () => {
    const truth = entry("files edit-page", "<page_id> [--content '...']", [
      param("<page_id>", true),
      param("-y", true),
    ]);
    expect(argsContractViolations(truth, registry)).toEqual([]);
  });

  it("stays silent on a command that forwards args the CLI itself never declares", () => {
    expect(argsContractViolations(entry("vfs", "<anything> <else> [--cwd PATH]"), registry)).toEqual([]);
  });

  it("points a red at the entry that caused it", () => {
    const violations = argsContractViolations(entry("sessions push", "<content> [--session ID]"), registry);
    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatchObject({ path: "sessions push", kind: "required-flag-bracketed", line: 1 });
  });
});
