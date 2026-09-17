import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(HERE, "..", "..", "..");
export const CLI_MAIN = join(REPO_ROOT, "cli/main.py");
export const DOCS_PAGE = join(REPO_ROOT, "www/app/docs/cli/page.tsx");

export function readCliMain(): string {
  return readFileSync(CLI_MAIN, "utf8");
}

export function readDocsPage(): string {
  return readFileSync(DOCS_PAGE, "utf8");
}

export type Command = {
  /** Space-joined path: "files add-page", or "memory" for a group that runs alone. */
  path: string;
  /** Long --flags the command declares in its typer signature. */
  flags: Set<string>;
  /** A group that runs without a subcommand (invoke_without_command + callback). */
  runnableGroup: boolean;
  /** Typer is told to accept args the command itself never declares. */
  passthrough: boolean;
};

export type Registry = {
  /** Every command path a user can run; hidden groups are excluded. */
  commands: Map<string, Command>;
  /** Visible group names, for longest-prefix resolution of prose mentions. */
  groups: Set<string>;
};

/**
 * Reads the command registry out of cli/main.py's source text: whatever decorators
 * the CLI declares is what the docs page is held to, so a new command cannot be
 * documented or undocumented without this file saying so. Multi-line `@x.command(`
 * decorators are not read — the single-line form is the one this codebase uses.
 */
export function deriveRegistry(mainPy: string = readCliMain()): Registry {
  const groupsByVar = new Map<string, { name: string; hidden: boolean }>();
  for (const match of mainPy.matchAll(/^(\w+)\.add_typer\((\w+),\s*name="([^"]+)"([^\n]*)\)/gm)) {
    groupsByVar.set(match[2], { name: match[3], hidden: match[4].includes("hidden=True") });
  }

  const commands = new Map<string, Command>();
  for (const match of mainPy.matchAll(/^@(\w+)\.command\(([^\n]*)\)$/gm)) {
    const [decoratorVar, decoratorArgs] = [match[1], match[2]];
    const group = decoratorVar === "app" ? null : groupsByVar.get(decoratorVar);
    // A command on a Typer that is never registered, or on a hidden group, is not
    // reachable by `stash <path>` and therefore not the docs page's business.
    if (decoratorVar !== "app" && !group) continue;
    if (group?.hidden) continue;

    const declaredName =
      /^\s*"([^"]+)"/.exec(decoratorArgs)?.[1] ?? /\bname="([^"]+)"/.exec(decoratorArgs)?.[1];
    const fn = functionAfter(mainPy, match.index + match[0].length, decoratorVar);
    const path = group
      ? `${group.name} ${declaredName ?? hyphenate(fn.name)}`
      : (declaredName ?? hyphenate(fn.name));
    addCommand(commands, {
      path,
      flags: declaredFlags(mainPy, fn, path),
      runnableGroup: false,
      passthrough: decoratorArgs.includes("allow_extra_args"),
    });
  }

  const groups = new Set<string>();
  for (const { name, hidden } of groupsByVar.values()) if (!hidden) groups.add(name);

  for (const [typerVar, group] of groupsByVar) {
    if (group.hidden || !runsAlone(mainPy, typerVar)) continue;
    const callback = new RegExp(`^@${typerVar}\\.callback\\(\\)`, "m").exec(mainPy);
    if (!callback) continue;
    const fn = functionAfter(mainPy, callback.index + callback[0].length, typerVar);
    addCommand(commands, {
      path: group.name,
      flags: declaredFlags(mainPy, fn, group.name),
      runnableGroup: true,
      passthrough: false,
    });
  }

  return { commands, groups };
}

function addCommand(commands: Map<string, Command>, command: Command): void {
  if (commands.has(command.path)) {
    throw new Error(`cli/main.py exposes \`stash ${command.path}\` twice — cannot guard it`);
  }
  commands.set(command.path, command);
}

/** A group runs on its own only when Typer allows it and a callback receives the call. */
function runsAlone(mainPy: string, typerVar: string): boolean {
  const declaration = new RegExp(`^${typerVar} = typer\\.Typer\\(([\\s\\S]*?)\\n\\)`, "m").exec(mainPy);
  if (!declaration) return false;
  return declaration[1].includes("invoke_without_command=True");
}

function hyphenate(functionName: string): string {
  return functionName.replace(/_/g, "-");
}

type FoundFunction = { name: string; start: number };

function functionAfter(mainPy: string, from: number, decoratorVar: string): FoundFunction {
  const fn = /^def\s+(\w+)\s*\(/m.exec(mainPy.slice(from));
  if (!fn) throw new Error(`no top-level function after \`@${decoratorVar}.command\` in cli/main.py`);
  return { name: fn[1], start: from + fn.index };
}

/**
 * The `--flags` a command accepts, read from its typer signature between the `def`
 * and its docstring — an explicit `"--flag"` (or `"--flag/--no-flag"` pair) in the
 * Option, or the flag typer derives from the parameter name. The docstring's prose is
 * excluded; it names other commands' flags.
 */
function declaredFlags(mainPy: string, fn: FoundFunction, path: string): Set<string> {
  const docstring = mainPy.indexOf('"""', fn.start);
  if (docstring === -1) {
    throw new Error(`\`stash ${path}\` has no docstring to end its signature at`);
  }
  return optionFlags(mainPy.slice(fn.start, docstring));
}

function optionFlags(signature: string): Set<string> {
  const flags = new Set<string>();
  for (const parameter of topLevels(signature.slice(signature.indexOf("(") + 1, signature.lastIndexOf(")")))) {
    const declared = /^(\w+)\s*(?::[^=]*)?=\s*typer\.Option\(([\s\S]*)\)\s*$/.exec(parameter.trim());
    if (!declared) continue;
    const explicit = explicitFlags(declared[2]);
    for (const flag of explicit) flags.add(flag);
    if (explicit.length === 0) flags.add(`--${declared[1].replace(/_/g, "-")}`);
  }
  return flags;
}

/**
 * Flag names inside one `typer.Option(...)` call. Typer writes a boolean switch as a
 * single literal pair — `"--record/--no-record"` — and both halves are real flags.
 * Whole-literal matching is what keeps a help string from being read as a flag.
 */
function explicitFlags(optionCall: string): string[] {
  const flags: string[] = [];
  for (const literal of optionCall.matchAll(/"([^"]*)"/g)) {
    if (!/^-[\w-]+(\/--?[\w-]+)*$/.test(literal[1])) continue;
    for (const name of literal[1].split("/")) flags.push(`--${name.replace(/^-+/, "")}`);
  }
  return flags;
}

/**
 * One string per top-level parameter of a signature, so a comma inside a help string
 * or a nested call never splits a parameter in two.
 */
function topLevels(parameters: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let at = 0; at < parameters.length; at++) {
    const char = parameters[at];
    if (char === "(" || char === "[" || char === "{") depth++;
    if (char === ")" || char === "]" || char === "}") depth--;
    if (char === "," && depth === 0) {
      parts.push(parameters.slice(start, at));
      start = at + 1;
    }
  }
  parts.push(parameters.slice(start));
  return parts;
}

export type Mention = {
  /** The tokens written after `stash`, e.g. "sources ls". */
  raw: string;
  /** The registry path it resolves to, or "" when the CLI has no such command. */
  path: string;
  line: number;
};

/**
 * Every `stash <words>` a reader of this page would actually type — `<Code>` and
 * `<CodeBlock>` text and the prose of descriptions — minus the route's metadata,
 * whose marketing sentence is not a CLI claim. A mention stops at the first non-word
 * token and at three words, so quoted arguments, flags, and the sentence a command is
 * mentioned inside never become part of the command name. `.stash`/`~/.stash` paths are
 * excluded: the leading punctuation means the word is the config file, not the CLI.
 */
export function mentionsOnPage(page: string = readDocsPage(), registry: Registry = deriveRegistry()): Mention[] {
  const scanned = page.replace(/^export const metadata[\s\S]*?\n\};/m, "");
  const mentions: Mention[] = [];
  const pattern = /(?<![.\w/-])stash\s+((?:[A-Za-z][\w-]*)(?:\s+[A-Za-z][\w-]*){0,2})/g;
  for (const match of scanned.matchAll(pattern)) {
    const raw = match[1].replace(/\s+/g, " ").trim();
    mentions.push({ raw, path: resolveMention(raw, registry), line: lineOf(page, match.index) });
  }
  return mentions;
}

/** Longest registered command path the mention starts with, or "" if none. */
export function resolveMention(raw: string, registry: Registry): string {
  const tokens = raw.split(" ").filter((token) => !token.startsWith("-"));
  for (let taken = tokens.length; taken > 0; taken--) {
    const candidate = tokens.slice(0, taken).join(" ");
    if (registry.commands.has(candidate)) return candidate;
  }
  return "";
}

export type DocumentedCommand = {
  /** The `command=` value after `stash`, e.g. "files add-page". */
  raw: string;
  /** The registry path it names, or "" when the CLI has no such command. */
  path: string;
  /** `--flags` documented in this entry's args= and params[].name values. */
  flags: string[];
  line: number;
};

/**
 * The page's command reference entries — what counts as *documenting* a command.
 * A passing mention in someone else's description is not an entry. A `command=` value
 * is the command's whole path, so it must match the registry exactly: matching only a
 * prefix would let `stash sessions query` pass as the `stash sessions` group.
 */
export function documentedCommands(page: string = readDocsPage(), registry: Registry = deriveRegistry()): DocumentedCommand[] {
  const entries: DocumentedCommand[] = [];
  for (const block of page.matchAll(/<CommandRef\b([\s\S]*?)\/>/g)) {
    const raw = /\bcommand="stash\s+([^"]*)"/.exec(block[1])?.[1];
    if (raw === undefined) continue;
    const path = raw.trim();
    entries.push({
      raw: path,
      path: registry.commands.has(path) ? path : "",
      flags: documentedFlags(block[1]),
      line: lineOf(page, block.index),
    });
  }
  return entries;
}

function documentedFlags(commandRefBody: string): string[] {
  const documented: string[] = [];
  for (const args of commandRefBody.matchAll(/\bargs=(?:"([^"]*)"|\{'([^']*)'\}|\{`([^`]*)`\})/g)) {
    documented.push(...flagTokens(args[1] ?? args[2] ?? args[3] ?? ""));
  }
  for (const name of commandRefBody.matchAll(/\bname:\s*"([^"]*)"/g)) {
    documented.push(...flagTokens(name[1]));
  }
  return documented;
}

function flagTokens(text: string): string[] {
  return [...text.matchAll(/--([A-Za-z][\w-]*)/g)].map((match) => `--${match[1]}`);
}

export type FlagViolation = {
  /** The entry's `command=` value, e.g. "files add-page". */
  path: string;
  flag: string;
  line: number;
};

/**
 * Documented `--flags` that the command does not accept. A command that swallows
 * unknown args (`stash vfs`) documents flags its own parser will see, so it is exempt.
 */
export function undocumentedFlags(
  page: string = readDocsPage(),
  registry: Registry = deriveRegistry(),
): FlagViolation[] {
  const violations: FlagViolation[] = [];
  for (const entry of documentedCommands(page, registry)) {
    const command = registry.commands.get(entry.path);
    if (!command || command.passthrough) continue;
    for (const flag of entry.flags) {
      if (!command.flags.has(flag)) violations.push({ path: entry.path, flag, line: entry.line });
    }
  }
  return violations;
}

/**
 * Names the page presents as CLI commands that cli/main.py does not expose: reference
 * entries whose `command=` matches nothing, plus every other mention on the page.
 */
export function stalePageReferences(
  page: string = readDocsPage(),
  registry: Registry = deriveRegistry(),
): { entries: DocumentedCommand[]; mentions: Mention[] } {
  return {
    entries: documentedCommands(page, registry).filter((entry) => entry.path === ""),
    mentions: mentionsOnPage(page, registry).filter((mention) => mention.path === ""),
  };
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}
