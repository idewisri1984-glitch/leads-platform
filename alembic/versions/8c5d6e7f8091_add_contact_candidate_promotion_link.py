"""add contact candidate promotion link

Revision ID: 8c5d6e7f8091
Revises: 7b4c5d6e7f80
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c5d6e7f8091"
down_revision: str | Sequence[str] | None = "7b4c5d6e7f80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "contact_discovery_candidates"
_COLUMN = "promoted_contact_id"
_FOREIGN_KEY = "fk_contact_discovery_candidates_promoted_contact_id_contacts"
_INDEX = "ix_contact_discovery_candidates_promoted_contact_id"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            _FOREIGN_KEY,
            "contacts",
            [_COLUMN],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(_INDEX, [_COLUMN], unique=False)


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_index(_INDEX)
        batch_op.drop_constraint(_FOREIGN_KEY, type_="foreignkey")
        batch_op.drop_column(_COLUMN)
