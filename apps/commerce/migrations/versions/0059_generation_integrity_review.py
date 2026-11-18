"""Expose scoped generation integrity hashes for recovery without private table grants."""

from pathlib import Path

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Forward-only migration; restore an isolated backup.")
