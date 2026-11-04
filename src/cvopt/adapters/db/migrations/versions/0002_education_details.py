"""Preserve education descriptions, highlights and links.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("education", sa.Column("summary", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "education", sa.Column("highlights", sa.Text(), nullable=False, server_default="[]")
    )
    op.add_column("education", sa.Column("links", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("education") as batch:
        batch.drop_column("links")
        batch.drop_column("highlights")
        batch.drop_column("summary")
