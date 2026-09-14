"""Several local model endpoints per user: named endpoint rows + the agent's pin.

A local endpoint is a box, not a setting. The founder runs several at once
(different machines, different URLs), so the one-row-per-provider primary key
that fits an API key actively destroys his configuration: every new connection
overwrote the previous box. Endpoints therefore become independent rows — each
with a stable `id` and a user-facing `name` (defaulted to the box's hostname) —
and the one-row rule survives only as a partial unique index over the key
providers, whose connect semantics are unchanged.

`agents.credential_id` is how an agent says which box to dial. It is the ONLY
endpoint selector on the row: one pin covers every turn of the agent's run, so
both halves of a two-phase curator run dial the same server. NULL means "no
pin" and resolves to the oldest connected local endpoint — the row a single
endpoint user already has, so today's behaviour is byte-preserved.

Backfill reads every existing row once: an endpoint row is named from the
host in its own (encrypted) base_url, a key row from its provider. An empty
database therefore never touches the encryption keyring at all.

Revision ID: 0209
Revises: 0208
"""

import json
from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op

revision = "0209"
down_revision = "0208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE user_agent_credentials
        ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid(),
        ADD COLUMN name text
        """
    )
    op.execute("ALTER TABLE user_agent_credentials DROP CONSTRAINT user_agent_credentials_pkey")
    op.execute("ALTER TABLE user_agent_credentials ADD PRIMARY KEY (id)")
    # The one-row-per-provider rule keeps its exact meaning for the providers it
    # fits — the API keys, whose connect has always overwritten — and steps
    # aside for 'local', where each row is a separate box.
    op.execute(
        """
        CREATE UNIQUE INDEX one_key_credential_per_user_provider
        ON user_agent_credentials (user_id, provider)
        WHERE provider <> 'local'
        """
    )
    # Every read of this table is per user (and for 'local' ordered by age).
    op.execute(
        "CREATE INDEX user_agent_credentials_user ON user_agent_credentials (user_id, provider, created_at)"
    )
    _backfill_names()
    op.execute("ALTER TABLE user_agent_credentials ALTER COLUMN name SET NOT NULL")
    op.execute(
        "ALTER TABLE agents ADD COLUMN credential_id UUID REFERENCES user_agent_credentials(id)"
    )


def _backfill_names() -> None:
    """Name every credential row that already exists.

    A key row is named after its provider. An endpoint row is named after the
    host in its own base_url, which is the one part of the doc the founder
    recognises as "that box". The decrypt happens per row, never up front, so a
    fresh or empty database runs this without an encryption keyring configured.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, provider, kind, secret_enc FROM user_agent_credentials")
    ).fetchall()
    if not rows:
        return

    from backend.integrations.storage import _decrypt

    for row in rows:
        if row.kind == "endpoint":
            doc = json.loads(_decrypt(row.secret_enc) or "")
            host = urlparse(doc["base_url"]).hostname
            if not host:
                raise ValueError(f"credential {row.id} has a base_url with no host to name it by")
            name = host
        else:
            name = row.provider
        bind.execute(
            sa.text("UPDATE user_agent_credentials SET name = :name WHERE id = :id"),
            {"name": name, "id": row.id},
        )


def downgrade() -> None:
    # One-way by arithmetic, not by choice: the old primary key is
    # (user_id, provider), so any user who connected a second box has rows that
    # no longer fit it. Restoring it would have to delete one of his endpoints —
    # destroying exactly the data this migration exists to protect. The
    # precedent for a one-way reshape is 0118's collapse to user scope.
    raise NotImplementedError("Several local endpoints per user is one-way.")
