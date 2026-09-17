# Stash MCP integration guide

Stash gives MCP-capable agents (Claude Code, Claude Desktop, Cursor, Codex, OpenCode,
Gemini CLI, OpenClaw, Hermes, pi — and anything else that speaks MCP) shared memory over
your Stash account: files, sessions, skills, connected sources, tables, memory, and
sharing. This is the canonical guide; it is regenerated from the shipped code, and
`cli/tests/test_mcp_integration_doc.py` fails the build if any command, flag, or tool named
here drifts from it.

> **Earlier drafts of this guide described a hosted Stash MCP endpoint, together with tool
> names that no longer exist, so an old config cannot be patched — start from the
> instructions below.**

## What Stash exposes over MCP

Installing the Stash CLI puts a **`stash-mcp`** executable on your `PATH` (it ships in the same
`stashai` package — there is nothing else to install). Running it starts a [Model Context
Protocol](https://modelcontextprotocol.io) server that speaks the **stdio** transport and
exposes the current **69 tools** listed in the [Tool reference](#tool-reference): search, the
unified VFS, sources, files & pages, tables, skills, sessions, trash, sharing, and publishing.

Three properties define the integration:

- **Local stdio only.** Stash does not host a remote MCP endpoint. Your MCP client launches
  `stash-mcp` as a child process and talks to it over stdin/stdout; the server then calls the
  Stash API like any other Stash client. Nothing to whitelist, no URL to keep alive.
- **Identity is your CLI sign-in.** The server reads the credentials stored by `stash signin`.
  Client config contains no API keys — see [Scope, auth, and troubleshooting](#scope-auth-and-troubleshooting).
- **One shared scope setting.** Whether tools act on your personal space or a workspace is the
  same setting the CLI uses (`stash workspace switch`), readable via `stash_list_workspaces`
  and switchable via `stash_switch_workspace`.

Tool parameters are self-describing: the server publishes a JSON schema per tool, so your
client sees argument names and descriptions natively — you do not need to learn them from docs.

## Install and sign in

```bash
uv tool install stashai
stash signin
```

`stash signin` authenticates in the browser and walks first-run setup (session recording, agent
hooks, folder context); re-run that wizard anytime with `stash setup`, and run `stash connect`
from any other project folder to wire that folder up. The released CLI keeps itself updated.

Both the CLI and the MCP server ride on the stored credentials, so verify the credential once:

```bash
stash vfs "ls /"
```

If that lists your roots, the server will work. On a headless machine that drives the CLI from
an MCP host (or any environment with no browser), store an already-issued key directly:

```bash
stash signin --api-key <key> --non-interactive
```

## MCP client configuration

Any MCP client that can spawn a stdio server needs the same block — this is exactly what the
web app's install tab shows:

```json
{
  "mcpServers": {
    "stash": {
      "command": "stash-mcp"
    }
  }
}
```

- **Claude Desktop:** paste it into `claude_desktop_config.json` (Settings → Developer → Edit
  Config) under the existing `mcpServers` key, then restart the app.
- **Claude Code:** add the block to the project's `.mcp.json` (this is also what
  `stash tools install` writes for registered servers, see below). `stash setup` can go
  one step further and install the stash plugin through the official Claude plugin
  marketplace, which wires hooks and agent context on top of the MCP server.
- **Everything else:** put the block wherever that client declares MCP servers.

Two practical notes:

- **Desktop launchers often miss your shell `PATH`.** If the client reports it cannot launch
  the server, replace `"stash-mcp"` with the absolute path printed by `which stash-mcp`.
- **Smoke-test the binary directly** by running `stash-mcp` in a terminal: it should start and
  wait silently on stdin (that is stdio speaking). Reaching an immediate crash or a traceback
  means the install is broken, not the client.

## Tool reference

The 69 tools the server exposes, grouped by area. Every name below exists on the shipped
server, and the guard test compares this table against `cli/mcp_server.py` in both directions.

### Search and the VFS

| Tool | What it does |
| --- | --- |
| `stash_search` | Relevance-merged search over native files, session transcripts, and connected sources |
| `stash_vfs` | One read-only shell-shaped script (`ls`, `cat`, `find`, `grep`, `tree`, pipes) over everything — see [VFS path format](#vfs-path-format) |

### Sources

| Tool | What it does |
| --- | --- |
| `stash_list_sources` | Every readable source: native `files`/`sessions` plus connected providers |
| `stash_browse_source` | List a source's entries like a file system (cursor-paginated) |
| `stash_read_source` | Read one document from a source by ref |
| `stash_add_source` | Connect a provider: `github_repo`, `google_drive`, `gmail`, `notion`, `slack`, `granola` |
| `stash_sync_source` | Trigger an immediate re-index of a source you own |
| `stash_remove_source` | Disconnect a source you own (its indexed documents cascade away) |
| `stash_snapshot_source` | Copy one connected-source document into a Skill as a self-contained page |

### Sessions and events

| Tool | What it does |
| --- | --- |
| `stash_query_events` | Query recent session events, filtered by agent or event type |
| `stash_list_agents` | Distinct agent names that have pushed events |
| `stash_push_event` | Push a new event into a session's stream |
| `stash_session_transcript` | A full session transcript as JSONL text |
| `stash_delete_session` | Soft-delete a session (restorable from the trash) |

### Memory and scope

| Tool | What it does |
| --- | --- |
| `stash_memory_tree` | The Memory wiki as a nested folder/page tree |
| `stash_list_workspaces` | Workspaces you belong to, plus which scope is active |
| `stash_switch_workspace` | Switch the active scope by name or domain, or back to personal |
| `stash_whoami` | The currently authenticated user |

### Folders and pages

| Tool | What it does |
| --- | --- |
| `stash_list_folders` | Your folders (flat) |
| `stash_create_folder` | Create a folder (nest with `parent_folder_id`) |
| `stash_edit_folder` | Rename and/or reparent a folder |
| `stash_delete_folder` | Delete a folder and everything inside it |
| `stash_tree` | Nested folder/page tree for your scope |
| `stash_list_pages` | Flat list of every page |
| `stash_read_page` | Read a page's content |
| `stash_create_page` | Create a page (optionally inside a folder) |
| `stash_edit_page` | Update a page's content and/or rename it |
| `stash_delete_page` | Delete a page |
| `stash_copy_page` | Duplicate a page as *Copy of <name>* |
| `stash_copy_folder` | Deep-duplicate a folder (subfolders, pages, tables, files) |
| `stash_copy_file` | Duplicate an uploaded file and its blob |
| `stash_batch_move` | Move many items at once |
| `stash_batch_delete` | Move many pages/files to the trash at once |
| `stash_batch_restore` | Restore many trashed pages/files at once |

### Tables

| Tool | What it does |
| --- | --- |
| `stash_list_tables` | Your tables |
| `stash_create_table` | Create a table from a columns JSON array |
| `stash_update_table` | Rename or update a table's metadata |
| `stash_delete_table` | Delete a table |
| `stash_table_schema` | A table's columns and types |
| `stash_query_table` | Query rows with optional sorting and filtering |
| `stash_insert_row` | Insert a row (column → value object) |
| `stash_update_row` | Update a row's column values |
| `stash_delete_row` | Delete a row |
| `stash_add_column` | Add a column (`text`, `number`, `boolean`, `date`, `select`, `url`) |
| `stash_delete_column` | Delete a column |
| `stash_export_table` | Return all rows of a table as JSON |

### Uploaded files

| Tool | What it does |
| --- | --- |
| `stash_list_files` | Your uploaded files |
| `stash_file_text` | Extract text content from a file (PDF, doc, …) |
| `stash_upload_file` | Upload a local file into your Stash |
| `stash_edit_file` | Rename and/or move a file |
| `stash_delete_file` | Delete a file |

### Skills

| Tool | What it does |
| --- | --- |
| `stash_list_skills` | Your skills — local SKILL.md folders and shared bundles |
| `stash_read_skill` | A skill's contents (SKILL.md and sibling pages) by folder id |
| `stash_create_skill` | Create a skill from full SKILL.md content |
| `stash_publish_skill` | Publish a skill folder publicly at `/skills/<slug>` |
| `stash_update_skill` | Update a published skill's metadata or Discover flag |
| `stash_unpublish_skill` | Stop sharing a skill (the folder itself stays) |
| `stash_get_shared_skill` | A public shared skill by its slug |
| `stash_fork_skill` | Fork a public skill into your own scope |

### Publish and Discover

| Tool | What it does |
| --- | --- |
| `stash_publish_html` | Single-call publish of an HTML page wrapped in a skill, returns the URL |
| `stash_publish_markdown` | Single-call publish of a markdown page wrapped in a skill |
| `stash_search_public_skills` | Search the public skill catalog (Discover) |
| `stash_read_public_skill` | Fetch a public skill by slug as plain text |

### Trash and sharing

| Tool | What it does |
| --- | --- |
| `stash_list_trash` | Soft-deleted pages, files, and sessions |
| `stash_restore` | Restore a trashed page, file, or session |
| `stash_purge` | Permanently delete a trashed item (not reversible) |
| `stash_share_object` | Share a folder/page/file/session/table with a person by email |
| `stash_unshare_object` | Revoke a share |
| `stash_list_shares` | Who an object is shared with |

## VFS path format

`stash_vfs` (and its CLI twin `stash vfs`) runs one **read-only** shell-shaped script over your
whole Stash. The roots:

| Root | Contents |
| --- | --- |
| `/files` | Your Files tree — pages, folders, uploaded files |
| `/sessions` | Recorded agent sessions |
| `/skills` | Skill folders (SKILL.md bundles) |
| `/memory` | The Memory wiki |
| `/sources` | Connected sources, addressed by handle |

```bash
stash vfs "ls /sources"
stash vfs --cwd /files "tree ."
```

Over MCP the same surface is one tool call: `stash_vfs(script="ls /")`. A non-zero
`exit_code` is the **shell's** result (`grep` found nothing), not a transport error — read
`stdout`/`stderr` the way you would read a terminal.

## Bring-your-own MCP servers

Stash also keeps a **registry of your own MCP servers**, used in two places: the Stash cloud
agent gets your registry written into its sprite's `.mcp.json` every turn, and you can install
any registered server into a local agent config from the CLI. Secrets (headers, env values) are
encrypted server-side at rest.

```bash
stash tools add fetch --command "uvx mcp-server-fetch"
stash tools add issues --url https://mcp.example.com/mcp --header "Authorization=Bearer <token>"
stash tools list
stash tools install fetch --agent claude
stash tools remove fetch
```

- `--command` (stdio launch command) and `--url` (http endpoint) are mutually exclusive;
  `--header`/`--env` take repeatable `KEY=VAL` pairs.
- `stash tools install <name>` writes the server into an agent's MCP config — by default this
  project's `.mcp.json` for Claude Code; `--agent pi` targets pi's `mcp.json` and requires the
  `pi-mcp-adapter` extension to be installed.
- The merge is **ownership-tracked**: entries Stash wrote are listed under a
  `stashManagedServers` key in the config file. Re-runs are idempotent, user-defined entries
  are never touched, and a name collision with a user entry fails loudly instead of clobbering.
- The web app's Tools page is the GUI for the same registry
  (`backend/routers/mcp_servers.py`).

## Scope, auth, and troubleshooting

**Auth lives in the CLI.** `stash signin` stores the credential the server authenticates with;
`stash keys list` / `stash keys revoke` manage the underlying API keys, and `stash logout`
clears everything (the MCP server stops working until you sign in again). Nothing in the MCP
client config carries a secret, so rotating a key never means editing client configs.

**Scope decides what "everything" means.** Tools act on the active scope — your personal space
or a workspace. `stash_list_workspaces` shows the active one; `stash_switch_workspace` (or
`stash workspace switch`) moves it. It is a single setting stored with your CLI
configuration, shared by the CLI, the plugins, and this server, so a switch made anywhere
applies to every tool call that follows:

```bash
stash workspace list
stash workspace switch "Acme"
```

**Troubleshooting:**

| Symptom | Cause and fix |
| --- | --- |
| Client cannot launch the server (`command not found`) | The client's environment lacks your shell `PATH` — point `command` at the absolute path from `which stash-mcp` |
| Server starts but the client shows zero tools | Client/server handshake issue: run `stash-mcp` manually — if it waits on stdin it is healthy; restart the client afterward |
| Every tool returns empty or unexpected results | Wrong sign-in or scope: check `stash_whoami` and `stash_list_workspaces`, switch scope if needed |
| 401 / auth errors after a key rotation or `stash keys revoke` | The stored credential is gone — `stash signin` again |
| A `stash tools install` target config is refused | The file is not valid JSON, or it already defines a server under that name outside Stash's management — fix the file or rename your server |
| An old config points the client at a hosted Stash URL | That transport was removed; replace the old entry with the stdio block above |
