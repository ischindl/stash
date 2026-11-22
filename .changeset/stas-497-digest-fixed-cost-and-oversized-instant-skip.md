# STAS-497 — what a digest phase actually costs, and a budget that can say "no part"

The founder's personal curator lane was the subject of both findings, and neither one is what the
intake expected. The intake read the digest's 566 s against a priced window of 3 events / 12
characters and concluded that fixed prompt bytes were eating the wall. Measured on the lane after
STAS-491's priced read window and STAS-498's rename, that is no longer true, and the number that
says so is `F = -1,235.55 s`. None of what follows changes the wall, the ceiling, the credit, or
the scoped lane's price.

- **The fixed cost of a digest phase is not a cost the wall pays for.** `F` is the digest's own
  seconds minus what its feed was priced at: on the run of 2026-11-21 20:23:45 the digest phase
  answered in **42.41 s** while its feed was priced at 4,000 characters x 0.31949 s/character =
  **1,277.96 s**. The phase costs 3 % of the price, so `F` is negative and there is nothing in the
  digest's prompt, persona, or tool set left to cut: the same lane's five post-rename digest phases
  cost 50, 33, 37, 57 and 42 s on 1-3 tool calls, none of them a wiki or memory read. For the
  payload-cutting lever to exist at all, a digest phase would have to spend about 1,278 s of fixed
  bytes instead of the measured 42 s, roughly thirty times every reading taken. What the wall really
  goes to on this lane is the second phase — the wiki writer, measured at 521 s, 745 s, and more than
  887 s without an answer — which tracks the size of the wiki and is priced by nothing except a flat
  270-second reserve. Naming that is STAS-500's question, not this card's answer.
- **The next pricing question is one grep.** Each phase of a curator turn writes its own
  `curation phase=<phase> agent=<id> session=<sid> seconds=… prompt_chars=… persona_chars=…
  feed_chars=…` record, and a personal pass writes what its wall bought *before* it reads a
  character: `curation: personal window agent=… key=… rate=… source=… wall=…s priced_share=…s
  grant=… characters=… events=… until=…`. The timings are clock-independent, and a phase the wall cut
  still gets its own number, because the cut run is the run worth reading. The two sentences the
  drain fan-out brackets with (`agent schedule: curator <id> turn started|finished`) and the
  `curator pass=` line are unchanged, and `_turn_bracket` still counts exactly two of them.
- **One budget answer can now say "skip".** `_budget_kept` answered three questions with one value,
  and `None` meant both "everything fit" and "the leading timestamp group alone overflowed the
  budget", so an oversized front could never be refused and never be skipped: on this lane the
  window handed to a pass was **4,000 characters against a grant of 1,784**, 1.47 times the whole
  wall priced at the lane's own measured rate. Its replacement, `_budget_decision`, answers with
  `kept`, `read_from`, `kept_through`, and the forfeited instant, and skips **at most one** leading
  group per pass. `PersonalWindow` carries both pointers (`since` beside `until`) and refuses
  outright — returning no window at all — when the leading instant and the one behind it are both
  bigger than the grant, which is the shape that used to be a silent over-hand.
- **The skip reaches the model, not just the commit.** `_feed_window`, `build_digest_turn` and
  `build_scheduled_turn` take the pass' `since`, so the feed command both phases run opens at
  `read_from` — the forfeited instant's last row — while `curated_through` stays where it was. This
  is STAS-491's argument about `until`, applied to the other edge: a pointer that never reaches the
  words is a group the model reads again and dies on.
- **Skipping is written off loudly, because it is data left behind.** Every skip prints one WARNING,
  `curation: curator <id> forfeits unread instant <iso> (<n> events, <c> characters): it is over the
  <grant>-character budget one pass can carry, and the watermark will pass it unread`, from the read
  side exactly once per pass — so the count of those lines IS the count of lost instants. A skipped
  instant is material nobody will ever curate, and that record is the only trace it keeps. The
  alternative, storing a second read pointer on `agents`, is a data-retention decision this card is
  required to ask about first; the founder feed does not need it, because after the 4,000-character
  front there are groups of 4, 4 and 3,624 characters and a pass that skips one group still moves
  the watermark. A feed with nothing passable says so in a different sentence — `curation: curator
  <id> has no passable part: … nothing is handed out, nothing is forfeited, and the watermark stays
  where it stands` — because a lane losing history and a lane that is stuck need opposite remedies.
- **The lever the arithmetic leaves alive is the wall, and it is a dial, not a patch.** Raising this
  lane's ceiling is `PATCH /api/v1/me/curators/{id}` with `{"turn_timeout_seconds": 1499}` — 1,499 is
  the highest value the product accepts, and asking for 1,500 answers 400: "turn_timeout_seconds must
  be between 1 and 1499 seconds; 1500 is refused because the worker bounds a task at 1500 seconds".
  Measured in the running worker against a copy of the row: ceiling 900 → wall 870 s → grant 1,784
  characters; ceiling 1,499 → wall 1,469 s → grant 3,658 characters, which leaves the writer 1,427 s
  against the more-than-887 s it was measured needing; a hypothetical ceiling of 1,500 would resolve
  to wall 1,470 s and a 3,662-character grant, but it is settable nowhere, because
  `worker_turn_ceiling_seconds()` is the worker's own `task_soft_time_limit` of 1,500 s and the write
  refuses anything at or above it. **1,470 s is therefore the ceiling of what `run_wall_seconds` can
  return, and 1,469 s the highest it does return for a curator** — measured, not quoted. The same
  reading says what the dial cannot do: even at that ceiling the leading instant of 4,000 characters
  is over the 3,658-character grant, so the wall alone never makes this feed passable — the skip
  does.

**What this does not do.** It does not price the write phase: `PERSONAL_WRITE_RESERVE_SECONDS = 270`
is still a flat constant and not a deadline, and pricing the writer truthfully at the founder's
measured wiki size would refuse the personal grant at every reachable wall — that is STAS-500's
calibration question, and this card only records the measurement for it. It books no migration and
adds no column: `0277` is unbooked, and the feed ahead of the watermark has passable groups, so the
lane needs a per-pass pointer and not a stored one. It does not touch `curator_pass_samples` (a
personal pass writes nothing there and nothing was written to make a point look measured), the
drain's read order, `AGENT_TURN_TIMEOUT_SECONDS`'s default, or the credit allowance. And it does not
move the founder lane: the dial value and a forced `run_curator_now` after
`refs/heads/deploy/main` moves are operator legs.
