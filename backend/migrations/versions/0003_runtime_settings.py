"""Persist non-secret local provider selection.

Revision ID: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("runtime_settings")
