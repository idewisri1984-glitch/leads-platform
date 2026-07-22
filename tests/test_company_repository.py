import pytest

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.project.repository import ProjectRepository


def test_create_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="OpenAI",
        )

        assert company.id is not None
        assert company.name == "OpenAI"
        assert company.project_id == project.id


def test_get_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Microsoft",
        )

        loaded = company_repository.get(company.id)

        assert loaded is not None
        assert loaded.id == company.id
        assert loaded.name == "Microsoft"


def test_get_all_companies() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company_repository.create(
            project_id=project.id,
            name="Google",
        )

        companies = company_repository.get_all()

        assert isinstance(companies, list)
        assert len(companies) >= 1


def test_get_companies_by_project() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company_repository.create(
            project_id=project.id,
            name="Apple",
        )

        companies = company_repository.get_by_project(project.id)

        assert len(companies) >= 1
        assert companies[0].project_id == project.id


def test_update_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Old Name",
        )

        company.name = "New Name"

        updated = company_repository.update(company)

        assert updated.name == "New Name"


def test_delete_company() -> None:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Repository Test")

        company = company_repository.create(
            project_id=project.id,
            name="Delete Me",
        )

        company_id = company.id

        company_repository.delete(company)

        deleted = company_repository.get(company_id)

        assert deleted is None


def test_promotion_create_is_flushed_and_owned_by_caller_transaction() -> None:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Promotion Repository Test")
        repository = CompanyRepository(session)
        company = repository.create_for_promotion(
            project_id=project.id,
            name="Promotion Company",
            website="https://promotion.example",
            country="US",
        )
        company_id = company.id

        assert company_id is not None
        assert repository.get_for_project(project.id, company_id) is company
        session.rollback()

    with SessionLocal() as verification:
        assert verification.get(Company, company_id) is None


def test_promotion_duplicate_lookup_is_normalized_and_project_scoped() -> None:
    with SessionLocal() as session:
        first = ProjectRepository(session).create("First Promotion Project")
        second = ProjectRepository(session).create("Second Promotion Project")
        repository = CompanyRepository(session)
        existing = repository.create(
            project_id=first.id,
            name="Existing",
            website="https://www.example.com/about",
        )

        assert repository.find_duplicate_by_website(first.id, "EXAMPLE.COM") is existing
        assert repository.find_duplicate_by_website(second.id, "https://example.com") is None
        assert repository.get_for_project(first.id, existing.id) is existing
        assert repository.get_for_project(second.id, existing.id) is None


@pytest.mark.parametrize("project_id", [0, -1, True, "1", None])
def test_promotion_scope_rejects_invalid_project_id(project_id: object) -> None:
    with SessionLocal() as session:
        repository = CompanyRepository(session)

        with pytest.raises(ValueError, match="Project ID must be a positive integer"):
            repository.acquire_promotion_scope(project_id)  # type: ignore[arg-type]


def test_promotion_scope_verifies_project_and_preserves_project_data() -> None:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Promotion Scope Project")
        project_id = project.id
        repository = CompanyRepository(session)

        repository.acquire_promotion_scope(project_id)

        assert project.name == "Promotion Scope Project"
        with pytest.raises(ValueError, match="Project was not found"):
            repository.acquire_promotion_scope(project_id + 1_000_000)
        session.rollback()


def test_promotion_repository_methods_do_not_control_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SessionLocal() as session:
        project = ProjectRepository(session).create("Promotion Transaction Test")
        repository = CompanyRepository(session)
        calls = {"commit": 0, "rollback": 0, "close": 0}

        monkeypatch.setattr(session, "commit", lambda: calls.__setitem__("commit", 1))
        monkeypatch.setattr(session, "rollback", lambda: calls.__setitem__("rollback", 1))
        monkeypatch.setattr(session, "close", lambda: calls.__setitem__("close", 1))

        repository.acquire_promotion_scope(project.id)
        company = repository.create_for_promotion(
            project_id=project.id,
            name="Transactionless Company",
            website=None,
            country=None,
        )
        repository.get_for_project(project.id, company.id)

        assert calls == {"commit": 0, "rollback": 0, "close": 0}
        monkeypatch.undo()
        session.rollback()
