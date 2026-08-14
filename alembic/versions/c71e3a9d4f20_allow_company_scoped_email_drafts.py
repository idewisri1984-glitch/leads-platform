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

_BATCH_TEMP_TABLE = "_alembic_tmp_email_drafts"


def _alter_contact_id_nullability(*, nullable: bool) -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        with op.batch_alter_table("email_drafts") as batch_op:
            batch_op.alter_column(
                "contact_id",
                existing_type=sa.Integer(),
                nullable=nullable,
            )
        return

    if sa.inspect(connection).has_table(_BATCH_TEMP_TABLE):
        raise RuntimeError("Cannot migrate while an abandoned email_drafts batch table exists.")

    context = op.get_context()
    with context.autocommit_block():
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError("Could not disable SQLite foreign-key enforcement.")
        try:
            with op.batch_alter_table("email_drafts") as batch_op:
                batch_op.alter_column(
                    "contact_id",
                    existing_type=sa.Integer(),
                    nullable=nullable,
                )
        except BaseException:
            inspector = sa.inspect(connection)
            if inspector.has_table("email_drafts") and inspector.has_table(_BATCH_TEMP_TABLE):
                connection.exec_driver_sql(f'DROP TABLE "{_BATCH_TEMP_TABLE}"')
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                raise RuntimeError("Could not restore SQLite foreign-key enforcement.")

        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("SQLite foreign-key violations detected after migration.")


def upgrade() -> None:
    _alter_contact_id_nullability(nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM email_drafts WHERE contact_id IS NULL")):
        raise RuntimeError("Cannot downgrade while company-scoped email drafts exist.")
    _alter_contact_id_nullability(nullable=False)
