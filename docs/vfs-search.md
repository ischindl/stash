# Search coverage

`POST /api/v1/me/vfs` returns `searches` alongside stdout/stderr. Each entry
reports the requested roots, the current listing's file count, the range
attempted, successfully searched files, unreadable files, listing completeness,
and whether another scan page is available. `search_complete` is null for
commands without a document search, true for complete searches, and false if any
search is partial. Pipes and later commands preserve earlier coverage metadata.

Partial searches retain matches but exit 2, including when no matches were found.
Only a complete search with no matches exits 1. Never interpret a partial empty
result as evidence that the information is absent. Continue or narrow the search;
if that is not possible, disclose the incomplete coverage in the answer.

A listing is held to the opposite rule: it answers and exits 0 while incomplete,
and names what it is missing. See “Incomplete source listings” below.

When `has_more` is true and `next_command` is present, run it in a **new request** with
the same credential, `user_id`, and cwd. Each request retains the 400-document
read budget. A cursor is bound to the query and visible path listing, and is
rejected when that listing changes. It grants no access: every new request
reauthenticates and rebuilds the caller's scoped tree, and individual reads still
check current permission and shared-publication approval.
If no continuation command is available, shorten the query or narrow the scope;
the server never suggests a command exceeding the HTTP script length limit.

Continuation results explicitly describe only their range and remain partial as
standalone results, even on the last page. Combine all ranges, check for read or
listing errors, and stop when `has_more` is false. This is not a frozen snapshot;
document bodies can change between requests. A failed read requires a fresh,
narrower search; it is never silently counted as searched. Do not automatically
loop without an overall request/time budget.

# Incomplete source listings

`stash ls`, `stash ls <path>` and `stash vfs` listing commands bound their own
work instead of running until the client gives up. The tree route lists every
integration at once under one deadline and one read cap; a connection that does
not answer inside its budget is **named**, not dropped, and the rest of the
listing still arrives. The budgets live as `TREE_ROUTE_BUDGET_SECONDS`,
`TREE_MEMBER_BUDGET_SECONDS` and `TREE_READ_CONCURRENCY` in
`backend/services/source_service.py`, and they stay under the CLI client's
shared 30 s request timeout, which this contract exists to make unnecessary.

A provider folder that came back short carries `listing_error`: a sentence
naming the provider, the connections that missed (by name, or counted as
`k of n connections` when there are more than three), the budget, and the
seconds spent. `stash ls` marks the folder `listing incomplete`, repeats the
sentence on stderr, and leaves stdout — and `--json`, which carries the field
inside `sources` — unchanged. **It still exits 0.** An answer that says what it
is missing is an answer; only search partiality is an exit-2 condition.

The same distinction holds in the VFS mount. A source folder whose entry
listing was cut short — a materialization cap, a listing that gave no cursor to
continue from, or a call that never answered — carries a sentence naming which
of those happened. `ls`, `find` and `tree` print that sentence on stderr beside
their `INCOMPLETE` marker; a `grep` over such a folder records
`listing_complete: false` in its coverage entry, which makes the search partial
and takes the exit-2 path described above. An empty listing that answered is
silent; only a listing that could not be read warns.

Treat a named gap as “not checked”, never as “does not exist”. Retry the
provider, narrow the path, or disclose the gap in the answer.

# Indexed developer wiki lookup

Start with `search 'part number or topic'` through the existing VFS endpoint.
It returns at most 20 ranked candidates from the selected customer's private
wiki and approved shared knowledge. Use `--scope shared` or `--scope private`
to narrow it, and `--limit N` for 1–50 results. Direct API clients can use
`GET /api/v1/me/vfs/wiki-search?query=...&user_id=...&scope=wiki&limit=20`.
Omitting `user_id` searches approved shared knowledge only.

The PostgreSQL index covers titles and body text, including part/model numbers;
it excludes editable metadata. Customer ownership and current publication
approval apply before ranking and limiting. Exact phrase occurrences rank ahead
of other term matches. Relevance is normalized by document length so long index
pages do not dominate through repetition; private knowledge breaks relevance ties.
Candidates include paths and bounded excerpts. Read the best candidates through
VFS before answering; each read checks current access again.

This is a lexical candidate search, not an exhaustive fact check or semantic
answer. `has_more` means additional candidates matched and the response is
partial. Refine the query or raise the bounded result limit. Files, transcripts,
and connected sources remain outside this command's scope; expand into those
only when the wiki is insufficient. Empty wiki results do not establish that
the information is absent. Existing client wrappers must preserve stdout,
stderr, exit code, and preferably structured coverage; no customer repository
change is included in this Stash implementation.
