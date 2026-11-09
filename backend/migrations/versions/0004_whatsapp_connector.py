"""Add linked-device ingestion, fencing and revision-aware work items.

Revision ID: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column("contacts", sa.Column("external_alias", sa.String(200)))
    op.add_column(
        "import_jobs", sa.Column("source_kind", sa.String(30), nullable=False, server_default="txt")
    )
    op.add_column(
        "messages", sa.Column("origin", sa.String(30), nullable=False, server_default="txt")
    )
    op.add_column(
        "messages",
        sa.Column("delivery_state", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "messages", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "messages", sa.Column("connector_metadata", JSONB(), nullable=False, server_default="{}")
    )
    stamp = sa.DateTime(timezone=True)

    def identity() -> list[sa.Column]:
        return [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", stamp, nullable=False),
        ]

    def fk(name: str, table: str, nullable: bool = False, delete: str = "CASCADE") -> sa.Column:
        return sa.Column(
            name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete=delete), nullable=nullable
        )

    op.create_table(
        "whatsapp_connections",
        *identity(),
        sa.Column("slot", sa.String(30), nullable=False, unique=True),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", stamp, nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("account_id", sa.String(64)),
        sa.Column("last_status_at", stamp, nullable=False),
        sa.Column("last_event_at", stamp),
        sa.Column("last_synced_at", stamp),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("captured_messages", sa.BigInteger(), nullable=False),
        sa.Column("duplicate_events", sa.BigInteger(), nullable=False),
        sa.Column("ignored_events", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "whatsapp_chats",
        *identity(),
        sa.Column("account_id", sa.String(64), nullable=False),
        fk("conversation_id", "conversations", True, "SET NULL"),
    )
    op.create_table(
        "whatsapp_chat_aliases",
        *identity(),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("jid", sa.String(180), nullable=False),
        fk("chat_id", "whatsapp_chats"),
        sa.UniqueConstraint("account_id", "jid"),
    )
    op.create_table(
        "whatsapp_message_states",
        *identity(),
        fk("chat_id", "whatsapp_chats"),
        sa.Column("external_message_id", sa.String(200), nullable=False),
        sa.Column("from_me", sa.Boolean(), nullable=False),
        fk("message_id", "messages", True, "SET NULL"),
        sa.Column("last_version_ms", sa.BigInteger()),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("deleted_locally", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("chat_id", "external_message_id", "from_me"),
    )
    op.create_table(
        "whatsapp_events",
        *identity(),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        fk("message_state_id", "whatsapp_message_states"),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
    )
    op.create_table(
        "message_work_items",
        *identity(),
        fk("job_id", "import_jobs"),
        fk("message_id", "messages"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_id", "message_id"),
    )
    for table, columns in {
        "whatsapp_chats": ["account_id", "conversation_id"],
        "whatsapp_chat_aliases": ["chat_id"],
        "whatsapp_message_states": ["chat_id", "message_id"],
        "whatsapp_events": ["account_id", "message_state_id", "outcome"],
        "message_work_items": ["job_id", "message_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "message_work_items",
        "whatsapp_events",
        "whatsapp_message_states",
        "whatsapp_chat_aliases",
        "whatsapp_chats",
        "whatsapp_connections",
    ):
        op.drop_table(table)
    for column in ("connector_metadata", "revision", "delivery_state", "origin"):
        op.drop_column("messages", column)
    op.drop_column("import_jobs", "source_kind")
    op.drop_column("contacts", "external_alias")
