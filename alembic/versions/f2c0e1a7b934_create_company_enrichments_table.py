"""create company enrichments table

Revision ID: f2c0e1a7b934
Revises: b7a9d4c2e1f0
Create Date: 2026-07-13 21:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2c0e1a7b934"
down_revision: str | Sequence[str] | None = "b7a9d4c2e1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_enrichments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "enrichment_status", sa.String(length=50), server_default="PENDING", nullable=False
        ),
        sa.Column("website_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=100), nullable=True),
        sa.Column("instagram_url", sa.String(length=500), nullable=True),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("contact_page_url", sa.String(length=500), nullable=True),
        sa.Column("about_page_url", sa.String(length=500), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index(
        op.f("ix_company_enrichments_company_id"),
        "company_enrichments",
        ["company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_company_enrichments_company_id"), table_name="company_enrichments")
    op.drop_table("company_enrichments")
