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
  /** The subset of `flags` the CLI refuses to run without — `typer.Option(...)`. */
  requiredFlags: Set<string>;
  /** The command's positional contract, in the order the CLI reads them. */
  positionals: Positional[];
  /** A group that runs without a subcommand (invoke_without_command + callback). */
  runnableGroup: boolean;
  /** Typer is told to accept args the command itself never declares. */
  passthrough: boolean;
};

export type Positional = {
  /** The Python parameter name, which the page is free to label differently. */
  name: string;
  /** Typer requires the argument when its call opens with `...` instead of a default. */
  required: boolean;
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
      ...declaredSignature(mainPy, fn, path),
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
      ...declaredSignature(mainPy, fn, group.name),
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
 * What a command's typer signature says it takes: the `--flags` it accepts, which of
 * them the CLI demands, and its positional contract. Read between the `def` and its
 * docstring — the docstring's prose is excluded because it names other commands' flags.
 */
function declaredSignature(mainPy: string, fn: FoundFunction, path: string): Signature {
  const docstring = mainPy.indexOf('"""', fn.start);
  if (docstring === -1) {
    throw new Error(`\`stash ${path}\` has no docstring to end its signature at`);
  }
  return signatureContract(mainPy.slice(fn.start, docstring));
}

type Signature = Pick<Command, "flags" | "requiredFlags" | "positionals">;

function signatureContract(signature: string): Signature {
  const flags = new Set<string>();
  const requiredFlags = new Set<string>();
  const positionals: Positional[] = [];
  const parameters = signature.slice(signature.indexOf("(") + 1, signature.lastIndexOf(")"));
  for (const parameter of topLevels(parameters)) {
    const option = /^(\w+)\s*(?::[^=]*)?=\s*typer\.Option\(([\s\S]*)\)\s*$/.exec(parameter.trim());
    if (option) {
      const explicit = explicitFlags(option[2]);
      const names = explicit.length > 0 ? explicit : [`--${option[1].replace(/_/g, "-")}`];
      for (const flag of names) flags.add(flag);
      if (typerDemands(option[2])) for (const flag of names) requiredFlags.add(flag);
      continue;
    }
    const argument = /^(\w+)\s*(?::[^=]*)?=\s*typer\.Argument\(([\s\S]*)\)\s*$/.exec(parameter.trim());
    if (argument) positionals.push({ name: argument[1], required: typerDemands(argument[2]) });
  }
  return { flags, requiredFlags, positionals };
}

/**
 * Typer's own marker for "no default, so the user must supply it": `...` as the call's
 * first argument. The first top-level argument is what matters — a help string that
 * merely starts with an ellipsis is not a requiredness declaration.
 */
function typerDemands(typerCall: string): boolean {
  return topLevels(typerCall)[0].trim() === "...";
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
  /** The entry's `args=` string — the invocation shape a reader copies — or "" if it shows none. */
  args: string;
  /** Every `params[]` entry: the documented name and whether the entry marks it required. */
  params: DocumentedParam[];
  line: number;
};

export type DocumentedParam = {
  /** Either a `--flag` or a positional label, exactly as the entry writes it. */
  name: string;
  /** Whether the entry's `params[]` object carries `required: true`. */
  required: boolean;
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
      args: documentedArgs(block[1])[0] ?? "",
      params: documentedParams(block[1]),
      line: lineOf(page, block.index),
    });
  }
  return entries;
}

/**
 * The entry's `args=` strings. All four ways of writing the attribute count — `"…"`, `{'…'}`,
 * `'…'`, and `` {`…`} `` — because reading only some of them would let an entry written one way
 * dodge a rule that entries written another way obey.
 */
function documentedArgs(commandRefBody: string): string[] {
  const pattern = /\bargs=(?:"([^"]*)"|\{'([^']*)'\}|'([^']*)'|\{`([^`]*)`\})/g;
  return [...commandRefBody.matchAll(pattern)].map(
    (match) => match[1] ?? match[2] ?? match[3] ?? match[4] ?? "",
  );
}

/**
 * The `params[]` entries of one `<CommandRef>`. Each object starts with `name: "…"`, so the
 * objects are found by splitting there; everything up to the next `name:` belongs to this one.
 */
function documentedParams(commandRefBody: string): DocumentedParam[] {
  const params: DocumentedParam[] = [];
  for (const object of commandRefBody.split(/\{\s*name:/).slice(1)) {
    const name = /^\s*"([^"]*)"/.exec(object)?.[1];
    if (name === undefined) continue;
    params.push({ name, required: /\brequired:\s*true\b/.test(object) });
  }
  return params;
}

function documentedFlags(commandRefBody: string): string[] {
  const documented: string[] = [];
  for (const args of documentedArgs(commandRefBody)) {
    documented.push(...flagTokens(args));
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

/** How the args= string bracketed a token: demanded, `[..]` optional, or `( a | b )` mutually exclusive. */
type Segment = "required" | "optional" | "group";

type ArgsToken = {
  /** `--flag` for an option; the label without `<…>` or a repeat `…` for an argument. */
  text: string;
  kind: "flag" | "short-flag" | "positional";
  segment: Segment;
};

/**
 * The invocation shape an `args=` string promises, token by token. A token that follows a flag is
 * that flag's value placeholder and is dropped, which is what keeps `[--api <base_url>]` from being
 * read as a second argument. Only whitespace splits tokens, so an enum written `internal|external`
 * stays one placeholder; the `|` inside a `(` group does split its alternatives.
 */
function argsTokens(args: string): ArgsToken[] {
  const tokens: ArgsToken[] = [];
  for (const { text, segment } of argsSegments(args)) {
    for (const alternative of segment === "group" ? text.split("|") : [text]) {
      let followsFlag = false;
      for (const word of alternative.split(/\s+/)) {
        const token = classifyToken(word);
        if (!token) continue;
        if (token.kind === "positional" && followsFlag) {
          followsFlag = false;
          continue;
        }
        tokens.push({ ...token, segment });
        followsFlag = token.kind !== "positional";
      }
    }
  }
  return tokens;
}

/** One `args=` string cut at its brackets: what lies bare is demanded, what is bracketed is not. */
function argsSegments(args: string): { text: string; segment: Segment }[] {
  const segments: { text: string; segment: Segment }[] = [];
  let bare = "";
  const leaveBare = () => {
    if (bare.trim()) segments.push({ text: bare, segment: "required" });
    bare = "";
  };
  for (let at = 0; at < args.length; ) {
    const bracket = args[at];
    if (bracket !== "[" && bracket !== "(") {
      bare += bracket;
      at++;
      continue;
    }
    const end = args.indexOf(bracket === "[" ? "]" : ")", at);
    if (end === -1) throw new Error(`a documented args= string leaves \`${bracket}\` unclosed: ${args}`);
    leaveBare();
    const inner = args.slice(at + 1, end);
    const segment: Segment = bracket === "[" ? "optional" : inner.includes("|") ? "group" : "required";
    segments.push({ text: inner, segment });
    at = end + 1;
  }
  leaveBare();
  return segments;
}

function classifyToken(word: string): { text: string; kind: ArgsToken["kind"] } | null {
  const bare = word.replace(/^["'`]+|["'`]+$/g, "");
  if (!bare) return null;
  if (/^--[\w-]+$/.test(bare)) return { text: bare, kind: "flag" };
  if (/^-[^-]/.test(bare)) return { text: bare, kind: "short-flag" };
  const label = /^<(.+)>$/.exec(bare.replace(/\.{3}$/, ""))?.[1] ?? bare.replace(/\.{3}$/, "");
  return { text: label, kind: "positional" };
}

export type ArgsViolation = {
  /** The entry's `command=` value, e.g. "sessions push". */
  path: string;
  line: number;
  kind: ArgsViolationKind;
  /** What the CLI declares versus what the entry shows, for the failure message. */
  detail: string;
};

export type ArgsViolationKind =
  | "required-flag-missing"
  | "required-flag-bracketed"
  | "required-flag-param-not-required"
  | "optional-flag-unbracketed"
  | "optional-param-marked-required"
  | "required-positionals-mismatch"
  | "optional-positionals-mismatch";

/**
 * Every place the docs promise an invocation the CLI will not accept, or refuse one it will:
 * an option the CLI demands written as optional, one it defaults written as required, and a
 * number of positional arguments that does not match the signature. The contract is read from
 * cli/main.py's typer declarations, never from the page, so the page cannot certify its own lies.
 */
export function argsContractViolations(
  page: string = readDocsPage(),
  registry: Registry = deriveRegistry(),
): ArgsViolation[] {
  const violations: ArgsViolation[] = [];
  for (const entry of documentedCommands(page, registry)) {
    const command = registry.commands.get(entry.path);
    // `stash vfs` forwards args its own parser reads, so the CLI signature describes neither side.
    if (!command || command.passthrough) continue;
    violations.push(...entryContractViolations(entry, command));
  }
  return violations;
}

function entryContractViolations(entry: DocumentedCommand, command: Command): ArgsViolation[] {
  const violations: ArgsViolation[] = [];
  const report = (kind: ArgsViolationKind, detail: string) =>
    violations.push({ path: entry.path, line: entry.line, kind, detail });
  const tokens = argsTokens(entry.args);
  const showsFlag = (flag: string, segment: Segment) =>
    tokens.some((token) => token.kind === "flag" && token.text === flag && token.segment === segment);

  for (const flag of command.requiredFlags) {
    if (showsFlag(flag, "required")) {
      if (!entry.params.some((param) => param.name === flag && param.required))
        report("required-flag-param-not-required", `${flag} is demanded by the CLI but the params table does not mark it required`);
    } else if (showsFlag(flag, "optional")) {
      report("required-flag-bracketed", `${flag} is demanded by the CLI but args shows it bracketed as optional`);
    } else if (!showsFlag(flag, "group")) {
      report("required-flag-missing", `${flag} is demanded by the CLI but args never shows it`);
    }
  }

  for (const token of tokens) {
    if (token.kind === "flag" && token.segment === "required" && !command.requiredFlags.has(token.text))
      report("optional-flag-unbracketed", `${token.text} has a default in the CLI but args shows it unbracketed as required`);
  }
  for (const param of entry.params) {
    if (param.required && param.name.startsWith("--") && !command.requiredFlags.has(param.name))
      report("optional-param-marked-required", `${param.name} has a default in the CLI but the params table marks it required`);
  }

  // Positional labels are the page's to choose (`<type:id>` where the CLI says refs), so only the
  // count and the bracket shape are compared. A "( a | b )" group is exempt: mutual exclusion is
  // decided in code, which typer cannot express.
  const positionals = tokens.filter((token) => token.kind === "positional");
  const onPage = (segment: Segment) => positionals.filter((token) => token.segment === segment).length;
  const requiredInCli = command.positionals.filter((positional) => positional.required);
  const optionalInCli = command.positionals.length - requiredInCli.length;
  if (onPage("required") !== requiredInCli.length)
    report(
      "required-positionals-mismatch",
      `the CLI takes ${requiredInCli.length} required argument(s) [${requiredInCli.map((p) => p.name).join(", ")}] but args shows ${onPage("required")}: ${entry.args}`,
    );
  if (onPage("optional") !== optionalInCli)
    report(
      "optional-positionals-mismatch",
      `the CLI takes ${optionalInCli} optional argument(s) but args shows ${onPage("optional")}: ${entry.args}`,
    );
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
