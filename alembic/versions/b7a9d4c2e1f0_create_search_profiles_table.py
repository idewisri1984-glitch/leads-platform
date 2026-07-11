"""create search profiles table

Revision ID: b7a9d4c2e1f0
Revises: 3a41da8f399a
Create Date: 2026-07-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7a9d4c2e1f0"
down_revision: str | Sequence[str] | None = "3a41da8f399a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("product_or_service", sa.String(length=255), nullable=False),
        sa.Column("target_customer_types", sa.JSON(), nullable=False),
        sa.Column("target_industries", sa.JSON(), nullable=False),
        sa.Column("positive_keywords", sa.JSON(), nullable=False),
        sa.Column("negative_keywords", sa.JSON(), nullable=False),
        sa.Column("countries", sa.JSON(), nullable=False),
        sa.Column("cities", sa.JSON(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("query_templates", sa.JSON(), nullable=False),
        sa.Column("result_limit", sa.Integer(), server_default="10", nullable=False),
        sa.Column("max_queries_per_run", sa.Integer(), server_default="10", nullable=False),
        sa.Column("total_result_ceiling", sa.Integer(), server_default="100", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_search_profiles_project_id"), "search_profiles", ["project_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_search_profiles_project_id"), table_name="search_profiles")
    op.drop_table("search_profiles")
