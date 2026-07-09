from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company_import import CompanyIngestionItem, CompanyIngestionService
from app.modules.project.repository import ProjectRepository


def create_project(name: str = "Ingestion Project") -> int:
    with SessionLocal() as session:
        return ProjectRepository(session).create(name).id


def create_existing_company(
    project_id: int,
    *,
    name: str = "Existing Company",
    website: str | None = None,
    country: str | None = None,
    city: str | None = None,
    notes: str | None = None,
) -> int:
    with SessionLocal() as session:
        company = CompanyRepository(session).create(
            project_id=project_id,
            name=name,
            website=website,
            country=country,
            city=city,
            notes=notes,
        )
        return company.id


def assert_result_invariant(total_rows: int, imported: int, skipped: int, failed: int) -> None:
    assert total_rows == imported + skipped + failed


def test_import_one_new_company() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="Acme", website="https://acme.example")],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 0
        assert result.failed == 0
        assert result.rolled_back is False
        assert session.get(Company, result.created_company_ids[0]) is not None


def test_import_multiple_new_companies() -> None:
    project_id = create_project()
    items = [CompanyIngestionItem(name="Acme"), CompanyIngestionItem(name="Globex")]

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(project_id, items)

        assert result.imported == 2
        assert len(result.created_company_ids) == 2
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )


def test_duplicate_website_against_existing_company() -> None:
    project_id = create_project()
    existing_id = create_existing_company(project_id, website="https://www.example.com/about")

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="Duplicate", website="EXAMPLE.COM")],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 1
        assert result.duplicates[0].existing_company_id == existing_id
        assert result.duplicates[0].matched_by == "website_hostname"
        assert result.duplicates[0].matched_value == "example.com"


def test_duplicate_website_inside_batch_first_record_wins() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [
                CompanyIngestionItem(name="First", website="https://example.com"),
                CompanyIngestionItem(name="Second", website="http://www.example.com/about"),
            ],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 1
        assert result.duplicates[0].existing_company_id == result.created_company_ids[0]


def test_same_hostname_in_different_projects_is_not_duplicate() -> None:
    first_project_id = create_project("First Project")
    second_project_id = create_project("Second Project")
    create_existing_company(first_project_id, website="https://example.com")

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            second_project_id,
            [CompanyIngestionItem(name="Second Project Company", website="example.com")],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 0


def test_company_without_website_deduplicates_by_name_country_city() -> None:
    project_id = create_project()
    existing_id = create_existing_company(
        project_id,
        name="ＡＣＭＥ  GmbH",
        country=" Germany ",
        city="MÜNCHEN",
    )

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="acme gmbh", country="germany", city="münchen")],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 1
        assert result.duplicates[0].existing_company_id == existing_id
        assert result.duplicates[0].matched_by == "name_country_city"


def test_same_normalized_name_with_different_city_imports() -> None:
    project_id = create_project()
    create_existing_company(project_id, name="Acme", country="US", city="Austin")

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="ACME", country="us", city="Boston")],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 0


def test_company_with_name_only_is_not_automatically_deduplicated() -> None:
    project_id = create_project()
    create_existing_company(project_id, name="Acme")

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="ACME")],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 0


def test_duplicate_does_not_update_existing_company() -> None:
    project_id = create_project()
    existing_id = create_existing_company(
        project_id,
        name="Original Name",
        website="example.com",
        notes="Original notes",
    )

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [
                CompanyIngestionItem(
                    name="Replacement Name",
                    website="https://www.example.com/new",
                    notes="Replacement notes",
                )
            ],
        )
        existing = session.get_one(Company, existing_id)

        assert result.skipped_duplicates == 1
        assert existing.name == "Original Name"
        assert existing.notes == "Original notes"


def test_mixed_batch_with_new_duplicate_and_invalid_website() -> None:
    project_id = create_project()
    create_existing_company(project_id, website="example.com")

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [
                CompanyIngestionItem(name="New Company", website="new.example"),
                CompanyIngestionItem(name="Duplicate", website="https://www.example.com"),
                CompanyIngestionItem(name="Invalid", website="ftp://invalid.example"),
            ],
        )

        assert result.imported == 1
        assert result.skipped_duplicates == 1
        assert result.failed == 1
        assert result.errors[0].code == "invalid_website"
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )


def test_created_company_ids_preserve_input_order() -> None:
    project_id = create_project()
    names = ["First", "Second", "Third"]

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name=name) for name in names],
        )
        stored_names = [
            session.get_one(Company, company_id).name for company_id in result.created_company_ids
        ]

        assert stored_names == names


def test_nonexistent_project_returns_rolled_back_result_and_saves_nothing() -> None:
    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            999_999,
            [CompanyIngestionItem(name="Acme"), CompanyIngestionItem(name="Globex")],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 0
        assert result.failed == 2
        assert result.created_company_ids == []
        assert result.errors[0].code == "project_not_found"
        assert result.rolled_back is True
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )

    with SessionLocal() as session:
        assert CompanyRepository(session).get_all() == []


def test_invalid_existing_website_uses_fallback_without_crashing() -> None:
    project_id = create_project()
    existing_id = create_existing_company(
        project_id,
        name="Acme",
        website="ftp://invalid.example",
        country="US",
        city="Austin",
    )

    with SessionLocal() as session:
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="ACME", country="us", city="austin")],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 1
        assert result.duplicates[0].existing_company_id == existing_id
        assert result.duplicates[0].matched_by == "name_country_city"


def test_persistence_failure_rolls_back_whole_batch_and_preserves_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project()

    with SessionLocal() as session:
        original_flush = session.flush
        flush_calls = 0

        def fail_on_second_flush(objects: Sequence[Any] | None = None) -> None:
            nonlocal flush_calls
            flush_calls += 1

            if flush_calls == 2:
                raise OperationalError("forced failure", {}, RuntimeError("forced failure"))

            original_flush(objects)

        monkeypatch.setattr(session, "flush", fail_on_second_flush)
        result = CompanyIngestionService(session).ingest(
            project_id,
            [CompanyIngestionItem(name="First"), CompanyIngestionItem(name="Second")],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 0
        assert result.failed == 2
        assert result.created_company_ids == []
        assert result.errors[-1].code == "persistence_error"
        assert result.rolled_back is True
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )

    with SessionLocal() as session:
        assert CompanyRepository(session).get_by_project(project_id) == []


def test_rollback_preserves_duplicates_identified_before_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project()
    existing_id = create_existing_company(project_id, website="existing.example")

    with SessionLocal() as session:

        def fail_flush(objects: Sequence[Any] | None = None) -> None:
            raise OperationalError("forced failure", {}, RuntimeError("forced failure"))

        monkeypatch.setattr(session, "flush", fail_flush)
        result = CompanyIngestionService(session).ingest(
            project_id,
            [
                CompanyIngestionItem(name="Duplicate", website="existing.example"),
                CompanyIngestionItem(name="New Company"),
            ],
        )

        assert result.imported == 0
        assert result.skipped_duplicates == 1
        assert result.failed == 1
        assert result.duplicates[0].existing_company_id == existing_id
        assert result.rolled_back is True
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )

    with SessionLocal() as session:
        companies = CompanyRepository(session).get_by_project(project_id)
        assert [company.id for company in companies] == [existing_id]
