---
"stash": patch
---

`stash shares ls` and `stash_list_shares` report a share's whole state instead of only
its list of people, which retires the bare-array answer those two surfaces used to give.

**What users get:** the folder's MCP handle reaches the CLI at all. `GET /api/v1/share`
mints it (from `PUBLIC_URL`, server-side) and returns it beside `shares` and
`general_access`, but the client method that served these two surfaces funneled that
response through the generic list helper, which keeps one key and discards its siblings.
So a folder whose link is granted — no named people, live handle — printed "No shares
found." and `[]`: the exact state where the handle is the only useful answer, and the
reason STAS-298's hand-off step H3 told the next session to read the handle with a raw
REST call "because the CLI will not tell you". `stash shares ls` now prints an
`anyone with the link — <url>` line in text mode (unwrapped, so it survives the paste into
`stash tools add --url`) and emits the endpoint's envelope in `--json` mode;
`stash_list_shares` returns that envelope to an agent.

**What was removed:** the client method `StashClient.list_object_shares`, with no alias —
the dedicated `get_share_state(object_type, object_id) -> dict` replaces it in one shot,
and every call site and test fake moved with it (zero hits for the old name under `cli/`).
Also removed: the bare-array `--json` payload of `stash shares ls` and of the
`stash_list_shares` tool result, which is now the object the endpoint sends. A script that
consumed either takes `.shares`. The generic `_list()` helper is untouched and still serves
its 17 other list endpoints; it simply no longer sits between this endpoint and its callers.

**What did not change:** the tool name `stash_list_shares` (`test_mcp_registry.py` pins
names and passes unedited), the endpoint and its query parameters, the "No shares found."
empty-state note and its wording, and a private folder's silence. `mcp_url` is read
tolerantly rather than required: the CLI and the server ship separately, so a released CLI
can be pointed at a server that predates this surface and never sends the key. An absent
key and a null one render the same silence, and neither is ever replaced by a URL the CLI
built itself.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased
bullet is the authoritative release note. This file exists because the task framework
mandates a removal record for a net-negative change — the founder may delete it.
