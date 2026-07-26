from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.company.repository import CompanyRepository
from app.modules.contact.models import Contact
from app.modules.contact.repository import ContactRepository
from app.modules.project.repository import ProjectRepository


def create_company_for_contact() -> int:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Contact Repository Project")
        company = company_repository.create(
            project_id=project.id,
            name="Contact Repository Company",
        )

        return company.id


def test_create_contact() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        contact = repository.create(
            company_id=company_id,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
        )

        assert contact.id is not None
        assert contact.company_id == company_id
        assert contact.first_name == "Ada"
        assert contact.last_name == "Lovelace"
        assert contact.email == "ada@example.com"


def test_get_contact() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        contact = repository.create(
            company_id=company_id,
            first_name="Grace",
        )

        loaded = repository.get(contact.id)

        assert loaded is not None
        assert loaded.id == contact.id
        assert loaded.first_name == "Grace"


def test_get_all_contacts() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        repository.create(
            company_id=company_id,
            first_name="Katherine",
        )

        contacts = repository.get_all()

        assert isinstance(contacts, list)
        assert len(contacts) >= 1


def test_get_contacts_by_company() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        repository.create(
            company_id=company_id,
            first_name="Margaret",
        )

        contacts = repository.get_by_company(company_id)

        assert len(contacts) >= 1
        assert contacts[0].company_id == company_id


def test_update_contact() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        contact = repository.create(
            company_id=company_id,
            first_name="Old",
        )

        contact.first_name = "New"

        updated = repository.update(contact)

        assert updated.first_name == "New"


def test_delete_contact() -> None:
    company_id = create_company_for_contact()

    with SessionLocal() as session:
        repository = ContactRepository(session)

        contact = repository.create(
            company_id=company_id,
            first_name="Delete",
        )

        contact_id = contact.id

        repository.delete(contact)

        deleted = repository.get(contact_id)

        assert deleted is None


@pytest.mark.parametrize("invalid_id", [0, -1, True, False, 1.5, "1", None])
def test_promotion_methods_reject_invalid_ids(invalid_id: object) -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        with pytest.raises(ValueError, match="promotion data"):
            repository.acquire_promotion_scope(cast(Any, invalid_id))
        with pytest.raises(ValueError, match="promotion data"):
            repository.get_for_company(1, cast(Any, invalid_id))


def test_acquire_promotion_scope_requires_company() -> None:
    with (
        SessionLocal() as session,
        pytest.raises(ValueError, match="Company was not found"),
    ):
        ContactRepository(session).acquire_promotion_scope(2_147_483_647)


def test_sqlite_promotion_scope_updates_before_verifying_company() -> None:
    class Dialect:
        name = "sqlite"

    class Bind:
        dialect = Dialect()

    class RecordingSession:
        events: list[str]

        def __init__(self) -> None:
            self.events = []

        def get_bind(self) -> Bind:
            return Bind()

        def execute(self, statement: Any) -> None:
            self.events.append(f"update:{statement.table.name}")

        def scalar(self, statement: Any) -> int:
            self.events.append("verify")
            return 1

    recording = RecordingSession()
    ContactRepository(cast(Session, recording)).acquire_promotion_scope(7)
    assert recording.events == ["update:companies", "verify"]


def test_locking_database_uses_select_for_update() -> None:
    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class RecordingSession:
        statement: Any = None

        def get_bind(self) -> Bind:
            return Bind()

        def scalar(self, statement: Any) -> int:
            self.statement = statement
            return 1

    recording = RecordingSession()
    ContactRepository(cast(Session, recording)).acquire_promotion_scope(8)
    assert recording.statement._for_update_arg is not None


def test_company_scoped_contact_and_duplicate_lookup() -> None:
    first_id = create_company_for_contact()
    second_id = create_company_for_contact()
    with SessionLocal() as session:
        repository = ContactRepository(session)
        first = repository.create(
            company_id=first_id,
            first_name="First",
            email="  ADA@Example.COM ",
        )
        second = repository.create(
            company_id=first_id,
            first_name="Second",
            email="ada@example.com",
        )
        repository.create(company_id=first_id, first_name="No Email", email=None)
        other = repository.create(
            company_id=second_id,
            first_name="Other",
            email="ada@example.com",
        )
        assert repository.get_for_company(first_id, first.id) is first
        assert repository.get_for_company(first_id, other.id) is None
        duplicate = repository.find_promotion_duplicate_by_email(first_id, "ada@example.com")
        assert duplicate is not None
        assert duplicate.id == min(first.id, second.id)
        assert repository.find_promotion_duplicate_by_email(second_id, "ada@example.com") is other


@pytest.mark.parametrize(
    "invalid_email",
    ["", " ADA@example.com", "ada@example.com ", "ADA@example.com", "a" * 256, True, 1, None],
)
def test_duplicate_lookup_rejects_unsafe_normalized_email(invalid_email: object) -> None:
    with (
        SessionLocal() as session,
        pytest.raises(ValueError) as raised,
    ):
        ContactRepository(session).find_promotion_duplicate_by_email(1, cast(Any, invalid_email))
    error = str(raised.value)
    assert error == "Normalized promotion email is invalid."
    if invalid_email:
        assert str(invalid_email) not in error


def promotion_values(company_id: int) -> dict[str, object]:
    return {
        "company_id": company_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "job_title": "Director",
        "email": "ada@example.com",
        "phone": "+15550100",
        "source": "contact-discovery",
        "external_id": "candidate:1",
        "status": "NEW",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("first_name", "x" * 101),
        ("last_name", "x" * 101),
        ("job_title", "x" * 151),
        ("email", "x" * 256),
        ("phone", "x" * 101),
        ("source", ""),
        ("source", "x" * 101),
        ("external_id", "x" * 256),
        ("status", ""),
        ("status", "x" * 51),
        ("email", True),
    ],
)
def test_create_for_promotion_validates_primitive_bounds(field: str, value: object) -> None:
    values = promotion_values(1)
    values[field] = value
    with (
        SessionLocal() as session,
        pytest.raises(ValueError) as raised,
    ):
        ContactRepository(session).create_for_promotion(**values)
    error = str(raised.value)
    assert error == "Contact promotion data is invalid."
    if value:
        assert str(value) not in error


def test_create_for_promotion_flushes_without_owning_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_id = create_company_for_contact()
    with SessionLocal() as session:
        repository = ContactRepository(session)
        with monkeypatch.context() as context:
            context.setattr(session, "commit", lambda: pytest.fail("unexpected commit"))
            context.setattr(session, "rollback", lambda: pytest.fail("unexpected rollback"))
            context.setattr(session, "close", lambda: pytest.fail("unexpected close"))
            contact = repository.create_for_promotion(**promotion_values(company_id))
            assert contact.id is not None
        contact_id = contact.id
        session.rollback()
    with SessionLocal() as verification:
        assert verification.get(Contact, contact_id) is None


def test_create_for_promotion_persists_only_after_caller_commit() -> None:
    company_id = create_company_for_contact()
    with SessionLocal() as session:
        company_before = session.scalar(select(Company).where(Company.id == company_id))
        assert company_before is not None
        snapshot = (company_before.name, company_before.status, company_before.notes)
        contact = ContactRepository(session).create_for_promotion(**promotion_values(company_id))
        contact_id = contact.id
        session.commit()
        company_after = session.scalar(select(Company).where(Company.id == company_id))
        assert company_after is not None
        assert snapshot == (company_after.name, company_after.status, company_after.notes)
    with SessionLocal() as verification:
        stored = verification.get(Contact, contact_id)
        assert stored is not None
        assert stored.first_name == "Ada"
        assert stored.source == "contact-discovery"


def test_promotion_repository_has_no_network_or_service_dependency() -> None:
    source = Path("app/modules/contact/repository.py").read_text(encoding="utf-8").casefold()
    for forbidden in (
        "contactservice",
        "httpx",
        "requests",
        "socket",
        "serpapi",
        "provider",
        "parser",
        "fetcher",
    ):
        assert forbidden not in source
