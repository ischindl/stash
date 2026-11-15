---
"stash": patch
---

Five shipped migrations can now run against a database that already holds the
objects they create, which is the state the founder's own database is in.

**Why this exists:** the founder database carries this repository's fork work in
its schema while its `alembic_version` row sits below the revisions that made
that work, because the 2026-10-03 renumbering moved the fork's ids into the
`025x`-`026x` range. Alembic therefore believes those revisions ran, trunk's
`0214` never creates `rm_scoring_runs`, and the first revision that reaches for
it aborts the boot:
`asyncpg.exceptions.UndefinedTableError: relation "rm_scoring_runs" does not
exist`. That is the red proof that blocked the last deploy. Converging the island
means running `0214`-head over data that already contains the fork's objects, and
five nodes in that span could not survive meeting them.

**The collision list is measured, not guessed.** It came from `stamp <base>` plus
`upgrade head` against a faithful copy restored from a `pg_dump` of the founder's
database, repeated revision by revision until one run reached head with exit 0.
The nodes that broke, in the order they broke: `0257` (`column "share_wiki" of
relation "session_folders" already exists`), `0261` (`column "curator_folder_id"
of relation "agents" already exists`), `0262` (`digest_provider`), `0263`
(`column "id" of relation "user_agent_credentials" already exists`) and `0266`
(`curated_through_event_id`). `0258` was executed and survived, so it stayed out
of this change, and the reason is the pairing, not the syntax: its
`ALTER TABLE agents DROP CONSTRAINT agents_last_run_outcome_check` is unguarded,
but the constraint is created inline with its column by `0184` and the matching
`ADD CONSTRAINT` runs inside the same transaction, so the object exists at the
start of every pass and the pair is self-restoring. Guarding it anyway would be a
second code path for a state the transaction cannot produce. An earlier draft of
this paragraph explained the exemption by saying Postgres has no
`DROP CONSTRAINT IF EXISTS`; that sentence was false and is corrected here. The
form exists and this chain executes it — at `0260:49` on this very constraint name,
and in `0266`'s new guard below. `0260`, `0264`, `0265`, `0267` and the tail needed nothing —
conditional DDL, a content-idempotent `UPDATE ... WHERE finished_at IS NULL`, and
one recorded decision that `0270`'s insert stays unguarded because a duplicate
folder must fail loudly.

**One guard per node, one code path, no swallowed error.** Each node reads one
catalog fact and then does the same work or nothing; nothing is wrapped in
`try`/`except` and no value is defaulted.

- `0257`: one `ALTER TABLE session_folders ADD COLUMN IF NOT EXISTS share_wiki
  BOOLEAN NOT NULL DEFAULT false`, the type, nullability and default byte-for-byte
  what the `sa.Column` it replaces spelled.
- `0261`: both `agents` columns and `session_folders.wiki_folder_id` in
  conditional form; `DROP INDEX IF EXISTS one_curator_per_user_per_wiki`, which is
  right on both lineages because the index must be gone either way and the founder
  lineage had already dropped it with its own fork work.
- `0262`: both `agents` digest columns conditional. They are plain nullable `TEXT`
  either way.
- `0263`: the reshape is guarded on the **measured primary key**, not on a stamp.
  If the key already stands on `id`, the columns and both indexes arrive
  conditionally and `_backfill_names()` selects only `WHERE name IS NULL`, so a
  row its lineage already named is never handed to the encryption keyring — while
  a genuinely unnamed row still fails loud on a host-less `base_url`. The key
  cannot be written conditionally, because dropping it is refused outright on that
  lineage (`cannot drop constraint user_agent_credentials_pkey ... because other
  objects depend on it`, the dependent object being `agents_credential_id_fkey`),
  and the key the fork left standing is already this revision's end state.
  `ALTER COLUMN name SET NOT NULL` stays unconditional.
- `0266`: `ADD COLUMN IF NOT EXISTS`, then the pair CHECK by
  `DROP CONSTRAINT IF EXISTS` followed by `ADD CONSTRAINT` — the form `0051` and
  `0071` use — so Postgres re-validates every stored pair against the rule it is
  about to install instead of trusting what the catalog happens to hold.

**The `one_curator_per_scope` decision, and the read behind it.** This index is
dropped and re-created from `0261`'s own `CREATE UNIQUE INDEX`, not created
`IF NOT EXISTS`, because `IF NOT EXISTS` over an inherited object would let a fork
variant of unknown definition survive and call itself converged. The
`pg_get_indexdef` read on the founder database, taken read-only before the change,
is `CREATE UNIQUE INDEX one_curator_per_scope ON public.agents USING btree
(user_id, curator_wiki, COALESCE(curator_folder_id,
'00000000-0000-0000-0000-000000000000'::uuid)) WHERE is_curator` — the same
statement this file spells, so the swap is a no-op in content on that lineage and a
real unique violation on any lineage where it is not one fails loudly rather than
being inherited.

**The value-level census, from queries run in the same invocation as the proof.**
Row counts are reported separately and prove only that the copy is faithful to the
dump; they cannot see an `UPDATE`, and a revision that writes a column moves no
number in them. What the converged copy actually had written, before -> after:

- the `0265` branch: the catalog probe
  `SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()
  AND table_name = 'workspaces' AND column_name = 'curation_generation'` answered
  **1**, so `converge()` took its early return at `0265:191`. No data path ran:
  `shares` stayed 0 rows, `skills` its one row (digest `be7f0d35…` unchanged),
  `folders` its 29 (`f13a83a5…`), `pages` its 205 with `content_hash` unchanged
  (`8229df05…`), `files.end_user_id` 0 non-null, `agents.curated_through` 32
  non-null (`497500c3…`) with nothing nulled, and both queue resets had 0 rows to
  reset. No folder or page was renamed and no content hash moved, because the
  branch that does those things did not execute on this data.
- `0223`: `user_integrations` rows for provider `x` = 0 before and after (`0223:27` deletes
  them, and `downgrade()` at `0223:30-33` is a deliberate `pass`, so nothing restores them
  either), and 0 `x_saves` + `instagram_saves` sources before and after (`0223:19-24` disables
  them). The irreversible delete the operator owns has nothing to delete on this data — a
  measured 0, not an assumption of safety.
- `0268` and `0269`: sessions 3201 with the whole-table digest
  `3fa9af3b…` -> `a9b87c65…`; `finished_at IS NULL` 3200 -> 680; curator and
  scheduled-lane sessions holding a `finished_at` 0 -> 2520. So **2 520 sessions
  had their `finished_at` written** by this move. The `899` in `0269`'s docstring
  describes trunk's own database and was not reused here.
- `0272_local_endpoint_thinking` — a fourth data-bearing revision, named by this
  run and by no intake: both local credential rows were re-encrypted
  (`md5(secret_enc)` digest `526ac751…` -> `08618df6…`), which Fernet's random IV
  guarantees even where no value moved. Both documents gained exactly
  `thinking: off` and their hashes with that key removed are identical on both
  sides (`eab17ac1af5a5399`, `984b4b7715c965c3`), so no address, model or key
  changed.
- fidelity, and separately: `pages` 205, `agents` 33, `users` 28, `sessions` 3201,
  `history_events` 4 905 837, `security_audit_events` 140 567, `page_edits` 6 591,
  `folder_mcp_tokens` 0 and `user_agent_credentials` 2 identical before and after,
  as are the eleven fork-carried columns' non-null counts (`credential_id` 5,
  `curated_through_event_id` 4, `curator_folder_id` 4, `digest_model_id` 3,
  `digest_provider` 3, `model_id` 6, `share_wiki` 16 of which 11 true,
  `wiki_folder_id` 4, `user_agent_credentials.id` 2, `name` 2,
  `models_json_enc` 0). No `agents.*` or `session_folders.*` column is dropped by
  any file in this change.

**What is NOT changed here:** no database was migrated, converged, re-stamped or
cut over. The founder database was read with `SELECT`s. The island leg belongs to
the operator and to STAS-441; what landed is the guards, a test that pins both
lineages (`backend/tests/test_migration_founder_lineage_reapply.py`), and a runbook
that no longer lets a stamp be read as a position in the trunk chain.
