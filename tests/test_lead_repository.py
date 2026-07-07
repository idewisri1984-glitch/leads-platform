from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact.repository import ContactRepository
from app.modules.lead.repository import LeadRepository
from app.modules.project.repository import ProjectRepository


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
