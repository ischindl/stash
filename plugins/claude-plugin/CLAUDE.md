# Stash Plugin

IMPORTANT: You have the `stash` CLI on your PATH. When the user mentions "Stash", their Stash, their activity, or transcripts, always use this CLI. Run `stash --help` to see all available commands.

## What a Skill is

<!-- stash:skill-model -->
A Skill is a *special folder* — one containing a SKILL.md — holding related artifacts
(pages, files, tables) that shares like any folder and gains a public URL when
published. Use one when you're publishing a *collection* of related things together — a
project writeup with its supporting files, a research thread with its sources, a session
transcript frozen as a page plus the files it produced.

A Skill is **not** a wrapper to slap on every single file you happen to share. One-item Skills
clutter Discover and defeat the model. Pick the right tool:

- Share a single file or a folder/project → `stash upload <path> --json`, hand over `app_url` (no Skill).
- Publishing a curated bundle → `stash upload <path> --skill "<title>" --json`.
- Creating a fresh skill → `stash skills create "<name>" --description "<what it holds>" --public --json`.
- Share a coding session → `stash share` (this one), or `stash share --session "<title>"` for another.

Run `stash prompts agent-guidance` to reprint this rule mid-session.

## Stash CLI

Most things are plain `stash` CLI subcommands. Always use `--json` for machine-readable output when parsing results.

### Everything as a filesystem
`stash ls` renders everything Stash can reach as one tree — your files, session transcripts, and every connected integration (GitHub, Slack, Gong, Gmail, Drive, Notion, …). When asked what you have access to, run it and show the tree.
```bash
stash ls                           # Everything as a filesystem
stash ls gong                      # One integration's contents
stash ls my-repo/docs              # Drill into a directory
stash ls -L 3 --json               # Deeper tree, machine-readable
```

### Virtual filesystem
Use `stash vfs` when you want to browse Stash like a filesystem without mounting anything into the OS. It accepts bash-shaped commands over the virtual Stash tree:
```bash
stash vfs ls /
stash vfs "find /me -maxdepth 3 -type f"
stash vfs "rg \"query\" /me"
stash vfs "cat '/me/README.md'"
```

### Plugin control
```bash
stash signin                       # Sign in + first-run setup (auth, recording, hooks)
stash setup                        # Re-run the setup wizard anytime
stash settings                     # Interactive settings page (streaming, scope, endpoint, …)
stash stop                         # Pause session recording across every plugin (stash start resumes)
```

### Memory
```bash
stash memory ls                                # The Memory wiki tree with ids
stash memory write "Topic/Page" --content "…"  # Create or update a wiki page (stdin for long bodies)
stash memory --curator off                     # Turn the nightly cloud curator off (on to resume)
```

### Files, history, tables

### Files
```bash
stash vfs "tree /me/files"                          # Show folders and pages
stash vfs "ls /me/files"                            # List your pages
stash vfs "find /me -name '*.md'"                   # List your pages
stash files create-folder "name"                    # Create a folder
stash files add-page "title" --content "markdown content"
stash vfs "cat '/me/files/<page>.md'"               # Read a page
stash files edit-page <page_id> --content "new content"
```

### History (Agent Event Logs)
```bash
stash sessions agents                                  # List distinct agent names
stash sessions push "text" --session "<session_id>" --agent <name> --type <event_type>
stash vfs "cat '/me/sessions/_index.jsonl'"            # Query events
stash search "query"                                   # Full-text search
```

### Tables
```bash
stash vfs "cat '/me/tables/_index.jsonl'"   # List tables
stash sql "SELECT * FROM <table> LIMIT 20"  # Query rows
```

### Tips
- Use `--json` flag on any command for JSON output
- The CLI reads config from `~/.stash/config.json`
