"""Task, quote, immutable ledger and persistent dispatch resources."""

from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
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
