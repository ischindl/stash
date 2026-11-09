---
"stash": patch
---

Our trunk now contains upstream in full. `origin/main` is an ancestor of the
absorbed line, so the ~100 releases the product shipped since the 2026-09-18
split — the intuition-MCP surface, upstream's resumable wiki curation, the pi-free
release train — are ours, and the two histories stay two histories: this is one
merge commit with both sides as parents, not a rebase and not a squash.

**What users get:** whatever upstream shipped on the surfaces upstream owns. Concretely,
the founder's dogfood account was the reason this cut existed: a curation backlog bigger
than one run's budget died at the ceiling exactly where it started, and upstream fixed
that bug while we were fixing it too. They also own the curator, the wiki, the evals and
the reward models now, and their shape won there — including eleven MCP tools that ship
but were not in our integration guide (`stash_reward_function_*` ×5,
`stash_intuition_run_outcome/credit/feedback/monitoring/label_queue/improve`); the guide's
roster and its advertised tool count now match the server, 102.

**What was removed — ours, replaced by a named upstream commit:**

- The scoped-curator slicing loop (`_load_slice`, per-slice watermark advance, STAS-266
  `f2307200e`) — replaced by upstream `091a86370` (#1274, "Make wiki curation
  incremental, bounded, and resumable"): one bounded batch per run plus a durable JSONB
  cursor (`backend/services/scoped_curation_inputs.py`, migration
  `0253_curator_progress_and_budget`) and a capped catch-up chain
  (`CURATOR_CATCHUP_PASSES = 8`). Same bug, finer mechanism, different granularity.
- `curation_service.position_bound`, our tie-safe slice window — replaced by the same
  `091a86370` cursor, which records completed siblings and character offsets at the
  watermark timestamp, strictly finer than our event-position bound.
- `CURATOR_RUN_TIMEOUT_SECONDS` (STAS-266) and `run_wall_timeout()` with its derived
  session-lock TTL (STAS-364 `4e5daac47`) — replaced by upstream's single
  `AGENT_TURN_TIMEOUT_SECONDS`, which already feeds the stop-key TTL, the turn-lock TTL
  and the `asyncio.wait_for` wall. Under one-batch-per-run there is no loop to time out.
- `llm.LOCAL_COMPLETION_TIMEOUT_S`'s public rename (STAS-364) — dead with the wall it
  existed to serve; no upstream consumer.
- Our local release-version bumps (`pyproject.toml`, `uv.lock`,
  `.claude-plugin/marketplace.json` and the fifty `CHANGELOG.md` release commits that
  carried them) — discarded in favour of upstream's published numbers, because upstream
  is what PyPI serves. `uv.lock` was re-resolved with `uv lock` for the merged
  manifests, not hand-edited: the merged `pyproject.toml` declares 0.1.488 while the
  carried-over lock still recorded 0.1.461, and the parity guard exists because an
  un-relocked bump ships the previous release of the package it claims to lock.

**What survived being deleted:** the fenced compare-and-set watermark advance
(STAS-250 `34a16ac70`, `advance_watermark` raising `CuratorWatermarkConflict`). It is the
one curator guarantee upstream does not enforce — its commit block is a bare
`UPDATE agents SET curated_through=…, curation_cursor=…` — while upstream itself carries
the reset-to-never path that the fence exists to survive (the end-user opt-out in
`backend/services/end_user_service.py` nulls both columns) and states that intent in a
comment. A mid-run opt-out would have been clobbered forward silently, which is verbatim
the defect STAS-250 was written to close. Per the operator's ruling, our delta is
re-applied where it enforces something upstream does not: upstream's batch/cursor commit
shape is kept and only the forward write is made conditional on the position the run
actually read.

**What did not change:** our additive surfaces came through with zero conflicts, because
upstream grew no twin for them — `backend/routers/share_mcp.py`,
`backend/services/share_mcp_service.py`, `backend/services/share_mcp_urls.py`, the
`folder_mcp_tokens` migration (`0264_folder_mcp_tokens.py`), the fifteen
`stashai/plugin/assets/pi` blobs, and the `backend/main.py` wiring for all of it. Every
file only we had touched is byte-identical in the merged tree. The rulebook survived:
`CLAUDE.md` and its tracked `AGENTS.md` symlink are both still there. Alembic needed no
surgery at all — STAS-371 had already renumbered our local-only revisions above
upstream's ceiling, so the two chains converge at one head (`0270`) and
`backend/migrations/versions/` produced zero conflicts.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s
Unreleased bullet is the authoritative release note. This file is the removal record the
absorption owes: the per-file ledger behind every deletion above lives in the STAS-374
task documents (`absorption-decisions`, `merge-conflict-ledger`), not here.
