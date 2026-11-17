---
"stash": patch
---

Run one curator's history drain as several parts at once, and fold what they produce back
into the wiki in part order rather than in the order the workers happened to finish.

**The problem this closes.** A `full_history` drain was one pair and one run: `drain_through`
beside `drain_cursor`, guarded by a Redis lock keyed `agent-run:<agent_id>` — the whole agent.
A second shard of the same curator therefore printed `is busy; skipping` and left, which is what
was measured on the founder copy at 10:55 (`agent-run:42fad1ea…`, TTL ~1800 s). One worker could
only ever be one model turn deep on a backlog measured at 4 191 130 events and 549 421 829
characters — about 140M tokens — while the same endpoint, measured the previous day, served 4,8
tok/s at one concurrent turn and 13,5 tok/s at three with per-turn latency all but unchanged. The
box was the limit, not the model, and the lock is what hid it.

**What changed.** A part is now a subject of its own: the lane key gains the suffix
`part:<part_id>`, so two or more parts of one curator hold their locks at once while every
un-parted subject — the incremental run, the compose pass, every test that names
`agent-run:<agent_id>` — keeps exactly the key it names today. Each part owns a slot inside the
SAME `agents.drain_cursor` jsonb (no column, no migration) holding its window, its own resumable
read position, its `state`, and the `claim_expires_at` that makes a dead worker's slot reclaimable
after the deadline and not before. `agents.drain_through` stopped being a primary fact and became
derived — the `from` of the oldest slot whose history the wiki has not absorbed — recomputed in the
same UPDATE that moves or prunes any slot, never hand-stamped, never NULL while a plan or a slot
stands. Every reader of a drain (feed, backlog figure, pending gate, prompt text, and `stash
changes`, which gained `--until`) now carries one closed bound named `until` and excludes the same
rows, with the seam row belonging to the earlier part, so no row is read twice and none is lost.

**How the parts' output is composed.** A part's model turn writes no shared wiki page and never
chooses a page name: it reads its window, and the server writes that part's report to one page
named from the `part_id`. Shared pages are written by a compose pass which is an ordinary curator
run with no part id, so it holds the un-partitioned lock and is the only shared-wiki writer while
the fan-out works; it folds only the contiguous `done` prefix in ascending `part_id` and hands
those reports to the run's existing writer turn as extracts. Two windows that both speak about one
page therefore reach one writer that has read both claims, instead of two writers meeting on that
page as last-write-wins. The rejected alternative — each part writing wiki pages with page writes
fenced on `content_hash` — stays rejected, and no fence parameter was added to `update_page` or the
page routes: a part's writer is the `stash` CLI through those routes, so the hash would be a value
the model can omit.

**What an operator sees.** `CURATOR_DRAIN_MAX_CONCURRENT_TURNS` (default 3) bounds live drain
turns at ONE endpoint — part turns and the compose pass together — and `CURATOR_DRAIN_PART_HOURS`
(default 24) is a granularity, not a measurement. A refused part prints
`drain part=<id> agent=<id> declined guard=endpoint_pressure gauge=… limit=…`, spends no turn and
no credit, and hands its slot claim straight back so the next dispatch takes it when room frees. A
committed part prints `drain part=<id> agent=<id> committed_through=… characters=… wall=…s` and
prints neither `turn started` nor `turn finished`, which are the un-parted run's bracket. The
worker pool is a second, often lower, supply and is reported rather than reconciled: the heavy lane
runs `--concurrency=2` per replica.

**What did not change, and could not be allowed to.** `backend/services/scoped_curation_service.py`
is absent from the diff: the scoped lane never learned what a part is, `_MAX_CONCURRENT_SCOPES`
stands, `advance_watermark`'s clamp and `finish_drain`'s hand-off are the same primitives, and the
busy-skip skip-and-refund path still belongs to the un-parted dispatch. `RUN_COMMIT_RESERVE_SECONDS`
is not a part allowance — every part gets the same grant an un-parted run gets, per turn, neither
divided nor reused, so a fan-out spends more of the month's turns rather than a wider slice each.
The one accepted limit worth naming: `last_run_*` is a single triple for many parts, so it names the
most recently resolved dispatch and not the live set; STAS-462's readout is where an N-slot answer
belongs.

**Tests.** `backend/tests/test_drain_fan_out.py` is the new file (34 tests): slots advancing
independently with a stale write refused by part id; the derived instant never NULL beside a plan
and refusing to leap past an un-composed `done` slot; a hand-edited column beside parts raising
rather than being repaired; a drain seeded by raw SQL served as exactly one part with its instant
derived; adjacent parts reading disjoint row sets with the seam owned by the earlier part; two part
locks of one curator held together, named by the exact key; a dead part's slot reclaimable only
after its deadline; a part run spending one digest turn, committing its own slot, writing its own
server-named report page and chaining the next; the gauge admitting every room it has and then
refusing by name; parts committed B-then-A composed A-then-B; two reports about one page landing as
one writer carrying both claims; a fold that prunes its slots and recomputes the derived instant in
one transaction, retiring only when nothing is pending at the newest composed edge; and a plan
chasing `now` keeping every seam on the grid its first cut fixed. Two mutations
were run and reversed on the lane tip, against a live Postgres, and the tree was restored to its
own bytes before anything else was measured: reading the reports in descending part order reddens
`test_reports_fold_in_part_order_whatever_order_the_parts_finished_in`,
`test_two_reports_about_one_page_reach_one_writer_carrying_both_claims`, and
`test_reports_waiting_earn_the_ordinary_run_while_other_parts_still_run` (3 of 34 in
`test_drain_fan_out.py`); dropping the window's closing bound from the part's pending gate reddens
`test_an_empty_window_earns_its_edge_without_a_model_turn`, and the same splice also reddens
`test_a_windowed_gate_backlog_and_feed_exclude_the_same_rows`, which is where the seam's closing
operator is asserted for the feed, the backlog and the gate at once.

**What the live proof took back.** Run against a copy of the founder database with the fan-out
actually executing, the plan died within two minutes of its first part completing:
`cut_drain_part would cut window 20261117T121345 over a slot the cursor already carries`. The cut
had clamped the head window's close to a live clock reading, so the next pass opened FROM that
mid-second instant, and because a part id names seconds, the id it reached for was the one the
neighbouring slot had already spent. Two windows were therefore wrong, not one: a 45-second window
claimed a dispatch and the planning gate's room while whole hours waited behind it, and two passes
inside one second collided on one id. A window is now opened only once its close has been reached,
the coordinator plans up to the last seam at or before `now`, and no window closes off the grid the
drain's first cut fixed. The proof also settled two things the suite could not see: `characters=0`
for a window is honest only when the rows are absent at the curator's own scope — a folder-bound
curator drains its folder, not its owner's whole corpus, and a seed placed in hours the folder
never spoke for earns its edge with no model turn at all — and a compose pass is one ordinary run
under the run wall it already had, so on a slow box a fold is cut and retried rather than
continued, with no position lost either way.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased bullet is
the authoritative release note.
