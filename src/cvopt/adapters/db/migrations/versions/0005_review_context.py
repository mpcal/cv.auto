"""Protect original proposal identity and reviewed target snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing proposals remain unknown: current data cannot establish past identity.
    op.add_column("proposal_item", sa.Column("review_context", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("Review history is forward-only; restore a separately managed backup.")
