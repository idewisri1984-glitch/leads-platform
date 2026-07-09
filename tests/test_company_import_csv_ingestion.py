from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.company_import import (
    CompanyImportRow,
    CompanyIngestionItem,
    CompanyIngestionService,
    company_import_row_to_ingestion_item,
    ingest_company_csv,
)
from app.modules.project.repository import ProjectRepository


def write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "companies.csv"
    path.write_text(content, encoding="utf-8")
    return path


def create_project() -> int:
    with SessionLocal() as session:
        return ProjectRepository(session).create("CSV Ingestion Project").id


def assert_result_invariant(total_rows: int, imported: int, skipped: int, failed: int) -> None:
    assert total_rows == imported + skipped + failed


def test_company_import_row_maps_to_ingestion_item() -> None:
    row = CompanyImportRow(
        row_number=7,
        name="Acme",
        website="https://acme.example",
        country="US",
        city="Austin",
        industry="Software",
        status="ACTIVE",
        notes="Priority account",
    )

    item = company_import_row_to_ingestion_item(row)

    assert item == CompanyIngestionItem(
        source_row_number=7,
        name="Acme",
        website="https://acme.example",
        country="US",
        city="Austin",
        industry="Software",
        status="ACTIVE",
        notes="Priority account",
    )


def test_valid_csv_persists_companies(tmp_path: Path) -> None:
    project_id = create_project()
    path = write_csv(tmp_path, "name,website\nAcme,acme.example\nGlobex,globex.example\n")

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.imported == 2
        assert result.failed == 0
        assert len(result.created_company_ids) == 2
        assert [
            session.get_one(Company, company_id).name for company_id in result.created_company_ids
        ] == ["Acme", "Globex"]


def test_valid_csv_with_parser_error_imports_valid_rows_and_preserves_error(
    tmp_path: Path,
) -> None:
    project_id = create_project()
    path = write_csv(
        tmp_path,
        "name,website\nAcme,acme.example\n,missing-name.example\nGlobex,globex.example\n",
    )

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.total_rows == 3
        assert result.imported == 2
        assert result.failed == 1
        assert result.errors[0].source_row_number == 3
        assert result.errors[0].code == "csv_validation_error"
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )


def test_duplicate_csv_company_is_skipped(tmp_path: Path) -> None:
    project_id = create_project()

    with SessionLocal() as session:
        existing = CompanyRepository(session).create(
            project_id=project_id,
            name="Existing",
            website="https://www.example.com/about",
        )

    path = write_csv(tmp_path, "name,website\nDuplicate,EXAMPLE.COM\n")

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.imported == 0
        assert result.skipped_duplicates == 1
        assert result.duplicates[0].existing_company_id == existing.id


def test_invalid_website_becomes_ingestion_error(tmp_path: Path) -> None:
    project_id = create_project()
    path = write_csv(tmp_path, "name,website\nInvalid,ftp://invalid.example\n")

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.imported == 0
        assert result.failed == 1
        assert result.errors[0].source_row_number == 2
        assert result.errors[0].code == "invalid_website"


def test_missing_name_column_returns_failed_result_without_persistence(tmp_path: Path) -> None:
    project_id = create_project()
    path = write_csv(tmp_path, "website,country\nexample.com,US\n")

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.total_rows == 1
        assert result.imported == 0
        assert result.failed == 1
        assert result.errors[0].source_row_number is None
        assert result.errors[0].code == "csv_validation_error"
        assert result.rolled_back is False
        assert CompanyRepository(session).get_by_project(project_id) == []


def test_empty_csv_returns_parser_error_without_persistence(tmp_path: Path) -> None:
    project_id = create_project()
    path = write_csv(tmp_path, "")

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)

        assert result.total_rows == 1
        assert result.failed == 1
        assert result.errors[0].code == "csv_validation_error"
        assert CompanyRepository(session).get_by_project(project_id) == []


def test_csv_with_no_valid_rows_does_not_call_ingestion_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_csv(tmp_path, "name,website\n,missing-name.example\n")

    def unexpected_ingest(
        self: CompanyIngestionService,
        project_id: int,
        items: list[CompanyIngestionItem],
    ) -> None:
        raise AssertionError("Ingestion service must not be called without valid rows.")

    monkeypatch.setattr(CompanyIngestionService, "ingest", unexpected_ingest)

    with SessionLocal() as session:
        result = ingest_company_csv(session, 999_999, path)

        assert result.total_rows == 1
        assert result.failed == 1
        assert result.rolled_back is False


def test_nonexistent_project_preserves_parser_errors_and_rolls_back(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "name,website\n,missing.example\nAcme,acme.example\n")

    with SessionLocal() as session:
        result = ingest_company_csv(session, 999_999, path)

        assert result.total_rows == 2
        assert result.imported == 0
        assert result.failed == 2
        assert result.created_company_ids == []
        assert [error.code for error in result.errors] == [
            "csv_validation_error",
            "project_not_found",
        ]
        assert result.rolled_back is True


def test_parser_errors_are_preserved_when_ingestion_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project()
    path = write_csv(
        tmp_path,
        "name,website\n,missing.example\nFirst,first.example\nSecond,second.example\n",
    )

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
        result = ingest_company_csv(session, project_id, path)

        assert result.total_rows == 3
        assert result.imported == 0
        assert result.skipped_duplicates == 0
        assert result.failed == 3
        assert result.created_company_ids == []
        assert [error.code for error in result.errors] == [
            "csv_validation_error",
            "persistence_error",
        ]
        assert result.rolled_back is True
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )

    with SessionLocal() as session:
        assert CompanyRepository(session).get_by_project(project_id) == []


def test_mixed_result_counter_invariant_and_created_id_order(tmp_path: Path) -> None:
    project_id = create_project()

    with SessionLocal() as session:
        CompanyRepository(session).create(
            project_id=project_id,
            name="Existing",
            website="existing.example",
        )

    path = write_csv(
        tmp_path,
        "name,website\n"
        "First,first.example\n"
        ",missing.example\n"
        "Duplicate,https://www.existing.example/about\n"
        "Invalid,ftp://invalid.example\n"
        "Second,second.example\n",
    )

    with SessionLocal() as session:
        result = ingest_company_csv(session, project_id, path)
        created_names = [
            session.get_one(Company, company_id).name for company_id in result.created_company_ids
        ]

        assert result.total_rows == 5
        assert result.imported == 2
        assert result.skipped_duplicates == 1
        assert result.failed == 2
        assert created_names == ["First", "Second"]
        assert_result_invariant(
            result.total_rows,
            result.imported,
            result.skipped_duplicates,
            result.failed,
        )
