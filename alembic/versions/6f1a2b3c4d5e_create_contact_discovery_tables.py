"""create contact discovery tables

Revision ID: 6f1a2b3c4d5e
Revises: f2c0e1a7b934
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6f1a2b3c4d5e"
down_revision: str | Sequence[str] | None = "f2c0e1a7b934"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_contact_discovery_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("discovery_status", sa.String(length=50), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discovery_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'NOT_FOUND', 'FAILED')",
            name="ck_company_contact_discovery_states_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    op.create_index(
        op.f("ix_company_contact_discovery_states_company_id"),
        "company_contact_discovery_states",
        ["company_id"],
        unique=True,
    )
    op.create_table(
        "contact_discovery_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("normalized_email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "discovery_status", sa.String(length=50), server_default="DISCOVERED", nullable=False
        ),
        sa.Column("deduplication_key", sa.String(length=1000), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_contact_discovery_candidates_confidence",
        ),
        sa.CheckConstraint(
            "source_type IN ('CONTACT_PAGE', 'ABOUT_PAGE', 'TEAM_PAGE', "
            "'LEADERSHIP_PAGE', 'STAFF_PAGE', 'OTHER_PUBLIC_PAGE')",
            name="ck_contact_discovery_candidates_source_type",
        ),
        sa.CheckConstraint(
            "discovery_status IN ('DISCOVERED', 'REVIEWED', 'PROMOTED', 'REJECTED')",
            name="ck_contact_discovery_candidates_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "deduplication_key",
            name="uq_contact_discovery_candidates_company_deduplication_key",
        ),
    )
    op.create_index(
        op.f("ix_contact_discovery_candidates_company_id"),
        "contact_discovery_candidates",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_contact_discovery_candidates_company_id"),
        table_name="contact_discovery_candidates",
    )
    op.drop_table("contact_discovery_candidates")
    op.drop_index(
        op.f("ix_company_contact_discovery_states_company_id"),
        table_name="company_contact_discovery_states",
    )
    op.drop_table("company_contact_discovery_states")
