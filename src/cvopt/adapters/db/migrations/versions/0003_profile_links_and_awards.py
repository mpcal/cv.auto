"""LinkedIn and GitHub profile fields; awards.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profile", sa.Column("linkedin", sa.Text(), nullable=False, server_default=""))
    op.add_column("profile", sa.Column("github", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "award",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("awarder", sa.Text(), nullable=False),
        sa.Column("awarded_month", sa.String(length=7), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id"),
    )
    with op.batch_alter_table("award", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_award_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("award", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_award_user_id"))
    op.drop_table("award")
    with op.batch_alter_table("profile") as batch:
        batch.drop_column("github")
        batch.drop_column("linkedin")
