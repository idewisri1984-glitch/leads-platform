"""create email drafts table

Revision ID: 9d6e7f8091a2
Revises: 8c5d6e7f8091
Create Date: 2026-08-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9d6e7f8091a2"
down_revision: str | Sequence[str] | None = "8c5d6e7f8091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("recipient_name", sa.String(length=201), nullable=False),
        sa.Column("recipient_role", sa.String(length=150), nullable=True),
        sa.Column("sender_name", sa.String(length=150), nullable=False),
        sa.Column("sender_company", sa.String(length=200), nullable=False),
        sa.Column("generation_tone", sa.String(length=30), nullable=False),
        sa.Column("generation_purpose", sa.String(length=500), nullable=False),
        sa.Column("generation_value_proposition", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'REJECTED')",
            name="ck_email_drafts_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_fingerprint", name="uq_email_drafts_request_fingerprint"),
    )
    for column in (
        "project_id",
        "company_id",
        "contact_id",
        "lead_id",
        "task_id",
        "context_fingerprint",
        "status",
    ):
        op.create_index(f"ix_email_drafts_{column}", "email_drafts", [column], unique=False)


def downgrade() -> None:
    op.drop_table("email_drafts")
