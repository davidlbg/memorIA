"""Bind explicit reprocessing jobs to their provider/model configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("processing_identity", sa.String(64)))


def downgrade() -> None:
    op.drop_column("import_jobs", "processing_identity")
