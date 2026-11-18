"""Freeze confirmed appearance profile provenance with each fact version."""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE app.studio_fact_versions ADD COLUMN appearance_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade():
    raise RuntimeError("Forward-only migration; restore an isolated backup.")
