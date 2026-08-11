"""add manual outreach tracking

Revision ID: b52cd03e8f71
Revises: a41bc92d7e60
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b52cd03e8f71"
down_revision: str | None = "a41bc92d7e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_drafts") as batch_op:
        batch_op.add_column(sa.Column("delivery_mode", sa.String(length=20), nullable=True))
        batch_op.create_check_constraint(
            "ck_email_drafts_delivery_mode",
            "delivery_mode IS NULL OR delivery_mode IN ('MANUAL', 'AUTOMATIC')",
        )
        batch_op.create_index("ix_email_drafts_delivery_mode", ["delivery_mode"])

    op.execute(
        "UPDATE email_drafts SET delivery_mode = 'AUTOMATIC' "
        "WHERE EXISTS (SELECT 1 FROM email_delivery_attempts "
        "WHERE email_delivery_attempts.email_draft_id = email_drafts.id)"
    )
    op.create_table(
        "manual_email_send_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("email_draft_id", sa.Integer(), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_manual_email_send_records_project_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_manual_email_send_records_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            name="fk_manual_email_send_records_contact_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["email_draft_id"],
            ["email_drafts.id"],
            name="fk_manual_email_send_records_email_draft_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manual_email_send_records"),
        sa.UniqueConstraint("email_draft_id", name="uq_manual_email_send_records_email_draft_id"),
    )
    op.create_index(
        "ix_manual_email_send_records_project_id",
        "manual_email_send_records",
        ["project_id"],
    )
    op.create_index(
        "ix_manual_email_send_records_company_id",
        "manual_email_send_records",
        ["company_id"],
    )
    op.create_index(
        "ix_manual_email_send_records_contact_id",
        "manual_email_send_records",
        ["contact_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_manual_email_send_records_contact_id",
        table_name="manual_email_send_records",
    )
    op.drop_index(
        "ix_manual_email_send_records_company_id",
        table_name="manual_email_send_records",
    )
    op.drop_index(
        "ix_manual_email_send_records_project_id",
        table_name="manual_email_send_records",
    )
    op.drop_table("manual_email_send_records")
    with op.batch_alter_table("email_drafts") as batch_op:
        batch_op.drop_index("ix_email_drafts_delivery_mode")
        batch_op.drop_constraint("ck_email_drafts_delivery_mode", type_="check")
        batch_op.drop_column("delivery_mode")
