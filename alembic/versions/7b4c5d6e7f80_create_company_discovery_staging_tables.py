"""create company discovery staging tables

Revision ID: 7b4c5d6e7f80
Revises: 6f1a2b3c4d5e
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b4c5d6e7f80"
down_revision: str | Sequence[str] | None = "6f1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_discovery_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("search_profile_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("run_status", sa.String(length=50), server_default="PENDING", nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("query_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "run_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'NOT_FOUND', 'FAILED')",
            name="ck_company_discovery_runs_status",
        ),
        sa.CheckConstraint("query_count >= 0", name="ck_company_discovery_runs_query_count"),
        sa.CheckConstraint("result_count >= 0", name="ck_company_discovery_runs_result_count"),
        sa.CheckConstraint(
            "candidate_count >= 0", name="ck_company_discovery_runs_candidate_count"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["search_profile_id"], ["search_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_company_discovery_runs_project_id"),
        "company_discovery_runs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_discovery_runs_search_profile_id"),
        "company_discovery_runs",
        ["search_profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_company_discovery_runs_project_status",
        "company_discovery_runs",
        ["project_id", "run_status"],
        unique=False,
    )
    op.create_index(
        "ix_company_discovery_runs_project_fingerprint",
        "company_discovery_runs",
        ["project_id", "request_fingerprint"],
        unique=False,
    )

    op.create_table(
        "company_discovery_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("normalized_name", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("website_identity", sa.String(length=300), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("identity_key", sa.String(length=700), nullable=False),
        sa.Column("best_position", sa.Integer(), nullable=True),
        sa.Column(
            "candidate_status",
            sa.String(length=50),
            server_default="DISCOVERED",
            nullable=False,
        ),
        sa.Column("promoted_company_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "candidate_status IN ('DISCOVERED', 'REVIEWED', 'PROMOTED', 'REJECTED')",
            name="ck_company_discovery_candidates_status",
        ),
        sa.CheckConstraint(
            "best_position IS NULL OR best_position >= 1",
            name="ck_company_discovery_candidates_best_position",
        ),
        sa.CheckConstraint(
            "country_code IS NULL OR (length(country_code) = 2 AND country_code GLOB '[A-Z][A-Z]')",
            name="ck_company_discovery_candidates_country_code",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["first_seen_run_id"], ["company_discovery_runs.id"]),
        sa.ForeignKeyConstraint(["last_seen_run_id"], ["company_discovery_runs.id"]),
        sa.ForeignKeyConstraint(["promoted_company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "identity_key",
            name="uq_company_discovery_candidates_project_identity",
        ),
    )
    for column in (
        "project_id",
        "first_seen_run_id",
        "last_seen_run_id",
        "promoted_company_id",
    ):
        op.create_index(
            op.f(f"ix_company_discovery_candidates_{column}"),
            "company_discovery_candidates",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_company_discovery_candidates_project_status",
        "company_discovery_candidates",
        ["project_id", "candidate_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_discovery_candidates_project_status",
        table_name="company_discovery_candidates",
    )
    for column in (
        "promoted_company_id",
        "last_seen_run_id",
        "first_seen_run_id",
        "project_id",
    ):
        op.drop_index(
            op.f(f"ix_company_discovery_candidates_{column}"),
            table_name="company_discovery_candidates",
        )
    op.drop_table("company_discovery_candidates")
    op.drop_index(
        "ix_company_discovery_runs_project_fingerprint", table_name="company_discovery_runs"
    )
    op.drop_index("ix_company_discovery_runs_project_status", table_name="company_discovery_runs")
    op.drop_index(
        op.f("ix_company_discovery_runs_search_profile_id"),
        table_name="company_discovery_runs",
    )
    op.drop_index(op.f("ix_company_discovery_runs_project_id"), table_name="company_discovery_runs")
    op.drop_table("company_discovery_runs")
