from sqlalchemy import delete, select

from app.core.database.session import SessionLocal
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.lead.models import Lead
from app.modules.project.models import Project


def create_company_and_contact() -> tuple[int, int]:
    with SessionLocal() as session:
        project = Project(name="Lead Model Project")
        company = Company(project=project, name="Lead Model Company")
        contact = Contact(company=company, first_name="Ada")
        session.add(project)
        session.commit()

        return company.id, contact.id


def test_create_lead_with_relationships() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        company = session.get_one(Company, company_id)
        contact = session.get_one(Contact, contact_id)
        lead = Lead(
            company=company,
            contact=contact,
            source="referral",
            notes="Initial lead",
        )
        session.add(lead)
        session.commit()
        session.refresh(lead)

        assert lead.id is not None
        assert lead.company_id == company_id
        assert lead.contact_id == contact_id
        assert lead.status == "NEW"
        assert lead.source == "referral"
        assert lead.notes == "Initial lead"
        assert lead in company.leads
        assert lead in contact.leads


def test_deleting_company_cascades_to_lead() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=contact_id)
        session.add(lead)
        session.commit()
        lead_id = lead.id

        session.execute(delete(Company).where(Company.id == company_id))
        session.commit()

    with SessionLocal() as session:
        assert session.get(Lead, lead_id) is None


def test_deleting_contact_sets_lead_contact_to_none() -> None:
    company_id, contact_id = create_company_and_contact()

    with SessionLocal() as session:
        lead = Lead(company_id=company_id, contact_id=contact_id)
        session.add(lead)
        session.commit()
        lead_id = lead.id

        session.execute(delete(Contact).where(Contact.id == contact_id))
        session.commit()

    with SessionLocal() as session:
        stored_lead = session.scalar(select(Lead).where(Lead.id == lead_id))

        assert stored_lead is not None
        assert stored_lead.contact_id is None
