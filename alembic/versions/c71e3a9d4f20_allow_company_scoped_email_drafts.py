"""allow company scoped email drafts

Revision ID: c71e3a9d4f20
Revises: b52cd03e8f71
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c71e3a9d4f20"
down_revision: str | None = "b52cd03e8f71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_drafts") as batch_op:
        batch_op.alter_column("contact_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM email_drafts WHERE contact_id IS NULL")):
        raise RuntimeError("Cannot downgrade while company-scoped email drafts exist.")
    with op.batch_alter_table("email_drafts") as batch_op:
        batch_op.alter_column("contact_id", existing_type=sa.Integer(), nullable=False)
