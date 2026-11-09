"""Serialize source mutations so concurrent deletion cannot orphan a memory.

Revision ID: 0002
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION lock_memory_source_parent() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE memory_conversation uuid; message_conversation uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                PERFORM id FROM memories WHERE id = NEW.memory_id FOR UPDATE;
            ELSIF TG_OP = 'DELETE' THEN
                PERFORM id FROM memories WHERE id = OLD.memory_id FOR UPDATE;
            ELSE
                PERFORM id FROM memories WHERE id IN (OLD.memory_id, NEW.memory_id) ORDER BY id FOR UPDATE;
            END IF;
            IF TG_OP != 'DELETE' THEN
                SELECT conversation_id INTO memory_conversation FROM memories WHERE id = NEW.memory_id;
                SELECT conversation_id INTO message_conversation FROM messages WHERE id = NEW.message_id;
                IF memory_conversation IS DISTINCT FROM message_conversation THEN
                    RAISE EXCEPTION 'source must belong to memory conversation' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            RETURN OLD;
        END $$;
        CREATE TRIGGER serialize_memory_sources BEFORE INSERT OR UPDATE OR DELETE ON memory_sources
        FOR EACH ROW EXECUTE FUNCTION lock_memory_source_parent();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER serialize_memory_sources ON memory_sources")
    op.execute("DROP FUNCTION lock_memory_source_parent()")
