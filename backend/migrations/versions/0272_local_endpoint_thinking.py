"""A local endpoint keeps the reasoning level it should be asked for.

Stash shipped the thinking level as one constant — off — so a founder whose vLLM
box eats its own output budget with reasoning had no way to bound it, and the only
fix available was a code change and a redeploy. The level becomes per-endpoint data
in the credential doc, which is the record the turn already reads to decide what to
tell `pi`, and the only place a level can be stored without inventing a second
source of truth.

Every existing endpoint gets `off`, which is precisely what it does today: `off`
adds no `--thinking` flag and no thinking field to the request, so nothing about how
a running endpoint behaves changes when this migration is applied.

The key is added INSIDE the encrypted doc, so the ciphertext of every local row
changes even though no readable value does — Fernet's IV is random per encryption,
so re-encrypting an unchanged doc cannot produce the old bytes. That is why the data
test for this migration asserts the decrypted doc and the untouched address, model
and key, and never compares ciphertext.

`thinking` is required when the doc is read (no `doc.get("thinking", "off")`
anywhere), so the backfill is what makes the new format readable at all: a row that
misses it fails loud at the next turn rather than silently reasoning at whatever
level the endpoint's own chat template feels like.

Revision ID: 0272
Revises: 0271
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0272"
down_revision = "0271"
branch_labels = None
depends_on = None

# The level Stash has always asked for, spelled the way `pi` spells it.
THINKING_OFF = "off"


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, secret_enc FROM user_agent_credentials WHERE provider = 'local'")
    ).fetchall()
    if not rows:
        # A fresh or empty database never touches the encryption keyring at all,
        # the same reason 0263's name backfill decrypts per row and not up front.
        return

    from backend.integrations.storage import _decrypt, _encrypt

    for row in rows:
        doc = json.loads(_decrypt(row.secret_enc) or "")
        if "thinking" in doc:
            continue  # already in the new format; nothing to carry forward
        doc["thinking"] = THINKING_OFF
        bind.execute(
            sa.text("UPDATE user_agent_credentials SET secret_enc = :enc WHERE id = :id"),
            {"enc": _encrypt(json.dumps(doc)), "id": row.id},
        )


def downgrade() -> None:
    # Deliberately a no-op. The code being rolled back ignores an unknown key in
    # the doc, so leaving `thinking` in place costs it nothing, while stripping it
    # would delete a level a user had chosen and re-write every local row's
    # ciphertext for a worse outcome. 0263 is one-way by arithmetic; this one is
    # one-way by kindness.
    pass
