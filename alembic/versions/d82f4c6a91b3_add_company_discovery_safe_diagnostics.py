"""add company discovery safe diagnostics

Revision ID: d82f4c6a91b3
Revises: c71e3a9d4f20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d82f4c6a91b3"
down_revision: str | None = "c71e3a9d4f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "company_discovery_runs",
        sa.Column("error_subtype", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "company_discovery_runs",
        sa.Column("error_http_status", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_discovery_runs", "error_http_status")
    op.drop_column("company_discovery_runs", "error_subtype")
