from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact.repository import ContactRepository
from app.modules.lead.models import Lead
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository


class StrictLeadCreationSession:
    def __init__(self, flush_error: BaseException | None = None) -> None:
        self.operations: list[str] = []
        self.added: list[object] = []
        self.flush_error = flush_error

    def add(self, instance: object) -> None:
        self.operations.append("add")
        self.added.append(instance)

    def flush(self) -> None:
        self.operations.append("flush")
        if self.flush_error is not None:
            raise self.flush_error
        added_lead = cast(Lead, self.added[-1])
        added_lead.id = 731

    def commit(self) -> None:
        self.operations.append("commit")

    def rollback(self) -> None:
        self.operations.append("rollback")

    def refresh(self, instance: object) -> None:
        self.operations.append("refresh")

    def close(self) -> None:
        self.operations.append("close")


def strict_creation_repository(
    *,
    flush_error: BaseException | None = None,
) -> tuple[LeadRepository, StrictLeadCreationSession]:
    session = StrictLeadCreationSession(flush_error)
    return LeadRepository(cast(Session, session)), session


def create_company_and_contact() -> tuple[int, int]:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)
        contact_repository = ContactRepository(session)

        project = project_repository.create("Lead Repository Project")
        company = company_repository.create(
            project_id=project.id,
            name="Lead Repository Company",
        )
        contact = contact_repository.create(
            company_id=company.id,
            first_name="Ada",
        )

        return company.id, contact.id


def create_second_company() -> int:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Second Lead Project")
        company = company_repository.create(
            project_id=project.id,
            name="Second Lead Company",
        )

        return company.id


def test_create_lead_with_company_only() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(company_id=company_id)

        assert lead.id is not None
        assert lead.company_id == company_id
        assert lead.contact_id is None
        assert lead.status == "NEW"


def test_create_lead_with_company_and_contact() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(
            company_id=company_id,
            contact_id=contact_id,
            source="referral",
            notes="Qualified lead",
        )

        assert lead.company_id == company_id
        assert lead.contact_id == contact_id
        assert lead.source == "referral"
        assert lead.notes == "Qualified lead"


def test_get_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(company_id=company_id)

        loaded = repository.get(lead.id)

        assert loaded is not None
        assert loaded.id == lead.id


def test_get_all_leads() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(company_id=company_id)

        leads = repository.get_all()

        assert [stored.id for stored in leads] == [lead.id]


def test_get_leads_by_company() -> None:
    company_id, _ = create_company_and_contact()
    second_company_id = create_second_company()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        expected = repository.create(company_id=company_id)
        repository.create(company_id=second_company_id)

        leads = repository.get_by_company(company_id)

        assert [lead.id for lead in leads] == [expected.id]


def test_get_leads_by_contact() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        contact_repository = ContactRepository(session)
        repository = LeadRepository(session)
        second_contact = contact_repository.create(
            company_id=company_id,
            first_name="Grace",
        )
        expected = repository.create(company_id=company_id, contact_id=contact_id)
        repository.create(company_id=company_id, contact_id=second_contact.id)

        leads = repository.get_by_contact(contact_id)

        assert [lead.id for lead in leads] == [expected.id]


def test_update_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(company_id=company_id)
        lead.status = "QUALIFIED"

        updated = repository.update(lead)

        assert updated.status == "QUALIFIED"


def test_delete_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        lead = repository.create(company_id=company_id)
        lead_id = lead.id

        repository.delete(lead)

        assert repository.get(lead_id) is None


def test_contact_id_becomes_none_when_contact_is_deleted() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        contact_repository = ContactRepository(session)
        lead_repository = LeadRepository(session)
        lead = lead_repository.create(company_id=company_id, contact_id=contact_id)
        lead_id = lead.id
        contact = contact_repository.get(contact_id)

        assert contact is not None

        contact_repository.delete(contact)

    with SessionLocal() as session:
        stored_lead = LeadRepository(session).get(lead_id)

        assert stored_lead is not None
        assert stored_lead.contact_id is None


def test_create_for_contact_maps_fields_flushes_and_returns_same_instance() -> None:
    repository, session = strict_creation_repository()

    lead = repository.create_for_contact(
        company_id=11,
        contact_id=22,
        status=" Qualified ",
        source=" Internal  Referral ",
    )

    assert lead is session.added[0]
    assert lead.id == 731
    assert lead.company_id == 11
    assert lead.contact_id == 22
    assert lead.status == " Qualified "
    assert lead.source == " Internal  Referral "
    assert lead.notes is None
    assert session.operations == ["add", "flush"]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("company_id", True),
        ("company_id", 0),
        ("company_id", -1),
        ("company_id", "1"),
        ("company_id", 1.0),
        ("company_id", None),
        ("company_id", object()),
        ("company_id", type("IntSubclass", (int,), {})(1)),
        ("contact_id", False),
        ("contact_id", 0),
        ("contact_id", -1),
        ("contact_id", "1"),
        ("contact_id", 1.0),
        ("contact_id", None),
        ("contact_id", object()),
        ("contact_id", type("OtherIntSubclass", (int,), {})(1)),
    ],
)
def test_create_for_contact_rejects_invalid_identifiers_before_session_use(
    field_name: str,
    invalid_value: object,
) -> None:
    repository, session = strict_creation_repository()
    arguments: dict[str, object] = {"company_id": 1, "contact_id": 2}
    arguments[field_name] = invalid_value

    with pytest.raises(ValueError, match=r"^Lead creation data is invalid\.$"):
        repository.create_for_contact(**arguments)  # type: ignore[arg-type]

    assert session.operations == []
    assert session.added == []


@pytest.mark.parametrize("invalid_status", [None, True, 1, "", "   ", "x" * 51])
def test_create_for_contact_rejects_invalid_status(
    invalid_status: object,
) -> None:
    repository, session = strict_creation_repository()

    with pytest.raises(ValueError, match=r"^Lead creation data is invalid\.$"):
        repository.create_for_contact(
            company_id=1,
            contact_id=2,
            status=invalid_status,  # type: ignore[arg-type]
        )

    assert session.operations == []


def test_create_for_contact_accepts_status_boundary_without_normalizing() -> None:
    repository, session = strict_creation_repository()
    status = "MiXeD " + ("x" * 44)

    lead = repository.create_for_contact(
        company_id=1,
        contact_id=2,
        status=status,
    )

    assert len(status) == 50
    assert lead.status == status
    assert session.operations == ["add", "flush"]


@pytest.mark.parametrize("invalid_source", [True, 1, "", "   ", "x" * 101])
def test_create_for_contact_rejects_invalid_source(
    invalid_source: object,
) -> None:
    repository, session = strict_creation_repository()

    with pytest.raises(ValueError, match=r"^Lead creation data is invalid\.$"):
        repository.create_for_contact(
            company_id=1,
            contact_id=2,
            source=invalid_source,  # type: ignore[arg-type]
        )

    assert session.operations == []


def test_create_for_contact_accepts_source_boundary_without_normalizing() -> None:
    repository, session = strict_creation_repository()
    source = " Mixed  Source " + ("x" * 85)

    lead = repository.create_for_contact(
        company_id=1,
        contact_id=2,
        source=source,
    )

    assert len(source) == 100
    assert lead.source == source
    assert session.operations == ["add", "flush"]


@pytest.mark.parametrize(
    "flush_error",
    [
        RuntimeError("ordinary flush failure"),
        SQLAlchemyError("sqlalchemy flush failure"),
        IntegrityError("insert", {}, RuntimeError("integrity failure")),
    ],
)
def test_create_for_contact_propagates_flush_errors_unchanged(
    flush_error: BaseException,
) -> None:
    repository, session = strict_creation_repository(flush_error=flush_error)

    with pytest.raises(type(flush_error)) as captured:
        repository.create_for_contact(company_id=1, contact_id=2)

    assert captured.value is flush_error
    assert session.operations == ["add", "flush"]
    assert "commit" not in session.operations
