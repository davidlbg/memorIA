"""Durable, content-free usage accounting and shared monthly reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.create_table(
        "ai_budget_periods",
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("limit_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("prior_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("limit_micro_usd >= 0 AND (prior_micro_usd IS NULL OR prior_micro_usd >= 0)"),
    )
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("month", sa.String(7), sa.ForeignKey("ai_budget_periods.month"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(30), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=True),
        *[sa.Column(name, sa.BigInteger(), nullable=True) for name in (
            "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens"
        )],
        sa.Column("pricing_version", sa.String(30), nullable=False),
        sa.CheckConstraint("status IN ('reserved','settled','uncertain','rejected')"),
        sa.CheckConstraint("reserved_micro_usd >= 0 AND (cost_micro_usd IS NULL OR cost_micro_usd >= 0)"),
        sa.CheckConstraint("(status = 'settled' AND cost_micro_usd IS NOT NULL) OR (status != 'settled' AND cost_micro_usd IS NULL)"),
    )
    op.create_index("ix_ai_usage_month", "ai_usage", ["month"])
    op.create_index("ix_ai_usage_operation_id", "ai_usage", ["operation_id"])


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("ai_budget_periods")
