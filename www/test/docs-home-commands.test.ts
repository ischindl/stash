import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

import {
  deriveRegistry,
  mentionsOnPage,
  readCliMain,
  REPO_ROOT,
  type Mention,
} from "./lib/cli-registry";

const DOCS_ROOT = join(REPO_ROOT, "www/app/docs");
const registry = deriveRegistry(readCliMain());

/**
 * Every page of the docs route. STAS-217's gate reads the one page its lib pins
 * (`DOCS_PAGE` = the CLI Reference), which is how the home page's marketing mock was free to
 * invent commands. This walks the whole route instead, so no page can rot off-guard again.
 */
function docsPages(dir: string = DOCS_ROOT): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) return docsPages(path);
    return entry.endsWith(".tsx") ? [path] : [];
  });
}

type Offence = { line: number; mention: string; why: string };

/**
 * The `stash …` mentions on a page that a reader cannot actually run, reusing the registry
 * lib's mention extraction — its metadata stripping, `.stash`/`~/.stash` exclusion, three-word
 * cap, and longest-prefix resolution — so this file adds no prose heuristics of its own.
 *
 * Two classes, and the second is the reason this gate exists:
 *  - the mention resolves to nothing at all, which is the class the lib already reports;
 *  - the mention resolves to a group that runs alone and takes no argument, yet carries a
 *    trailing word anyway. Prefix resolution makes `stash sessions search` look resolved,
 *    because `stash sessions` is genuinely runnable — so without this leftover-word rule a
 *    made-up subcommand hides behind its own group and the gate stays green over a lie.
 *    It is limited to such groups on purpose: after a command that takes an argument, or
 *    inside a sentence, a trailing word is data, not a subcommand.
 */
function unrunnable(page: string): Offence[] {
  return mentionsOnPage(page, registry).flatMap((mention) => {
    const command = registry.commands.get(mention.path);
    if (!command) return [{ ...position(page, mention), mention: mention.raw, why: "no such command" }];
    const words = mention.raw.split(" ").filter((word) => !word.startsWith("-"));
    const leftover = words.slice(command.path.split(" ").length);
    const trailingWordIsData = command.passthrough || command.positionals.length > 0;
    if (leftover.length === 0 || !command.runnableGroup || trailingWordIsData) return [];
    return [
      {
        ...position(page, mention),
        mention: mention.raw,
        why: `\`stash ${command.path}\` runs alone and takes no argument, so "${leftover[0]}" is not a subcommand`,
      },
    ];
  });
}

/**
 * The lib measures a mention's line against the metadata it strips before scanning, so the
 * number it reports is short by the height of the route's metadata block. A violation prints a
 * line a human will open, so the mention is re-found in the file's own text: a line in the raw
 * file can only sit at or after the line the lib measured, so the earliest candidate at or after
 * it is the mention the lib meant, and any occurrence inside the metadata block sits above it.
 */
function position(page: string, mention: Mention): { line: number } {
  const words = mention.raw.split(" ").map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const found = [...page.matchAll(new RegExp(`stash\\s+${words.join("\\s+")}`, "g"))].map(
    (match) => page.slice(0, match.index).split("\n").length,
  );
  const candidates = found.filter((line) => line >= mention.line);
  if (candidates.length === 0) {
    throw new Error(`\`stash ${mention.raw}\` was scanned off a page it cannot be found on`);
  }
  return { line: Math.min(...candidates) };
}

describe("the docs pages only show commands the CLI can run", () => {
  const pages = docsPages();

  it("sweeps the whole docs route, not just the pinned CLI Reference", () => {
    const read = pages.map((file) => relative(REPO_ROOT, file));
    expect(read).toContain("www/app/docs/page.tsx");
    expect(pages.reduce((seen, file) => seen + mentionsOnPage(readFileSync(file, "utf8"), registry).length, 0))
      .toBeGreaterThan(0);
  });

  it("renders no invocation that cli/main.py does not expose", () => {
    const violations = pages.flatMap((file) =>
      unrunnable(readFileSync(file, "utf8")).map(
        (offence) => `${relative(REPO_ROOT, file)}:${offence.line}: stash ${offence.mention} — ${offence.why}`,
      ),
    );
    expect(violations).toEqual([]);
  });
});

describe("the mention rule fires on made-up commands and stays silent on real ones", () => {
  // Nothing here is seeded: every command name is read out of the registry derived from
  // cli/main.py, so the fixtures cannot age apart from the CLI. A rule that merely never fires
  // would keep this suite green while the pages lied, so every silent case is paired with one
  // that must report.
  const fromRegistry = (path: string | undefined, shape: string): string => {
    if (!path) throw new Error(`the guard's fixtures need a command that is ${shape}, and cli/main.py has none`);
    return path;
  };
  const group = fromRegistry(
    [...registry.commands.values()].find((c) => c.runnableGroup && c.positionals.length === 0 && !c.passthrough)?.path,
    "a group that runs alone and takes no argument",
  );
  const subcommand = fromRegistry(
    [...registry.commands.keys()].find((path) => path.startsWith(`${group} `))?.split(" ")[1],
    `a subcommand of \`${group}\``,
  );
  const takesArgument = fromRegistry(
    [...registry.commands.values()].find((c) => c.positionals.length > 0 && !c.runnableGroup)?.path,
    "a command that takes an argument",
  );
  const passthrough = fromRegistry(
    [...registry.commands.values()].find((c) => c.passthrough)?.path,
    "a command that accepts extra arguments",
  );

  const reported = (text: string) => unrunnable(text).map((offence) => offence.mention);

  it("fires on a subcommand its runnable group never registered", () => {
    expect(reported(`<span>stash ${group} zzz-does-not-exist</span>`)).toEqual([
      `${group} zzz-does-not-exist`,
    ]);
  });

  it("fires on a top-level command that does not exist at all", () => {
    expect(reported("<span>stash zzz-not-a-command</span>")).toEqual(["zzz-not-a-command"]);
  });

  it("is silent about a group's own subcommand", () => {
    expect(reported(`<span>stash ${group} ${subcommand}</span>`)).toEqual([]);
  });

  it("is silent about a group that runs alone", () => {
    expect(reported(`<span>stash ${group}</span>`)).toEqual([]);
  });

  it("is silent about the words that follow a command taking an argument", () => {
    expect(reported(`<p>Use stash ${takesArgument} to file it away.</p>`)).toEqual([]);
  });

  it("is silent about the passthrough command's payload", () => {
    expect(reported(`<Code>stash ${passthrough} zzz anything at all</Code>`)).toEqual([]);
  });

  it("is silent about a path that merely names a command inside quotes", () => {
    expect(reported(`<Code>stash ${passthrough} "ls /${group}"</Code>`)).toEqual([]);
  });

  it("is silent about an empty page", () => {
    expect(reported("")).toEqual([]);
  });

  it("is silent about the product name and the config file the CLI writes", () => {
    expect(reported("<p>Stash instructions land in .stash and ~/.stash/config.json.</p>")).toEqual([]);
  });
});
