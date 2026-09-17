import type { Metadata } from "next";

import { Callout, Code, CodeBlock, CommandRef, H2, P, Title, Subtitle } from "../components";

export const metadata: Metadata = {
  title: "CLI Reference · Stash Docs",
  description:
    "Every stash command: push session events, browse the VFS, search, upload files, and manage skills from your terminal.",
  alternates: { canonical: "/docs/cli" },
};

export default function CLIPage() {
  return (
    <>
      <Title>CLI Reference</Title>
      <Subtitle>
        A command-line interface for managing Stash from your terminal — push session events
        and manage all resources.
      </Subtitle>

      <Callout type="tip">
        Most commands accept <Code>--json</Code> for machine-readable output.
      </Callout>

      <H2>Install</H2>
      <CodeBlock>{`uv tool install stashai`}</CodeBlock>

      <CommandRef
        command="stash upgrade"
        args="[--json]"
        description="Upgrade the Stash CLI to the latest version on PyPI. An editable checkout is refused — git pull that one instead."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash welcome"
        description="Show the post-install welcome splash again."
      />

      <H2>First-time setup</H2>
      <P>
        Run the setup wizard. It authenticates you through the browser, turns on session
        recording (pause anytime with <Code>stash stop</Code>), lets you pick which coding
        agents to record, sets up the folder you&apos;re standing in, and imports your
        conversation history in the background. No manual config editing required.
      </P>
      <CodeBlock>{`stash signin`}</CodeBlock>
      <P>
        The wizard saves everything to <Code>~/.stash/config.json</Code>. Re-run it anytime
        with <Code>stash setup</Code> — no answer is final. Self-hosting? Point the CLI at
        your instance with <Code>stash signin --api &lt;url&gt;</Code>.
      </P>
      <CommandRef
        command="stash setup"
        description="Re-run the setup wizard: session recording, agent hooks, folder context, and history import. Safe to repeat."
      />
      <CommandRef
        command="stash connect"
        description="Set up the current folder for Stash: writes .stash and adds Stash instructions to CLAUDE.md so agents working there use your Stash. Works in any folder — a git repo is not required."
      />
      <CommandRef
        command="stash import-history"
        args="[--status]"
        description="Import your historical agent conversations (Claude Code, Codex, Cursor) into Stash with parallel uploads. Safe to re-run — the server skips sessions that already exist. The wizard launches this in the background; --status attaches a live progress bar (Ctrl-C detaches)."
        params={[
          { name: "--status", type: "flag", desc: "Follow the running or last-finished import with a live progress bar." },
        ]}
      />

      <H2>Virtual filesystem</H2>
      <P>
        Use <Code>stash vfs</Code> when an agent needs to browse Stash through one
        filesystem-shaped interface without mounting anything into the OS. Your Stash
        exposes <Code>files</Code>, <Code>sessions</Code>, <Code>skills</Code>,{" "}
        <Code>tables</Code>, and <Code>sources</Code> — the last surfacing every connected
        integration (Gmail, GitHub, Slack, Jira, …) as read-only documents you can{" "}
        <Code>ls</Code>, <Code>cat</Code>, and <Code>grep</Code>.
      </P>
      <CodeBlock>{`stash vfs ls /
stash vfs "find /me -maxdepth 3 -type f"
stash vfs "rg 'database migration' /me"
stash vfs --cwd "/me/sources" "rg 'incident' ."`}</CodeBlock>
      <CommandRef
        command="stash vfs"
        args={'[--cwd PATH] "command"'}
        description="Run bash-shaped read and write commands against the virtual Stash tree."
        params={[
          { name: "--cwd", type: "string", desc: "Virtual working directory. Defaults to /." },
          { name: "command", type: "string", desc: "Bash-shaped command such as ls, find, rg, cat, sed, tee, or redirection." },
        ]}
      />

      <CommandRef
        command="stash ls"
        args="[path] [--depth N] [--json]"
        description="Everything Stash can reach, printed as one filesystem — your files, session transcripts, and every connected integration (GitHub, Slack, Gong, …). Pass a source or a path to print just part of it."
        params={[
          { name: "path", type: "string", desc: "Source or path to list, e.g. 'gong' or 'my-repo/docs'. Omit for everything." },
          { name: "--depth", type: "number", desc: "How many levels of the tree to print." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash download"
        args="<vfs_path> [-o PATH]"
        description="Download the original bytes behind a VFS path. `stash vfs cat` shows a document's extracted text; this fetches the file itself — use it when your harness reads PDFs and images directly."
        params={[
          { name: "<vfs_path>", type: "path", desc: "VFS path, e.g. '/sources/google/Part Catalogs/bendix.pdf'.", required: true },
          { name: "--output", type: "path", desc: "Destination path. Defaults to the file's name in the current directory." },
        ]}
      />

      <CommandRef
        command="stash export"
        args="[-o PATH]"
        description="Download your entire Stash as a zip of standard files: folders become directories, pages become plain .md/.html, uploads keep their original bytes — no proprietary formats, no lock-in."
        params={[
          { name: "--output", type: "path", desc: "Path for the zip. Defaults to stash-export-<timestamp>.zip in the current directory." },
        ]}
      />

      <H2>Authentication</H2>

      <CommandRef
        command="stash signin"
        args="[--api <base_url>] [--api-key <key>] [--non-interactive]"
        description="Authenticate this machine. With no flags it runs the browser flow against managed Stash and, on first run, continues into the setup wizard (recording, agent hooks, folder context, history import). Self-hosters pass --api with their instance URL. On SSH/headless it prints a URL to open instead of launching a browser. Pass --api-key to store a pre-minted key directly (no browser) on an unattended, browser-less machine — typically a self-hosted CI runner; get the key from your self-hosted instance's API-key page."
        params={[
          { name: "--api", type: "string", desc: "Base URL of the Stash server. Override for self-hosted deployments.", required: false },
          { name: "--api-key", type: "string", desc: "A pre-minted key to store directly, skipping the browser. For unattended, browser-less machines.", required: false },
          { name: "--non-interactive", type: "flag", desc: "Skip the setup wizard; just authenticate. Implied when stdin isn't a terminal.", required: false },
        ]}
      />

      <Callout>
        Setting <Code>STASH_API_KEY</Code> / <Code>STASH_URL</Code> in the environment
        authenticates <em>CLI commands</em> for CI and scripts — but it does{" "}
        <strong>not</strong> reach the streaming hooks, which read{" "}
        <Code>~/.stash/config.json</Code>. To make an unattended machine stream, use{" "}
        <Code>stash signin --api-key</Code>. Change the endpoint or streaming agents
        later from <Code>stash settings</Code>.
      </Callout>

      <CommandRef
        command="stash whoami"
        description="Display the currently authenticated user."
      />

      <CommandRef
        command="stash verify-email"
        args="[--json]"
        description="Email yourself a verification link. Verifying your email is what joins you to your company's workspace, if one exists for your email domain."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash logout"
        args="[--json]"
        description="Sign out and clear stored credentials and preferences. Streaming hooks go inert until you sign in again."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash disconnect"
        args="[--json]"
        description="Disconnect this folder from Stash by removing its .stash file — the reverse of stash connect. It does not sign you out; use stash logout for that. Idempotent: a folder that was never connected exits 0 and reports the no-op."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash settings"
        args="[--json]"
        description="Interactive settings page — change the endpoint, toggle which agents stream, and view config. Pass --json for a read-only snapshot."
        params={[
          { name: "--json", type: "flag", desc: "Print a read-only snapshot instead of the interactive page." },
        ]}
      />

      <Callout>
        After <Code>stash signin</Code>, your defaults are stored. Change the endpoint
        any time from <Code>stash settings</Code>, or set <Code>STASH_API_KEY</Code> /{" "}
        <Code>STASH_URL</Code> as environment variables for CI and scripts.
      </Callout>

      <H2>Files</H2>
      <P>
        Pages and folders live in one tree — print it with <Code>stash ls</Code> or walk it with{" "}
        <Code>stash vfs</Code>. These commands write into it.
      </P>

      <CommandRef
        command="stash files create-folder"
        args="<name> [--parent FOLDER_ID]"
        description="Create a folder in the files."
        params={[
          { name: "<name>", type: "string", desc: "Folder name.", required: true },
          { name: "--parent", type: "string", desc: "Parent folder ID." },
        ]}
      />

      <CommandRef
        command="stash files add-page"
        args="<name> [--folder FOLDER_ID] [--content '...']"
        description="Add a new page to the files."
        params={[
          { name: "<name>", type: "string", desc: "Page title.", required: true },
          { name: "--folder", type: "string", desc: "Folder ID." },
          { name: "--content", type: "string", desc: "Initial page content." },
        ]}
      />

      <CommandRef
        command="stash files read-page"
        args="<page_id>"
        description="Read a page."
        params={[
          { name: "<page_id>", type: "string", desc: "ID of the page.", required: true },
        ]}
      />

      <CommandRef
        command="stash files edit-page"
        args="<page_id> --content '...'"
        description="Update a page. Reads from stdin if --content is not given."
        params={[
          { name: "<page_id>", type: "string", desc: "ID of the page.", required: true },
          { name: "--content", type: "string", desc: "New page content. Reads from stdin if omitted." },
        ]}
      />

      <CommandRef
        command="stash files edit-folder"
        args="<folder_id> --name NAME"
        description="Rename a folder. Use stash mv to relocate it."
        params={[
          { name: "<folder_id>", type: "string", desc: "ID of the folder.", required: true },
          { name: "--name", type: "string", desc: "New folder name.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash files edit-file"
        args="<file_id> --name NAME"
        description="Rename a file. Use stash mv to relocate it."
        params={[
          { name: "<file_id>", type: "string", desc: "ID of the file.", required: true },
          { name: "--name", type: "string", desc: "New file name.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash files download"
        args="<file_ref> [-o PATH]"
        description="Download a file's bytes to a local path. Files a page embeds don't appear in the files tree — the page's markdown links them, so read the page first and download a linked file only when you need its contents."
        params={[
          { name: "<file_ref>", type: "string", desc: "File id, or the embed link from a page (/api/v1/me/files/<id>/download).", required: true },
          { name: "--output", type: "path", desc: "Destination path. Defaults to the file's name in the current directory." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>Sessions</H2>

      <CommandRef
        command="stash sessions push"
        args="<content> [--agent cli] [--type message] [--session ID] [--attach FILE]"
        description="Push a new event to your session stream."
        params={[
          { name: "<content>", type: "string", desc: "Event content to push.", required: true },
          { name: "--agent", type: "string", desc: 'Agent identifier. Defaults to "cli".' },
          { name: "--type", type: "string", desc: 'Event type. Defaults to "message".' },
          { name: "--session", type: "string", desc: "Session ID to group events under." },
          { name: "--tool", type: "string", desc: "Tool identifier." },
          { name: "--attach", type: "path", desc: "Local file path to upload and attach. Repeatable." },
          { name: "--attach-id", type: "string", desc: "Pre-uploaded file ID to attach. Repeatable." },
        ]}
      />

      <CommandRef
        command="stash sessions"
        args="[-n 20] [--json]"
        description="Your most recent session events — the log your agents push into. Pass --limit to see more of it."
        params={[
          { name: "-n, --limit", type: "number", desc: "How many events to print. Defaults to 20." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <Callout type="tip">
        To search sessions, use the unified <Code>stash search</Code> with{" "}
        <Code>--source sessions</Code> (see <strong>Sources &amp; search</strong> below). It replaces
        the old per-resource search commands.
      </Callout>

      <CommandRef
        command="stash sessions agents"
        args=""
        description="List distinct agent names that have logged events in your Stash."
        params={[]}
      />

      <CommandRef
        command="stash sessions import"
        args="[--agent NAME] [-n N] [--replace] [-y]"
        description="Import historical conversations from coding agents on this machine. Discovers conversations from Claude Code, Cursor, and Codex, then uploads them as transcripts."
        params={[
          { name: "--agent", type: "string", desc: "Only import from this agent." },
          { name: "-n, --limit", type: "number", desc: "Max conversations to import. 0 imports all (default)." },
          { name: "--replace", type: "flag", desc: "Replace sessions that already exist." },
          { name: "-y, --yes", type: "flag", desc: "Skip the confirmation prompt." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <Callout type="tip">
        Transcripts are read through the VFS, by title:{" "}
        <Code>{`stash vfs "cat '/sessions/<title>/transcript.md'"`}</Code>. List the titles with{" "}
        <Code>{`stash vfs "ls /sessions"`}</Code>.
      </Callout>

      <H2>Memory</H2>
      <P>
        Your Memory wiki lives in a reserved folder that the Memory curator — and, since{" "}
        <Code>stash memory write</Code>, any agent — maintains. Pages are addressed by path.
      </P>

      <CommandRef
        command="stash memory"
        args="[--recompute] [--curator on|off] [--json]"
        description="Show your reserved Memory folder and the nightly curator's schedule state. --recompute runs the curator now; --curator off|on toggles the nightly cloud run (on-demand runs keep working)."
        params={[
          { name: "--recompute", type: "flag", desc: "Run the Memory curator now." },
          { name: "--curator", type: "string", desc: "Turn the nightly cloud curator run off or on." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash memory write"
        args='"<Path>" [--content TEXT] [--json]'
        description="Create or update a Memory wiki page at a path (e.g. 'Customers/Chainbase'). Missing subfolders are created; a trailing .md is stripped. Long bodies pipe on stdin."
        params={[
          { name: "--content", type: "string", desc: "Page body. Reads stdin if omitted." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash memory ls"
        args="[--json]"
        description="Print the Memory wiki tree with folder and page ids."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash changes"
        args="[--since TS] [--wiki internal|external] [--folder ID]"
        description="What changed since a timestamp — history, pages, files, saves, and sources. This is the change feed the Memory curator's incremental pass consumes."
        params={[
          { name: "--since", type: "string", desc: "ISO timestamp (e.g. 2026-09-01). Omit for everything." },
          { name: "--wiki", type: "string", desc: "'internal' (your own memory — the default) or 'external' (the developer workspace's shared anonymized wiki)." },
          { name: "--folder", type: "string", desc: "Session folder id: restrict the feed to one project's sessions." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>Sources &amp; search</H2>
      <P>
        A <strong>source</strong> is anything the agent can read, exposed as a virtual file
        system: the two native sources — <Code>files</Code> and <Code>sessions</Code> — plus your
        connected sources (GitHub, Google Drive, Gmail, Notion, Slack, Granola). <Code>stash ls</Code>{" "}
        prints every source you can reach, <Code>stash vfs</Code> browses and reads them by path, and{" "}
        <Code>stash search</Code> queries one source — or everything at once.
      </P>

      <CommandRef
        command="stash sources add"
        args="<source_type> [--ref REF] [--name NAME]"
        description="Connect a source. Slack and Granola resolve their reference from your connected token; Gmail uses the mailbox email as --ref; the others need a --ref (e.g. a repo 'owner/name')."
        params={[
          { name: "<source_type>", type: "string", desc: "github_repo | google_drive | gmail | notion | slack | granola.", required: true },
          { name: "--ref", type: "string", desc: "External reference, e.g. a repo 'owner/name' or Gmail address." },
          { name: "--name", type: "string", desc: "Display name for the source." },
        ]}
      />

      <Callout type="tip">
        Browse a connected source and read one of its documents through the VFS:{" "}
        <Code>{`stash vfs "ls /sources/<handle>"`}</Code> and{" "}
        <Code>{`stash vfs "cat '/sources/<handle>/<document path>'"`}</Code>. For the original bytes
        of a document — a PDF or image your harness reads directly — use <Code>stash download</Code>.
      </Callout>

      <CommandRef
        command="stash sources sync"
        args="<source_id>"
        description="Trigger an immediate re-index of a connected source you own."
        params={[
          { name: "<source_id>", type: "string", desc: "ID of the connected source.", required: true },
        ]}
      />

      <CommandRef
        command="stash sources rm"
        args="<source_id>"
        description="Disconnect a source you own. Its indexed documents are removed."
        params={[
          { name: "<source_id>", type: "string", desc: "ID of the connected source.", required: true },
        ]}
      />

      <CommandRef
        command="stash search"
        args="<query> [--source HANDLE] [-n 20]"
        description="Search across everything you can see — files, sessions, and connected sources. Pass --source to scope to one; omit it to search everything."
        params={[
          { name: "<query>", type: "string", desc: "Search query.", required: true },
          { name: "--source", type: "string", desc: "Scope to one source handle (from stash ls). Omit to search everything." },
          { name: "--modified-after", type: "string", desc: "Only results last modified after this ISO timestamp (e.g. 2026-01-01). Results with no known modification time are excluded." },
          { name: "--modified-before", type: "string", desc: "Only results last modified before this ISO timestamp. Results with no known modification time are excluded." },
          { name: "-n, --limit", type: "number", desc: "Maximum number of results. Defaults to 20." },
        ]}
      />

      <H2>Tables</H2>
      <P>
        A table is an object in your Stash like any other: <Code>stash ls</Code> finds its id,{" "}
        <Code>stash sql</Code> queries it with read-only SQL, and <Code>stash tables export</Code>{" "}
        writes it out as CSV.
      </P>

      <CommandRef
        command="stash tables create"
        args="<name> [--columns JSON]"
        description="Create a new table with optional column definitions."
        params={[
          { name: "<name>", type: "string", desc: "Name for the table.", required: true },
          { name: "--columns", type: "JSON", desc: 'Column definitions as a JSON array of {name, type, options?}.' },
        ]}
      />

      <CommandRef
        command="stash tables update"
        args="<table_id> [--name TEXT] [--description TEXT]"
        description="Update a table's name or description."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "--name", type: "string", desc: "New table name." },
          { name: "--description", type: "string", desc: "New table description." },
        ]}
      />

      <CommandRef
        command="stash tables insert"
        args="<table_id> <data_json>"
        description="Insert a new row. Data is a JSON object with column names as keys."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<data_json>", type: "JSON", desc: "Row data as a JSON object.", required: true },
        ]}
      />

      <CommandRef
        command="stash tables import"
        args="<table_id> <file> [--format csv|json]"
        description="Bulk import rows from a file. Auto-chunks into batches of 5000. CSV uses the first row as column headers. Supports piping: cat data.csv | stash tables import <id> --format csv."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<file>", type: "path", desc: "Path to the import file.", required: true },
          { name: "--format", type: "string", desc: 'File format: "csv" or "json". Auto-detected if omitted.' },
        ]}
      />

      <CommandRef
        command="stash tables update-row"
        args="<table_id> <row_id> <data_json>"
        description="Update an existing row with a partial merge. Data is a JSON object with column names as keys."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<row_id>", type: "string", desc: "ID of the row to update.", required: true },
          { name: "<data_json>", type: "JSON", desc: "Updated row data as a JSON object.", required: true },
        ]}
      />

      <CommandRef
        command="stash tables delete-row"
        args="<table_id> <row_id>"
        description="Delete a row from a table."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<row_id>", type: "string", desc: "ID of the row to delete.", required: true },
        ]}
      />

      <CommandRef
        command="stash tables add-column"
        args="<table_id> <name> [--type text] [--options TEXT]"
        description="Add a column to a table."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<name>", type: "string", desc: "Column name.", required: true },
          { name: "--type", type: "string", desc: 'Column type. Defaults to "text".' },
          { name: "--options", type: "string", desc: "Comma-separated options for select/multiselect columns." },
        ]}
      />

      <CommandRef
        command="stash tables delete-column"
        args="<table_id> <column_id>"
        description="Delete a column from a table."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "<column_id>", type: "string", desc: "Column ID (col_xxx) or column name.", required: true },
        ]}
      />

      <CommandRef
        command="stash tables count"
        args="<table_id>"
        description="Count rows in a table, optionally with filters."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
        ]}
      />

      <CommandRef
        command="stash tables export"
        args="<table_id> [--sort COL] [--filter COL=VALUE] [--order asc|desc] [--file PATH]"
        description="Export a table as CSV — sorted and filtered if you ask. Sort and filter take column names, which are auto-resolved."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "--sort", type: "string", desc: "Column name to sort by." },
          { name: "--filter", type: "string", desc: "Filter by column name." },
          { name: "--order", type: "string", desc: "Sort direction: asc (default) or desc." },
          { name: "--file", type: "path", desc: "Write the CSV here instead of to stdout." },
        ]}
      />

      <CommandRef
        command="stash tables delete"
        args="<table_id> [-y]"
        description="Delete a table and all its data."
        params={[
          { name: "<table_id>", type: "string", desc: "ID of the table.", required: true },
          { name: "-y, --yes", type: "flag", desc: "Skip confirmation prompt." },
        ]}
      />

      <CommandRef
        command="stash sql"
        args='"<query>" [--json]'
        description="Query your tables with read-only SQL (DuckDB&apos;s Postgres-flavored dialect). A table is addressable by bare name when unique — SELECT * FROM jobs — and always by its folder path as the schema: SELECT * FROM &quot;files/Hiring&quot;.jobs. Explore with information_schema.tables and information_schema.columns."
        params={[
          { name: "<query>", type: "string", desc: "A SELECT statement.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>Uploaded Files</H2>

      <CommandRef
        command="stash upload"
        args="<path> [--skill TITLE]"
        description="Upload a single file (Markdown/HTML become pages, everything else a binary file) or a folder into your Stash. Pass --skill to also bundle it into a shareable Skill."
        params={[
          { name: "<path>", type: "path", desc: "File or directory to upload.", required: true },
          { name: "--skill", type: "string", desc: "Also publish the upload as a Skill with this title." },
        ]}
      />

      <CommandRef
        command="stash files text"
        args="<file_id>"
        description="Print extracted text for a file (PDF, image OCR, or plain text)."
        params={[
          { name: "<file_id>", type: "string", desc: "ID of the file.", required: true },
        ]}
      />

      <H2>Object operations</H2>
      <P>
        One set of verbs across every object type. Pass items as{" "}
        <Code>type:id</Code> tokens (e.g. <Code>page:abc</Code>, <Code>file:def</Code>,{" "}
        <Code>session:ghi</Code>); each verb accepts several at once.
      </P>

      <CommandRef
        command="stash rm"
        args="<type:id>... [--permanent]"
        description="Move pages, files, or sessions to trash. Pass --permanent to skip the trash window and delete immediately."
        params={[
          { name: "<type:id>", type: "string", desc: 'Items to delete, e.g. page:<id> session:"<title>". A session may be named by its title as well as its id.', required: true },
          { name: "--permanent", type: "flag", desc: "Delete immediately instead of trashing." },
        ]}
      />

      <CommandRef
        command="stash restore"
        args="<type:id>..."
        description="Restore pages, files, or sessions from trash."
        params={[
          { name: "<type:id>", type: "string", desc: 'Items to restore, e.g. page:<id> session:"<title>", naming a session as `stash trash list` prints it.', required: true },
        ]}
      />

      <CommandRef
        command="stash mv"
        args="<type:id>... (--to-folder ID | --to-root)"
        description="Move pages, files, folders, tables, or sessions into a folder, or to the root."
        params={[
          { name: "<type:id>", type: "string", desc: 'Items to move. A session may be named by its title as well as its id.', required: true },
          { name: "--to-folder", type: "string", desc: "Target folder id." },
          { name: "--to-root", type: "flag", desc: "Move to the root." },
        ]}
      />

      <CommandRef
        command="stash cp"
        args="<type:id>... [--to-folder ID]"
        description="Duplicate pages, files, or folders as 'Copy of <name>'."
        params={[
          { name: "<type:id>", type: "string", desc: "Items to copy.", required: true },
          { name: "--to-folder", type: "string", desc: "Target folder id for the copies." },
        ]}
      />

      <CommandRef
        command="stash trash list"
        args="[--json]"
        description="List trashed pages, files, and sessions — what stash rm parked, with the ids stash restore takes back."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>Skills</H2>
      <P>
        A <strong>Skill</strong> is a special folder — one containing a <Code>SKILL.md</Code> —
        of pages, files, and tables. Publishing a skill makes it publicly readable at its link (optionally listed
        in Discover); to share privately with a specific person, share its folder
        like any other folder. (The <Code>stash</Code> CLI name is unchanged.)
      </P>

      <CommandRef
        command="stash skills list"
        args=""
        description="List Skills in your Stash."
        params={[]}
      />

      <CommandRef
        command="stash skills create"
        args="<name> [--public] [--discover]"
        description="Create a skill: a folder with a SKILL.md template. Pass --public to publish immediately."
        params={[
          { name: "<name>", type: "string", desc: "Skill name (becomes the folder name).", required: true },
          { name: "--public", type: "flag", desc: "Publish immediately and mint a shareable link." },
          { name: "--discover", type: "flag", desc: "List the public Skill in the Discover catalog (requires --public)." },
        ]}
      />

      <CommandRef
        command="stash skills add"
        args="<folder> [--json]"
        description="Upload a local skill folder (must contain a SKILL.md) into your Files."
        params={[
          { name: "<folder>", type: "path", desc: "Local folder containing a SKILL.md.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash skills publish"
        args="<folder_id> [--discover]"
        description="Publish an existing skill folder: mint its share record and print the public URL."
        params={[
          { name: "<folder_id>", type: "string", desc: "The skill folder to publish.", required: true },
          { name: "--discover", type: "flag", desc: "List the public Skill in Discover." },
        ]}
      />

      <CommandRef
        command="stash skills update"
        args="<skill_id> [--title TEXT] [--description TEXT] [--no-discover]"
        description="Update a published Skill's metadata or its Discover listing flag."
        params={[
          { name: "<skill_id>", type: "string", desc: "ID of the published Skill.", required: true },
          { name: "--title", type: "string", desc: "New title." },
          { name: "--description", type: "string", desc: "New description." },
          { name: "--discover", type: "flag", desc: "List the Skill in Discover; pass --no-discover to hide it." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash skills snapshot-source"
        args="<skill_id> --source ID --path PATH"
        description="Copy a point-in-time snapshot of one connected-source document into the Skill as a page, so the skill stays self-contained."
        params={[
          { name: "<skill_id>", type: "string", desc: "ID of the Skill.", required: true },
          { name: "--source", type: "string", desc: "Connected-source id (from stash ls).", required: true },
          { name: "--path", type: "string", desc: "Document path within the source.", required: true },
        ]}
      />

      <CommandRef
        command="stash skills fork"
        args="<slug>"
        description="Fork a public Skill: deep-copy its folder into your Stash."
        params={[
          { name: "<slug>", type: "string", desc: "Public Skill slug.", required: true },
        ]}
      />

      <CommandRef
        command="stash skills unpublish"
        args="<skill_id>"
        description="Stop sharing a Skill: delete its publish record. The folder stays."
        params={[
          { name: "<skill_id>", type: "string", desc: "ID of the published Skill.", required: true },
        ]}
      />

      <CommandRef
        command="stash skills install"
        args="<slug> [--project] [--dir PATH]"
        description="Install a public Skill into your agent's skills directory. Installed skills are tracked and auto-update whenever skills sync runs (the plugin runs one at every session start); a one-shot notice at the next session start lists what changed."
        params={[
          { name: "<slug>", type: "string", desc: "Public Skill slug.", required: true },
          { name: "--project", type: "flag", desc: "Install into ./.claude/skills (this repo only) instead of ~/.claude/skills." },
          { name: "--dir", type: "string", desc: "Custom skills directory." },
        ]}
      />

      <CommandRef
        command="stash skills uninstall"
        args="<slug-or-name> [--project] [--dir PATH]"
        description="Remove an installed Skill and stop auto-updating it."
        params={[
          { name: "<slug-or-name>", type: "string", desc: "The installed skill's slug or folder name.", required: true },
        ]}
      />

      <CommandRef
        command="stash skills follow"
        args="[--project] [--dir PATH]"
        description="Auto-install skills people share with you. New shared skills land at the next skills sync and update like any installed skill; stash skills unfollow turns it off (already-installed skills stay)."
        params={[]}
      />

      <CommandRef
        command="stash skills unfollow"
        args="[--project] [--dir PATH]"
        description="Stop auto-installing newly shared skills; already-installed skills stay. Idempotent: a root that does not follow already has nothing to stop — the command exits 0 and reports the no-op."
        params={[
          { name: "--project", type: "flag", desc: "Stop following for ./.claude/skills (this repo only)." },
          { name: "--dir", type: "string", desc: "Skills directory to stop following into." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash skills sync"
        args="[--project] [--dir PATH]"
        description="Two-way sync between the local skills directory and your Stash: your own skills three-way sync (conflicts are skipped loudly), installed skills refresh from their cloud copy. The plugin runs this automatically at session start."
        params={[]}
      />

      <CommandRef
        command="stash browse"
        args='[query] [--sort trending|newest|popular] [--no-pick]'
        description="Browse the public Skill catalog. Opens an interactive picker by default; pass --no-pick to print a flat list instead."
        params={[
          { name: "query", type: "string", desc: "Optional search query." },
          { name: "--sort", type: "string", desc: "Ranking: trending (default), newest, or popular." },
          { name: "--pick", type: "flag", desc: "Interactive picker on (default). --no-pick prints a flat list." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash read"
        args="<url>"
        description="Read a public Skill and print its contents. Takes the Skill's URL or slug."
        params={[
          { name: "<url>", type: "string", desc: "Public Skill URL or slug.", required: true },
        ]}
      />

      <CommandRef
        command="stash share"
        args="[--title TEXT] [--session ID] [--file PATH]"
        description="Share a session as a public Skill: publishes a focused summary (the question and the finding), the full conversation transcript, and any attached files as one public Skill."
        params={[
          { name: "--title", type: "string", desc: "Title for the shared Skill." },
          { name: "--session", type: "string", desc: "Session ID or title. Auto-detected if omitted." },
          { name: "--file", type: "path", desc: "File to attach. Repeatable." },
        ]}
      />

      <CommandRef
        command="stash prompts agent-guidance"
        args="[--json]"
        description="Print the canonical 'what is a Skill and when to create one' guidance block — for coding agents to re-inject mid-session (Claude Code, Codex, Cursor, etc.)."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>MCP servers</H2>
      <P>
        Register MCP servers once and every agent gets them: your cloud agent&apos;s{" "}
        <Code>.mcp.json</Code> is refreshed on each turn, and <Code>stash tools install</Code>{" "}
        writes an entry into the target agent&apos;s own MCP config — this repo&apos;s{" "}
        <Code>.mcp.json</Code> for Claude Code by default, or pi&apos;s agent-dir{" "}
        <Code>mcp.json</Code> with <Code>--agent pi</Code>. Header and env secrets
        are stored encrypted.
      </P>

      <CommandRef
        command="stash tools list"
        args="[--json]"
        description="List your registered MCP servers."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash tools add"
        args="<name> (--url URL | --command CMD) [--header K=V] [--env K=V]"
        description="Register an MCP server: --url for a remote (HTTP) server, --command for a local (stdio) one."
        params={[
          { name: "<name>", type: "string", desc: "Server name, unique in your account.", required: true },
          { name: "--url", type: "string", desc: "HTTP MCP endpoint (remote transport)." },
          { name: "--command", type: "string", desc: "Command line to launch a stdio server." },
          { name: "--header", type: "string", desc: "HTTP header as KEY=VALUE (repeatable; stored encrypted)." },
          { name: "--env", type: "string", desc: "Env var for stdio servers as KEY=VALUE (repeatable; stored encrypted)." },
        ]}
      />

      <CommandRef
        command="stash tools install"
        args="<name> [--agent claude|pi]"
        description="Write a registered server into the target agent's MCP config (merged — your own entries are never touched; re-runs are idempotent). Targets pi only when the pi-mcp-adapter extension is registered (pi install npm:pi-mcp-adapter); otherwise it fails loud and writes nothing."
        params={[
          { name: "<name>", type: "string", desc: "A server from stash tools list.", required: true },
          { name: "--agent", type: "string", desc: "Target agent: claude (default — this repo's .mcp.json) or pi (its agent-dir mcp.json). Other agents fail loud." },
        ]}
      />

      <CommandRef
        command="stash tools remove"
        args="<name>"
        description="Delete a registered MCP server."
        params={[
          { name: "<name>", type: "string", desc: "Server name.", required: true },
        ]}
      />

      <H2>Workspaces</H2>
      <P>
        A workspace is a shared team scope: members&apos; sessions, events, and searches can run
        against it instead of their personal Stash. Switching applies everywhere on your machine —
        the CLI, every agent plugin, and the MCP server. Team workspaces are set up for you — email{" "}
        <Code>sam@joinstash.ai</Code>.
      </P>

      <CommandRef
        command="stash workspace list"
        args=""
        description="List workspaces you belong to, marking the active scope."
        params={[]}
      />

      <CommandRef
        command="stash workspace switch"
        args="<name>"
        description="Route sessions, events, transcripts, and searches to this workspace — or back with stash workspace switch personal."
        params={[
          { name: "<name>", type: "string", desc: "Workspace name or domain, or 'personal'.", required: true },
        ]}
      />

      <H2>Cloud agents</H2>
      <P>
        Stash can run agents for you in the cloud: chat personas you configure and
        prompt-scheduled runs, all started and followed from the CLI.
      </P>

      <CommandRef
        command="stash agent list"
        args="[--json]"
        description="List your configured agents — personas, models, and schedules."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash agent chat"
        args='"<message>" [--session ID] [--agent NAME]'
        description="Start (or continue) a cloud agent chat and stream the turn live. Ctrl-C disconnects the stream, which stops the turn on the box."
        params={[
          { name: "<message>", type: "string", desc: "The message to send.", required: true },
          { name: "--session", type: "string", desc: "Continue an existing chat session (id or title)." },
          { name: "--agent", type: "string", desc: "Agent name or id. The default agent is used if omitted." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash agent status"
        args="<session>"
        description="Whether a turn is currently running in a chat session."
        params={[
          { name: "<session>", type: "string", desc: "The chat session (id or title) to check.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash agent watch"
        args="<session> [--poll SECONDS]"
        description="Follow a chat session live — works for turns started anywhere (web, Slack, a schedule, or another terminal). Exits when the turn ends."
        params={[
          { name: "<session>", type: "string", desc: "The chat session (id or title) to follow.", required: true },
          { name: "--poll", type: "number", desc: "Poll interval in seconds. Defaults to 2." },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash agent stop"
        args="<session>"
        description="Stop the turn running in a chat session (kills the run on the box)."
        params={[
          { name: "<session>", type: "string", desc: "The chat session (id or title) whose turn to stop.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash agent run"
        args="<agent>"
        description="Run a prompt-scheduled agent now and stream the run live."
        params={[
          { name: "<agent>", type: "string", desc: "Scheduled agent name or id.", required: true },
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <H2>Shares</H2>
      <P>
        Share a single object — a folder, page, file, session, or table — with a specific person by
        email. If they don&apos;t have an account yet the share is recorded as pending and converts
        when they sign up. (To share a whole folder of related work, convert it to a <strong>Skill</strong>.)
      </P>

      <CommandRef
        command="stash shares ls"
        args="<object_type> <object_id>"
        description="List who an object is shared with."
        params={[
          { name: "<object_type>", type: "string", desc: "folder | page | file | session | table.", required: true },
          { name: "<object_id>", type: "string", desc: "ID of the object, or a session's title.", required: true },
        ]}
      />

      <CommandRef
        command="stash shares add"
        args="<object_type> <object_id> <email> [--permission read]"
        description="Share an object with a person by email."
        params={[
          { name: "<object_type>", type: "string", desc: "folder | page | file | session | table.", required: true },
          { name: "<object_id>", type: "string", desc: "ID of the object, or a session's title.", required: true },
          { name: "<email>", type: "string", desc: "Recipient email (pending until they sign up).", required: true },
          { name: "--permission", type: "string", desc: "read | write | admin. Defaults to read." },
        ]}
      />

      <CommandRef
        command="stash shares rm"
        args="<object_type> <object_id> <principal_id> [--principal-type user]"
        description="Revoke a person's access to an object."
        params={[
          { name: "<object_type>", type: "string", desc: "folder | page | file | session | table.", required: true },
          { name: "<object_id>", type: "string", desc: "ID of the object, or a session's title.", required: true },
          { name: "<principal_id>", type: "string", desc: "The user id to revoke (from stash shares ls).", required: true },
          { name: "--principal-type", type: "string", desc: 'Principal kind. Defaults to "user".' },
        ]}
      />

      <H2>Keys</H2>

      <CommandRef
        command="stash keys list"
        description="List your active API keys (one per device / login)."
      />

      <CommandRef
        command="stash keys revoke"
        args="<key_id>"
        description="Revoke an API key by ID. Any device using it will receive a 401 on the next call."
        params={[
          { name: "<key_id>", type: "string", desc: "ID of the key to revoke.", required: true },
        ]}
      />

      <H2>Streaming & hooks</H2>
      <P>
        <Code>stash setup</Code> installs the streaming hooks for every supported coding agent on
        your <Code>$PATH</Code>, and <Code>stash connect</Code> decides which folders they read.
        Switch the whole machine off with <Code>stash stop</Code> and check what is actually
        arriving with <Code>stash status</Code>.
      </P>

      <CommandRef
        command="stash start"
        description="Resume streaming agent transcripts, undoing a stash stop."
      />

      <CommandRef
        command="stash stop"
        description="Stop streaming agent transcripts from this machine."
      />

      <CommandRef
        command="stash status"
        args="[--json]"
        description="Per-agent upload health: whether each plugin's stream is healthy, how much is queued, and the last success, failure, and error."
        params={[
          { name: "--json", type: "flag", desc: "Machine-readable output." },
        ]}
      />

      <CommandRef
        command="stash settings"
        args="[--json]"
        description="Open the interactive settings page."
        params={[
          { name: "--json", type: "flag", desc: "Print a read-only snapshot of settings instead of opening the interactive page." },
        ]}
      />
    </>
  );
}
