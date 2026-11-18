"""Preserve slot identities when changing the active suite scope."""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE app.studio_slots ADD COLUMN archived boolean NOT NULL DEFAULT false")


def downgrade():
    raise RuntimeError("Forward-only migration; restore an isolated backup.")
