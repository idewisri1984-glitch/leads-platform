"""add contact channel foundation

Revision ID: 8c5d6e7f8091
Revises: 7b4c5d6e7f80
Create Date: 2026-07-22 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c5d6e7f8091"
down_revision: str | Sequence[str] | None = "7b4c5d6e7f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NULL_NAME_DOWNGRADE_ERROR = "F7H0 downgrade refused: contacts.first_name contains NULL values."
_INVALID_CONTACT_UPGRADE_ERROR = (
    "F7H0 upgrade refused: contacts contains a row without a meaningful name or channel."
)
_MEANINGFUL_IDENTITY_CHECK = (
    "NULLIF(TRIM(first_name), '') IS NOT NULL "
    "OR NULLIF(TRIM(last_name), '') IS NOT NULL "
    "OR NULLIF(TRIM(email), '') IS NOT NULL "
    "OR NULLIF(TRIM(phone), '') IS NOT NULL "
    "OR NULLIF(TRIM(linkedin_url), '') IS NOT NULL "
    "OR NULLIF(TRIM(instagram_url), '') IS NOT NULL"
)


def upgrade() -> None:
    connection = op.get_bind()
    invalid_contact = connection.execute(
        sa.text(
            "SELECT 1 FROM contacts "
            "WHERE NULLIF(TRIM(first_name), '') IS NULL "
            "AND NULLIF(TRIM(last_name), '') IS NULL "
            "AND NULLIF(TRIM(email), '') IS NULL "
            "AND NULLIF(TRIM(phone), '') IS NULL "
            "AND NULLIF(TRIM(linkedin_url), '') IS NULL "
            "LIMIT 1"
        )
    ).first()
    if invalid_contact is not None:
        raise RuntimeError(_INVALID_CONTACT_UPGRADE_ERROR)

    with op.batch_alter_table("contacts") as batch_op:
        batch_op.alter_column(
            "first_name",
            existing_type=sa.String(length=100),
            nullable=True,
        )
        batch_op.add_column(sa.Column("instagram_url", sa.String(length=255), nullable=True))
        batch_op.create_check_constraint(
            "ck_contacts_meaningful_identity",
            _MEANINGFUL_IDENTITY_CHECK,
        )

    with op.batch_alter_table("contact_discovery_candidates") as batch_op:
        batch_op.add_column(sa.Column("linkedin_url", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("instagram_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    null_name = connection.execute(
        sa.text("SELECT 1 FROM contacts WHERE first_name IS NULL LIMIT 1")
    ).first()
    if null_name is not None:
        raise RuntimeError(_NULL_NAME_DOWNGRADE_ERROR)

    with op.batch_alter_table("contact_discovery_candidates") as batch_op:
        batch_op.drop_column("instagram_url")
        batch_op.drop_column("linkedin_url")

    with op.batch_alter_table("contacts") as batch_op:
        batch_op.drop_constraint("ck_contacts_meaningful_identity", type_="check")
        batch_op.drop_column("instagram_url")
        batch_op.alter_column(
            "first_name",
            existing_type=sa.String(length=100),
            nullable=False,
        )
