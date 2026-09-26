---
"stash": patch
---

The Sessions list stops opening as a landfill of the curator's own run transcripts,
and the checkbox that used to make that optional is gone.

**What users get:** `GET /api/v1/me/sessions` with no parameters answers without a
single `agent-curate-` session, always. On the founder stack that is the difference
between a list of 1,285 live rows and one of 388 — the measured live counts, of
which the newest page shown first used to be 28 curator transcripts out of 50. The
exclusion is not a filter you can lose, forget, or fail to discover: it is the same
classification the curation feed has always enforced so the curator never feeds on
itself, now applied to the human-read list by the same code. Hidden means hidden
from the list, not deleted: a curator transcript still resolves through
`GET /me/sessions/detail`, the curator's own runs page and curator-log are untouched,
and non-curator scheduled agents (`agent-sched-`) stay visible.

**What was removed:** the `hide_curator` query parameter of the sessions list, the
`hideCurator` field and `hide_curator` mapping in the frontend API client, the
`stash_sessions_hide_curator` localStorage key and its checkbox, and the fetch gate
that existed only to wait for that stored preference before page one. Removed
outright — no alias, no ignored-but-honored flag, no migration code touching the
orphaned localStorage values people already have (the new code never reads the key;
a stale client sending the old parameter just gets the unconditional answer).

**Where the decision lives now:** one constant and one clause builder in
`backend/services/curation_service.py` (`CURATOR_SESSION_ID_PREFIX`,
`curator_run_exclusion_clause`), which the feed's eligibility constant, the scoped
feed's event scope, and the list route all splice — the first two byte-identically
to before, pinned by a guard test. `sprite_agent_service` now imports the prefix
from there when minting run ids, and the digest lane's own literal reads it too, so
the `agent-curate-` classification has exactly one producer and one definition. The
prefix moved rather than crossing the module boundary at import time:
`sprite_agent_service` already imports `curation_service` at module level (for
`Position`), so the reverse import is a cycle in both load orders, proven red before
the constant was re-homed.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s
Unreleased bullet is the authoritative release note. This file exists because the
task framework mandates a removal record for a net-negative change — the founder may
delete it.
