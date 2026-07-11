from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base


class SearchProfile(Base):
    """
    Project-scoped discovery search configuration.
    """

    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    product_or_service: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    target_customer_types: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    target_industries: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    positive_keywords: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    negative_keywords: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    countries: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    cities: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    languages: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    query_templates: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    result_limit: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )

    max_queries_per_run: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )

    total_result_ceiling: Mapped[int] = mapped_column(
        Integer,
        default=100,
        nullable=False,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    project = relationship(
        "Project",
        back_populates="search_profiles",
    )
