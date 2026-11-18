"""Pin formal archives to their private storage bucket and object version."""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE app.studio_deliveries ADD COLUMN bucket text, ADD COLUMN storage_version_id text"
    )


def downgrade():
    raise RuntimeError("Forward-only migration; restore an isolated backup.")
