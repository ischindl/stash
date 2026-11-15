---
"stash": patch
---

A connected source that does not answer in time is now named instead of taking the whole listing down with it, and `stash ls` answers instead of dying.

**The problem this closes.** `cli/client.py` issues its tree request on the shared
`httpx.Client(timeout=30)`, and `sources_tree` awaited every provider's
`list_documents` one after another — so one account holding several integrations cost the
*sum* of their latencies, and any sum over 30 s produced a transport exception naming
nothing. Measured on a stack with 42 live connections across 10 providers: the tree route
spent **48.3 s** server-side (**50.5 s** summed across providers), every connection answered
in 1.4–1.9 s, and the pool was idle at 3 connections of 20, so the seconds were spent
waiting on provider I/O one at a time and not on database work or payload size. On the same
stack, `stash ls` reached that ceiling — `httpx: ReadTimeout` after **30.03 s**, exit 2, zero
bytes written.

**What changed.** `sources_tree` fans its providers out and `_provider_tree_node` fans its
connections out, both under one `asyncio.Semaphore` and one deadline, with each read wrapped
in its own `asyncio.wait_for`. Three constants in `backend/services/source_service.py` carry
the contract and the two inequalities that make it safe sit beside them as assertions:
`TREE_MEMBER_BUDGET_SECONDS = 10.0` per connection, `TREE_ROUTE_BUDGET_SECONDS = 18.0` for
the route, `TREE_READ_CONCURRENCY = 8` concurrent provider reads. A connection that misses is
recorded as a `listing_error` sentence on its folder — provider, the connections that missed
by name, or counted as `k of n connections` past three, the budget, and the seconds it spent
— and the same sentence lands on the provider node, so the caller never has to infer a gap
from an absent child. A provider whose whole listing raises degrades to an empty folder
carrying that sentence instead of taking the response down; the catch is `except Exception`,
so a cancelled route still cancels. The serial `_member_tree` is gone rather than left beside
the bounded path. The VFS mount's partiality channel went from a bare count to a sentence, so
a source folder says which of its three fates it met — the materialization cap, a listing that
gave no cursor, or a call that never answered — where it previously said nothing at all, and
one leg that used to raise (`/sources` when the connection list itself failed) now mounts an
empty, warned folder. `stash ls` marks a short provider and the connection folder that missed
with `listing incomplete` and writes the server's sentence to stderr from both render sites,
markup-escaped; drilling into one path warns about that source alone.

**What users see.** On the same 42-source stack, `stash ls` now answers in **8.22 s** with
50,354 bytes at exit 0 where it previously died at 30.03 s with nothing, `ls /files` no longer
blocks the shared timeout at all, and `stash changes` — the next thing a curator wants after a
listing — is reachable at 0.32 s. Squeezing the per-connection budget to 0.5 s on the same
stack makes the degradation visible on purpose: `ls` answers in **3.10 s** at exit 0 and stderr
names all five stragglers, e.g. `google: 11 of 12 connections did not answer within the 0.5 s
listing budget (1.6 s elapsed) — their documents are not in this listing`. `stash vfs ls
sources` answers every provider in 0.36 s.

**What did not change:** `cli/client.py`'s shared `timeout=30`, byte for byte, and every
per-route exception beside it; `depth`/`limit` parameters, `source_tree_builder.sanitize`,
`TREE_DOC_LIMIT`, the provider-root fold, and the tree's JSON shape — this card adds one
optional key to it; exit codes, so `ls` stays 0 for a partial listing (`--json` stdout stays
parse-clean and warnings go to stderr, which is how `changes --json` already handles it);
search's exit-2 partial contract and `search_complete`; the per-handle listing route, which
one integration per call never approached the ceiling; and the VFS mount's native roots, which
have no provider fan-out to bound. `docs/vfs-search.md` now says plainly that a listing is the
opposite case to a partial search, because the exit-2 rule reads as if it covered both.

**How it is kept honest.** 16 new tests are red against the pre-fix code and green at this tip.
The strongest one reads peak in-flight overlap and answers `only 1 of 6 listings were in flight
at once` against the old code — the sum, measured in the test itself; another proves a
straggler's raise used to escape `sources_tree` as `RuntimeError`; the CLI pair proves a
provider with a gap used to render a full document tree with an empty stderr; the VFS pair
proves an entry listing that raised used to mount an empty folder at exit 0, and that a failed
connection list made `/sources` indistinguishable from an account with no integrations. Two
tests assert the inequalities against `timeout=30` read off the client and the pool bound read
off `config.py`, so a future constant that breaks the ladder is caught by the number it breaks
rather than by prose. Four further tests are guards, not proofs, and are labelled in the task
document as passing before the fix too.
