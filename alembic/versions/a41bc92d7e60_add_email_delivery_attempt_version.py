"""add email delivery attempt version

Revision ID: a41bc92d7e60
Revises: 93dfda21cf4f
Create Date: 2026-08-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a41bc92d7e60"
down_revision: str | Sequence[str] | None = "93dfda21cf4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "email_delivery_attempts",
        sa.Column(
            "row_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("email_delivery_attempts", "row_version")
