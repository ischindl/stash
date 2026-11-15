"""A local endpoint keeps the context window it was configured with.

Every turn Stash ran against a local model server reported that box as a 131072-token
model asking for 8192 output tokens, whatever the box actually was. The number lived in
`_synthesized_models_json`, one pair for every model server on earth: a founder running
gpt-oss on an 8 GB card through LM Studio at 4096 was told he had a 131072-token model and
was handed a request the box would reject, while a founder running a 150000-token vLLM box
was told the same thing and could not raise his output ceiling. The window becomes
per-endpoint data in the credential doc, alongside the address, model and reasoning level,
which is the record a turn already reads.

A new endpoint has its window from the moment it is connected. This migration carries the
rows that were connected before: the number stamped is the one that row's own effective
models.json already declared, so nobody's traffic changes on upgrade. A row whose owner had
saved a verbatim models.json override gets the `contextWindow` from inside that file, which
is what his turns were actually told, and it differs row by row. It is read for the model that
row's credential doc names, not for whichever model the file lists first: one override can
carry several models at several sizes, and only one of those entries describes this row.

The key is added INSIDE the encrypted doc, so the ciphertext of every local row changes even
where no readable value does -- Fernet's IV is random per encryption, so re-encrypting an
unchanged doc cannot reproduce the old bytes. That is why this migration's data test asserts
the decrypted doc and the untouched address, model and key, and never compares ciphertext to
a remembered value.

`context_window` is required when the doc is read -- no `doc.get("context_window", ...)`
anywhere -- so the backfill is what makes the new format readable at all. A row it misses
fails loud at its next turn instead of running on a size nobody chose, which is the failure
this card exists to retire.

The two numbers below, and where each one comes from.

`WINDOW_FLOOR_TOKENS` is the floor that `agent_auth.MIN_CONTEXT_WINDOW_TOKENS` enforces on
every connect and every edit, copied here instead of imported: this module runs at boot, at a
point where the service it would import may not be importable at all, and a migration that
opens a keyring it does not need is a migration that can fail in a database it should have
skipped. The cost is two copies, and two copies can drift. If the floor rises here and falls
in the code, a window connect accepts is refused here -- a loud, one-off upgrade failure that
names its row. If the floor falls here and rises in the code, a window connect refuses gets
stamped, and the ceiling derived from it comes out zero or negative. That asymmetry is why the
comparison below refuses instead of clamping: the loud direction is the safe one.

`DEFAULT_SYNTHESIZED_WINDOW_TOKENS` is a history record, not a copy: it is what Stash itself
claimed about every local box until this card, and it is only ever the answer for a row that
has no number of its own to be read. It is cited the way the constants in `agent_auth` cite
their provenance -- by card and document key, never by a file path a renumber would invalidate
-- and the card that changes the rule changes this line too.


Revision ID: 0275
Revises: 0274
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0275"
down_revision = "0274"
branch_labels = None
depends_on = None

# The smallest window that can hold a conversation, and the turn's answer room inside it.
# Stamped windows below this one would derive a ceiling of zero or less, which no model
# server accepts. Kept in step with agent_auth.MIN_CONTEXT_WINDOW_TOKENS -- see above.
WINDOW_FLOOR_TOKENS = 2048
# What Stash asserted about every local endpoint before this card, for the rows that are
# still on the synthesized default and therefore have no number of their own to be read.
DEFAULT_SYNTHESIZED_WINDOW_TOKENS = 131072


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, secret_enc, models_json_enc FROM user_agent_credentials "
            "WHERE provider = 'local'"
        )
    ).fetchall()
    if not rows:
        # A fresh or empty database never touches the encryption keyring at all, the same
        # reason 0263's name backfill decrypts per row rather than up front.
        return

    from backend.integrations.storage import _decrypt, _encrypt

    for row in rows:
        doc = json.loads(_decrypt(row.secret_enc))
        if "context_window" in doc:
            # Already in the new format. This is not an impossible state: a saved override
            # is reset by DELETING the override, which re-synthesizes the doc from the
            # stored values, and a reset done after the new code shipped writes a doc that
            # already carries the key. Re-stamping such a row would overwrite a window its
            # owner had already set, so the row is left exactly as it is.
            continue

        window = _declared_window(row, doc)
        if window < WINDOW_FLOOR_TOKENS:
            # No clamp and no default: a box whose window is below the floor is a mistyped
            # field rather than a small box, and fixing it on the user's behalf would be
            # inventing the number this card refuses to invent.
            raise ValueError(
                f"local endpoint {row.id} declares a context window of {window} tokens, "
                f"below the {WINDOW_FLOOR_TOKENS}-token floor. Correct that row's models.json "
                "override (or reset it) and run the upgrade again."
            )
        doc["context_window"] = window
        bind.execute(
            sa.text("UPDATE user_agent_credentials SET secret_enc = :enc WHERE id = :id"),
            {"enc": _encrypt(json.dumps(doc)), "id": row.id},
        )


def _declared_window(row, doc: dict) -> int:
    """The window this row's own effective models.json already declared for this row's model.

    A row on the synthesized default is asked nothing: it had no number of its own, so it
    keeps the one its turns were told. A row with a saved override is READ -- the override
    is the file its turns actually wrote, and the number inside it is his, so it is carried
    across unchanged. It is looked up by the model the credential doc names, because an
    override may list several models at different sizes and the first entry is not this row's
    box: stamping it would hand the owner a size his turns were never told. Anything that
    cannot be read is a refusal naming the row: the row's model name is the only thing a file
    whose shape is unknown can be identified by, and guessing a window for it would put a
    made-up number on the wire with his key on it.
    """
    if row.models_json_enc is None:
        return DEFAULT_SYNTHESIZED_WINDOW_TOKENS

    from backend.integrations.storage import _decrypt

    model = doc.get("model")
    try:
        models = json.loads(_decrypt(row.models_json_enc))["providers"]["local"]["models"]
        declared = {entry["id"]: entry["contextWindow"] for entry in models}[model]
    except (ValueError, TypeError, LookupError) as exc:
        raise ValueError(
            f"local endpoint {row.id} (model {model!r}) stores a models.json override with no "
            f"readable providers.local.models[].contextWindow for that model: {exc}. "
            "Fix that file or reset the override, then run the upgrade again."
        ) from exc
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise ValueError(
            f"local endpoint {row.id} (model {doc.get('model')!r}) stores a context window "
            f"that is not a whole number of tokens: {declared!r}."
        )
    return declared


def downgrade() -> None:
    # Deliberately a no-op. The code being rolled back ignores an unknown key in the doc, so
    # leaving `context_window` in place costs it nothing, while stripping it would throw away
    # the size the endpoint's owner stated for his box -- and re-write every local row's
    # ciphertext for a worse outcome. 0263 is one-way by arithmetic; this is one-way by
    # kindness, the same way 0272's downgrade is.
    pass
