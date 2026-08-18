"""allow company-scoped manual send records

Revision ID: e41f7a9c2b60
Revises: d82f4c6a91b3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e41f7a9c2b60"
down_revision: str | None = "d82f4c6a91b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNGRADE_BLOCKED = "Cannot downgrade e41f7a9c2b60 while company-scoped manual send records exist."


def upgrade() -> None:
    with op.batch_alter_table("manual_email_send_records") as batch_op:
        batch_op.alter_column(
            "contact_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    company_scoped = connection.scalar(
        sa.text("SELECT COUNT(*) FROM manual_email_send_records WHERE contact_id IS NULL")
    )
    if company_scoped:
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    with op.batch_alter_table("manual_email_send_records") as batch_op:
        batch_op.alter_column(
            "contact_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
