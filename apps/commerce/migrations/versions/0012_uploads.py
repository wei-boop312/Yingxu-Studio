"""Reject null fencing tokens and refunds for unresolved provider attempts."""

from pathlib import Path

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        Path(__file__)
        .with_suffix(".sql")
        .read_text(encoding="utf-8")
        .replace(":queued", r"\:queued")
    )


def downgrade():
    raise RuntimeError("Forward-only baseline. Restore an isolated backup for rollback.")
