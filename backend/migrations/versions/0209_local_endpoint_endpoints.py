"""absorbed: founder-build `0209` (local_endpoint_endpoints)

A no-op node, not a migration. The founder (dogfood) image shipped its own
re-numbered chain `0203`-`0209`, which are trunk's `0210`-`0216 under new ids —
this file is that build's `0209`, whose content trunk already ships as
``0216_local_endpoint_endpoints``. It exists so a database left stamped `0209`
can resolve its stamp at all: without a node named `0209`, every boot of this
chain died in ``alembic upgrade head`` with
`Can't locate revision identified by '0209'` (the crash STAS-232 fixes), and
`alembic stamp` could not be used as a workaround because stamping runs a full
migration-script load that fails the same way.

Registered as an absorbed node in `shipped_revisions.json` -> `absorbed`, which
is the only way an id listed under `stamp_only` may ever gain a file. The node
executes nothing because the founder build already ran this revision's content
under its own number — the schema is present, only the name was missing.

The migrations trunk `0203`-`0207` owe such a database are carried by
`0218_converge_dogfood_lineage`.

It parents onto trunk's `0217` rather than `0216` because trunk booked `0217`
(`folder_mcp_tokens`) while this node was in flight: two nodes off `0216` make
two heads, and a founder database would have had no path to `head` at all. The
cost of that placement is stated where it is paid — alembic reads an ancestor as
applied, so a database stamped `0209` never runs `0217_folder_mcp_tokens`, and
`0218_converge_dogfood_lineage` creates that table on its behalf.

Revision ID: 0209
Revises: 0217
"""

revision = "0209"
down_revision = "0217"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No schema work: the founder build that stamped this id ran it already."""


def downgrade() -> None:
    """Nothing to undo — this node never changed anything."""
