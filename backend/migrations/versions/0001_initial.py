"""Initial PostgreSQL schema, immutable once released.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None


def upgrade() -> None:
    # Dimension is a deployment-time schema parameter. Existing columns never
    # change when an environment variable changes; startup checks detect drift.
    from app.config import get_settings

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    dim = get_settings().embedding_dimensions

    def identity() -> list[sa.Column]:
        return [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        ]

    def fk(name: str, table: str, nullable: bool = False, delete: str = "CASCADE") -> sa.Column:
        return sa.Column(
            name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete=delete), nullable=nullable
        )

    stamp = sa.DateTime(timezone=True)
    op.create_table(
        "conversations",
        *identity(),
        sa.Column("external_id", sa.String(128), unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("conversation_type", sa.String(16), nullable=False),
        sa.Column("updated_at", stamp, nullable=False),
        sa.Column("imported_at", stamp, nullable=False),
        sa.CheckConstraint("conversation_type IN ('direct','group','unknown')"),
    )
    op.create_table(
        "contacts",
        *identity(),
        fk("conversation_id", "conversations"),
        sa.Column("display_name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("external_identifier", sa.String(200)),
        sa.Column("updated_at", stamp, nullable=False),
        sa.UniqueConstraint("conversation_id", "normalized_name"),
    )
    op.create_table(
        "import_jobs",
        *identity(),
        fk("conversation_id", "conversations"),
        sa.Column("filename", sa.String(250), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_messages", sa.Integer(), nullable=False),
        sa.Column("imported_messages", sa.Integer(), nullable=False),
        sa.Column("processed_messages", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(500)),
        sa.Column("completed_at", stamp),
        sa.CheckConstraint("status IN ('pending','processing','completed','failed')"),
    )
    op.create_table(
        "messages",
        *identity(),
        fk("conversation_id", "conversations"),
        fk("sender_contact_id", "contacts", True, "SET NULL"),
        sa.Column("sender_name_snapshot", sa.String(500)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(30), nullable=False),
        sa.Column("sent_at", stamp, nullable=False),
        sa.Column("external_message_id", sa.String(200)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        fk("source_import_id", "import_jobs"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.UniqueConstraint("conversation_id", "content_hash"),
    )
    op.create_table(
        "memories",
        *identity(),
        fk("conversation_id", "conversations"),
        sa.Column("memory_type", sa.String(20), nullable=False),
        fk("subject_contact_id", "contacts", True, "SET NULL"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("observed_at", stamp, nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("updated_at", stamp, nullable=False),
        sa.UniqueConstraint("conversation_id", "fingerprint"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1"),
        sa.CheckConstraint("memory_type IN ('fact','event','intention','commitment')"),
        sa.CheckConstraint("status IN ('active','outdated','disputed','deleted')"),
    )
    op.create_table(
        "memory_sources",
        *identity(),
        fk("memory_id", "memories"),
        fk("message_id", "messages"),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.UniqueConstraint("memory_id", "message_id"),
    )
    for kind in ("message", "memory"):
        table = f"{kind}_embeddings"
        op.create_table(
            table,
            *identity(),
            fk(f"{kind}_id", "messages" if kind == "message" else "memories"),
            sa.Column("embedding", Vector(dim), nullable=False),
            sa.Column("model_name", sa.String(200), nullable=False),
            sa.UniqueConstraint(f"{kind}_id", "model_name"),
        )
        op.create_index(f"ix_{table}_{kind}_id", table, [f"{kind}_id"])
    for table, columns in {
        "contacts": ["conversation_id"],
        "import_jobs": ["conversation_id", "status"],
        "messages": [
            "conversation_id",
            "sender_contact_id",
            "sent_at",
            "content_hash",
            "source_import_id",
        ],
        "memories": ["conversation_id", "subject_contact_id", "memory_type", "observed_at"],
        "memory_sources": ["memory_id", "message_id"],
    }.items():
        for col in columns:
            op.create_index(f"ix_{table}_{col}", table, [col])
    for table in ("messages", "memories"):
        op.execute(
            f"CREATE INDEX ix_{table}_fts ON {table} USING gin (to_tsvector('simple', content))"
        )
    op.execute("""
        CREATE FUNCTION require_memory_source() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid;
        BEGIN
            IF TG_TABLE_NAME = 'memories' THEN target := NEW.id;
            ELSE target := OLD.memory_id; END IF;
            IF EXISTS (SELECT 1 FROM memories WHERE id = target)
               AND NOT EXISTS (SELECT 1 FROM memory_sources WHERE memory_id = target) THEN
                RAISE EXCEPTION 'memory requires at least one source' USING ERRCODE='23514';
            END IF;
            RETURN NULL;
        END $$;
        CREATE CONSTRAINT TRIGGER memory_has_source AFTER INSERT OR UPDATE ON memories
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_memory_source();
        CREATE CONSTRAINT TRIGGER source_not_orphaned AFTER DELETE OR UPDATE ON memory_sources
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_memory_source();
    """)


def downgrade() -> None:
    for table in (
        "memory_embeddings",
        "message_embeddings",
        "memory_sources",
        "memories",
        "messages",
        "import_jobs",
        "contacts",
        "conversations",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION require_memory_source()")
