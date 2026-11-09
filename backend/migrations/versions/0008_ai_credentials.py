"""Encrypted provider credentials configured through the authenticated extension."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.create_table(
        "ai_configuration",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=True),
        sa.Column("processing_since", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider = 'openai'", name="ck_ai_provider"),
        sa.CheckConstraint(
            "(enabled AND encrypted_key IS NOT NULL) OR (NOT enabled AND encrypted_key IS NULL)",
            name="ck_ai_key_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_configuration")
