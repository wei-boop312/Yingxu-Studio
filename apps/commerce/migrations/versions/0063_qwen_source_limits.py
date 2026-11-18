"""Validate actual canonical source bounds before Qwen quotes or tasks exist."""

from pathlib import Path

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Forward-only migration; restore an isolated backup.")
