"""initial

Revision ID: cec9c6903e42
Revises:
Create Date: 2026-07-02 04:57:22.465888

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "cec9c6903e42"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
