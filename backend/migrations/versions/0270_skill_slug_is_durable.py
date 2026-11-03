"""One durable MCP handle per published skill folder.

A skill's slug *is* its public handle: `/skills/<slug>`, the Discover entry and
`/api/v1/mcp/skills/<slug>` are all derived from it, and the MCP one gets pasted
into a coding agent's config, where it is expected to go on working. Minting it
per publish record therefore bricked every URL the owner had already handed out:
unpublish followed by republish printed a new slug, and the address someone's
agent was configured with answered "This Stash share URL is no longer shared."
forever, with nothing to restore it.

So the slug is minted once per *folder* and kept here, the way
`folder_mcp_tokens` keeps a shared folder's secret handle. Two tables, two
different questions, and that split is the whole design:

* `skill_slugs` is the **reservation** — which handle belongs to this folder. It
  outlives every publish record, so republishing re-honours the same URL.
* the `skills` row is the **publication** — whether that URL answers at all.
  Unpublishing deletes it and the endpoint 404s; publishing again revives it.

`folder_id` references `folders(id)` and never `skills(id)`, because deleting a
publish record is precisely the event that must not cascade the reservation
away. `slug` mirrors `skills.slug` — `views.slug VARCHAR(96) NOT NULL UNIQUE`,
renamed onto `skills` — so the two can never disagree on length.

The consequences are deliberate rather than incidental:

* Renaming a folder never rotates its handle, published title and all — the same
  rule the folder-token table states for a shared folder.
* A slug reserved by a folder that outlives its publication stays reserved.
  Tenant conversion (`end_user_service`) deletes publish records and keeps the
  folders, and the archive deletes in the curator-generation and
  founder-convergence migrations do the same; the reservation survives either, so
  those slugs are never reissued to another folder. Recycling a retired handle
  would re-brick the URLs already distributed for it.
* Deleting the folder is the only thing that retires the handle, through
  `ON DELETE CASCADE`. A folder later created under the same title mints its own.

This narrows one sentence in the folder-token migration
(`*_folder_mcp_tokens.py`, located by slug because its number has moved twice).
It says a slug "disappears when it is unpublished": honoration still does, the
reservation no longer does.

The backfill copies every live `skills.slug` across, so the slugs already handed
out become the durable rows and no deployed URL rotates at deploy. There is no
`ON CONFLICT`: `skills_folder_id_key` already makes `skills.folder_id` unique, so
the select cannot name one folder twice — and if it ever did, the upgrade is
supposed to fail loudly rather than keep a different handle.

Revision ID: 0270
Revises: 0269
"""

from alembic import op

revision = "0270"
down_revision = "0269"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS skill_slugs (
    folder_id UUID PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
    slug VARCHAR(96) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""")
    op.execute("INSERT INTO skill_slugs (folder_id, slug) SELECT folder_id, slug FROM skills")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS skill_slugs")
