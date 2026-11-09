"""Track all extraction inputs separately from the model's literal citations."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.create_table(
        "memory_context",
        sa.Column(
            "memory_id",
            sa.UUID(),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "message_id",
            sa.UUID(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_memory_context_revision"),
    )
    op.create_index("ix_memory_context_message_id", "memory_context", ["message_id"])
    op.execute("""
        CREATE TRIGGER serialize_memory_context BEFORE INSERT OR UPDATE OR DELETE ON memory_context
        FOR EACH ROW EXECUTE FUNCTION lock_memory_source_parent();
        CREATE FUNCTION check_memory_context_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_revision integer; current_state text;
        BEGIN
            SELECT revision, delivery_state INTO current_revision, current_state
              FROM messages WHERE id = NEW.message_id FOR SHARE;
            IF current_revision IS DISTINCT FROM NEW.revision OR current_state IS DISTINCT FROM 'active' THEN
                RAISE EXCEPTION 'context must reference an active current revision' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER validate_memory_context AFTER INSERT OR UPDATE ON memory_context
        FOR EACH ROW EXECUTE FUNCTION check_memory_context_revision();
    """)


def downgrade() -> None:
    op.drop_table("memory_context")
    op.execute("DROP FUNCTION check_memory_context_revision()")
