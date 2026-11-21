"""Record what a curator pass cost, so the price of a character stops being shipped.

One input character's cost sizes the input slice a curator pass may be handed, and the
number that decided it was one module constant pair measured once on one box (STAS-458).
Reads of a founder's own endpoint reported costs 3-9x cheaper than that constant while
nothing read them back, so the lane stayed granted at 1,455 characters per pass behind a
backlog of 4,669,736 documents. The samples become data so `curator_calibration` can
price a route class from the passes that route class actually ran.

Rows are samples of a moving window, not an audit trail: twelve rows per key is all any
price reads, and the table stays small because a lane writes at most one row per
committed pass and the window discards what it no longer prices.

Revision ID: 0276
Revises: 0275
"""

from alembic import op

revision = "0276"
down_revision = "0275"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE curator_pass_samples (
            id bigserial PRIMARY KEY,
            calibration_key text NOT NULL,
            characters integer NOT NULL CHECK (characters > 0),
            seconds double precision NOT NULL CHECK (seconds > 0),
            seconds_per_character double precision NOT NULL,
            observed_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX curator_pass_samples_key_recent ON curator_pass_samples "
        "(calibration_key, observed_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX curator_pass_samples_key_recent")
    op.execute("DROP TABLE curator_pass_samples")
