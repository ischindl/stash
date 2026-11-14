---
"stash": patch
---

joinstash.ai no longer documents an API the product does not serve. The whole
`/docs` route tree was the retired reward-model product, and every `curl` example
on it returned 404.

**The failure this fixes:** `www/app/docs` was seven sitemap-indexed pages whose
subject was a reward-model app that is gone. `docs/api` called itself the "REST
reference for /api/v1/rm" and listed only `/formats`, `/traces*`, `/annotations*`,
`/export/*`, `/reward-models*`, `/gepa-runs*`, `/query`; `docs/page.tsx` advertised
`OTEL_EXPORTER_OTLP_ENDPOINT=https://api.joinstash.ai/api/v1/rm/otel`. No router in
`backend/routers/` carries an `/api/v1/rm` prefix, there is no otel route, the trace
annotation API does not exist, and the import adapters those pages pin their examples
to (`backend/services/rm/adapters.py`) have no tracked file. Measured at
`refs/heads/main`: `git grep -nE '"/(gepa|reward|training|workbench)' -- backend/`
exits 1 with empty stdout and empty stderr. A paying user following the public docs
was walked into 404s, and search engines had been handed the whole tree.

**What was removed:** the routed tree `www/app/docs/` (overview, trace-format,
annotations, implementation, training, gepa, api, plus the docs-only `components.tsx`,
`parts.tsx`, `pipeline.tsx`, `examples.ts`); its four demo recordings under
`www/public/docs/demo/`; the seven `"/docs..."` rows in `www/app/sitemap.ts`; the
`Docs` nav item in `www/app/discover/page.tsx` and in `www/app/_components/LegalShell.tsx`;
the two `/reward-models*` redirects in `www/next.config.ts`, which forwarded visitors
onto a frontend route that does not exist; `README.md`'s `Read the docs` link and
`sdk/README.md`'s `The full API reference lives at joinstash.ai/docs` sentence (that
file is the published `stash-sdk` PyPI readme, so the false claim shipped to PyPI);
and the four prose clauses in `www/README.md`, `www/vitest.config.ts`, `www/DESIGN.md`
and `www/app/_components/SiteHeader.tsx` that described the deleted tree. Removed
outright: no redirect, no rewrite, no placeholder route, no "this page moved" copy.

**Also removed:** `_reconcile_trace_segments` from `backend/tasks/embeddings.py` and its
entry in the `_reconcile()` tuple. It ran inside the live 60-second `embedding-reconcile`
beat sweep and SELECTed/UPDATEd `rm_trace_segments`, a table whose only other reference to
that exact name in tracked code is its own DDL in
`backend/migrations/versions/0244_behaviors.py` (the wider `rm_*` scoring family lives on in
five migration files and `shipped_revisions.json`). No writer survives, so the sweep paid
one guaranteed-empty query per minute. The five live reconcilers (`pages`, `table_rows`,
`history_events`, `files`, `source_documents`) and the `embedding-reconcile` beat entry
in `backend/celery_app.py` are untouched.

**What users get:** a public site that claims only what the product does, and one
deterministic guard so it cannot drift again.
`backend/tests/test_documented_api_routes.py` reads the FastAPI route table from
`backend.main.app` and every `.ts`/`.tsx` file under `www/app`, and fails naming
`file:line` for any `/api/v1/...` path the server does not route, for any reappearance
of `/api/v1/rm/`, `/docs/gepa`, `/docs/training` or `/reward-models` on the public site,
and for a `www` tree it cannot actually read. No network, no LLM, no skip, no warning mode.

**What is deliberately NOT removed:** the `rm_*` table family and
`users.reward_models_enabled` stay in the schema exactly as they are. Their retention is
the operator's decision after STAS-440 (founder-database convergence, which is still
carrying the first 29 `rm_*` tables) and STAS-441 (the deploy that follows it), not a
side effect of tidying documentation. Two known absences are reported rather than papered
over: there is no public CLI Reference page any more (its coverage guard in `www/test/`
pinned `www/app/docs/cli/page.tsx`, a file that already did not exist, and died at import
before this card), and the product's live SQL capability is at `/api/v1/me`
(`backend/routers/sql.py`), a prefix the deleted reference never named — so a truthful
public API reference is a writing card of its own, not a rescue of this content.

**Note:** as STAS-302 recorded, this repo has no tooling that consumes `.changeset/`;
`CHANGELOG.md`'s Unreleased bullet is the authoritative release note, and this card's
Do-NOT list leaves `CHANGELOG.md` alone. This file exists because the task framework
mandates a removal record for a net-negative change — the founder may delete it.
