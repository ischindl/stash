---
"stash": patch
---

Report a history drain's own position in every curator readout, and name the instant a backlog was counted from.

**The problem this closes.** A history drain can take a month, and for all of that month the
curator console said the lane had nothing new to read. Two positions live on one `agents` row and
exactly one of them is live at a time: `curated_through` is the monotone incremental watermark, and
`drain_through` beside `drain_cursor` is where a `full_history` backfill resumes, moved at every
committed pass. The run path already asked which of the two was live before it read anything
(`curation_service.read_position_of`, `curation_service.drain_of`). The human path never asked:
measured on trunk `2161d11f5`, `backend/routers/developer.py` counted the backlog with
`curation_service.position_of(curator)`, `backend/routers/files_tree.py` did the same for the
folder-curator rows and for the `POST /me/memory/recompute` gate, and the credits-spent clause of
the stale-curators alert printed its "N distinct events pending" from the watermark too. A drain's
backlog sits *behind* the watermark by design and `finish_drain` hands the watermark over only when
the drain retires, so every one of those four readers named a number that could not move while the
drain's committed passes moved invisibly — and the recompute button answered "Nothing new to curate
since the last run." at the one lane that was starving for exactly the material its drain was
reading.

**What changed.** `curation_service.readout_position_of(agent)` is now the only place in `backend/`
that answers "which position is live, as a `Position`", and `curation_service.curator_readout` is
the one readout that counts from it and returns the backlog untouched plus `read_from` (the instant
it counted from) and `draining` (whose instant that was). All four readers moved onto the predicate;
`_pending_input`'s inline drain-or-watermark expression became one call to it, behavior unchanged;
and `GET /me/changes` stayed caller-relative on purpose, because `drain_pass_budget` recognises a
drain pass by comparing the request's position to the row's stored pair. `curation_service.py` grew
no new body for the counting: `curator_event_backlog` is byte-identical and is asked at a different
position. After this, `git grep -n "curation_service.position_of(" -- backend/routers` prints
nothing.

**The bound this carries: a drain pair has no event half.** There is no `drain_through_event_id`
column, so during a drain the readout is the whole instant — a lane standing inside an instant that
holds more events than one pass can carry is reported as reading from *before* that instant, not as
though the tie it stands in had been consumed. That is the conservative direction on purpose: the
other direction is a backlog that reads zero while a tie's tail is still unread. The second bound is
the seed. A drain is seeded by its absence (`drain_through IS NULL AND drain_cursor = '{}'`), so
between a dispatch and its first committed pass nothing stored separates a seeded drain from no
drain, and the readout names the watermark for both. A marker column to tell them apart would be a
second position counter, which is the thing this card exists to make impossible.

**What users see.** The curator console's third card reads "Draining through" while a backfill is
in flight, shows the instant its own unread figure was counted from, and says the nightly watermark
stays where it is until the drain retires; with no drain open that card is word for word what it was
before. A project curator's row in Settings says "N unread events during a history drain" instead of
naked "N unread events". "Curate now" no longer refuses a lane whose backlog is entirely behind its
watermark, and a curator frozen by a spent monthly allowance is reported to Slack with the remainder
its drain still owes and which position that remainder was counted from.

**What did not change:** no endpoint, no router, no migration, no column, no new table. No second
counter and no client-side choice between two instants — `read_from` and `draining` are required on
both TypeScript backlog shapes rather than optional, because an absent field would have to be
defaulted somewhere and a default over a live position is the bug class this removes.
`curated_through` keeps its meaning and every writer of it: `advance_drain`, `advance_watermark`,
`finish_drain`, and the ingest-rewind clamp are untouched, and `GET /me/changes` answers from the
position its caller named exactly as before.

**What was measured, and what was not.** The five readers, the four run-path anchors that stay on
`position_of`, and the census above were read off trunk at `2161d11f5` and are re-run in
`docs/testing.md`'s own voice. The claim that the unread figure falls per committed pass is pinned by
a test that commits two real passes through `agent_service.advance_drain` and asserts the same
helper's count strictly falls while the watermark is byte-identical. The leg of that claim that was
supposed to be taken on a production copy could not be taken, and is not claimed here: of the two
copies on this host, `stash3995` runs `main-39cb4595`, whose `agents` table carries no `drain_through`
column at all, and `stashdemo` runs `main-c14c5458` with `drain_through` and `drain_cursor` present
but zero rows carrying an open drain (0 with `drain_through IS NOT NULL`, 0 with a non-`{}`
`drain_cursor`, `max(curated_through)` = 2026-11-01) and no `curator pass=` meter line anywhere in the
logs of its three app containers — so the copy holds no two real positions of one lane to measure.
The operator harness for dispatching a real pass is a host path outside that container's only mount
(the secrets volume), so its `preflight` was never reached; the artifact forbids improvising a
dispatch, and writing `drain_through` onto a production copy to manufacture progress is forbidden
too. That measurement is the operator's leg: it needs a lane with an open drain.
