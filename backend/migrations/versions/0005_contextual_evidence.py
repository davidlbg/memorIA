"""Track interpreted memories and exact per-message support without revising history."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("evidence_basis", sa.String(20), nullable=False, server_default="explicit"),
    )
    op.create_check_constraint(
        "ck_memory_evidence_basis", "memories", "evidence_basis IN ('explicit','interpretation')"
    )
    op.add_column(
        "memory_sources", sa.Column("evidence_quote", sa.Text(), nullable=False, server_default="")
    )
    op.create_index(
        "ix_messages_context_order", "messages", ["conversation_id", "sent_at", "sequence", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_messages_context_order", table_name="messages")
    op.drop_column("memory_sources", "evidence_quote")
    op.drop_constraint("ck_memory_evidence_basis", "memories", type_="check")
    op.drop_column("memories", "evidence_basis")
