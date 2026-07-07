import pytest

from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact.repository import ContactRepository
from app.modules.lead.repository import LeadRepository
from app.modules.lead.schemas import LeadCreate, LeadRead
from app.modules.lead.service import LeadService
from app.modules.project.repository import ProjectRepository


def create_company_and_contact(name: str = "Lead Service Company") -> tuple[int, int]:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)
        contact_repository = ContactRepository(session)

        project = project_repository.create(f"{name} Project")
        company = company_repository.create(project_id=project.id, name=name)
        contact = contact_repository.create(company_id=company.id, first_name="Ada")

        return company.id, contact.id


def test_create_lead_with_company_only() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        service = LeadService(LeadRepository(session), ContactRepository(session))
        lead = service.create(LeadCreate(company_id=company_id))

        assert isinstance(lead, LeadRead)
        assert lead.company_id == company_id
        assert lead.contact_id is None
        assert lead.status == "NEW"


def test_create_lead_with_matching_contact() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        service = LeadService(LeadRepository(session), ContactRepository(session))
        lead = service.create(
            LeadCreate(
                company_id=company_id,
                contact_id=contact_id,
                source="referral",
            )
        )

        assert lead.company_id == company_id
        assert lead.contact_id == contact_id
        assert lead.source == "referral"


def test_reject_lead_when_contact_belongs_to_another_company() -> None:
    first_company_id, contact_id = create_company_and_contact("First Company")
    second_company_id, _ = create_company_and_contact("Second Company")

    assert first_company_id != second_company_id

    with SessionLocal() as session:
        service = LeadService(LeadRepository(session), ContactRepository(session))

        with pytest.raises(
            ValueError,
            match=f"Contact {contact_id} does not belong to company {second_company_id}",
        ):
            service.create(LeadCreate(company_id=second_company_id, contact_id=contact_id))


def test_get_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))
        created = service.create(LeadCreate(company_id=company_id))

        loaded = service.get(created.id)

        assert loaded is not None
        assert loaded.id == created.id


def test_get_all_leads() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))
        created = service.create(LeadCreate(company_id=company_id))

        leads = service.get_all()

        assert [lead.id for lead in leads] == [created.id]


def test_get_leads_by_company() -> None:
    company_id, _ = create_company_and_contact("First Company")
    second_company_id, _ = create_company_and_contact("Second Company")

    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))
        expected = service.create(LeadCreate(company_id=company_id))
        service.create(LeadCreate(company_id=second_company_id))

        leads = service.get_by_company(company_id)

        assert [lead.id for lead in leads] == [expected.id]


def test_get_leads_by_contact() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        contact_repository = ContactRepository(session)
        repository = LeadRepository(session)
        service = LeadService(repository, contact_repository)
        second_contact = contact_repository.create(
            company_id=company_id,
            first_name="Grace",
        )
        expected = service.create(LeadCreate(company_id=company_id, contact_id=contact_id))
        service.create(LeadCreate(company_id=company_id, contact_id=second_contact.id))

        leads = service.get_by_contact(contact_id)

        assert [lead.id for lead in leads] == [expected.id]


def test_update_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))
        created = service.create(LeadCreate(company_id=company_id))
        lead = repository.get(created.id)

        assert lead is not None

        lead.status = "QUALIFIED"
        updated = service.update(lead)

        assert updated.status == "QUALIFIED"


def test_delete_lead() -> None:
    company_id, _ = create_company_and_contact()

    with SessionLocal() as session:
        repository = LeadRepository(session)
        service = LeadService(repository, ContactRepository(session))
        created = service.create(LeadCreate(company_id=company_id))
        lead = repository.get(created.id)

        assert lead is not None

        service.delete(lead)

        assert service.get(created.id) is None
