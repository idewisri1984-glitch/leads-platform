from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
from app.modules.contact.repository import ContactRepository
from app.modules.contact.schemas import ContactCreate
from app.modules.contact.service import ContactService
from app.modules.project.repository import ProjectRepository


def create_company_for_contact_service() -> int:
    with SessionLocal() as session:
        project_repository = ProjectRepository(session)
        company_repository = CompanyRepository(session)

        project = project_repository.create("Contact Service Project")
        company = company_repository.create(
            project_id=project.id,
            name="Contact Service Company",
        )

        return company.id


def test_create_contact_service() -> None:
    company_id = create_company_for_contact_service()

    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contact = service.create(
            ContactCreate(
                company_id=company_id,
                first_name="Alan",
                last_name="Turing",
                email="alan@example.com",
            )
        )

        assert contact.id is not None
        assert contact.company_id == company_id
        assert contact.first_name == "Alan"
        assert contact.last_name == "Turing"
        assert contact.email == "alan@example.com"


def test_get_contact_service() -> None:
    company_id = create_company_for_contact_service()

    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        created = service.create(
            ContactCreate(
                company_id=company_id,
                first_name="Barbara",
            )
        )

        loaded = service.get(created.id)

        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.first_name == "Barbara"


def test_get_missing_contact_service() -> None:
    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        contact = service.get(0)

        assert contact is None


def test_get_all_contact_service() -> None:
    company_id = create_company_for_contact_service()

    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        service.create(
            ContactCreate(
                company_id=company_id,
                first_name="Tim",
            )
        )

        contacts = service.get_all()

        assert isinstance(contacts, list)
        assert len(contacts) >= 1


def test_get_contacts_by_company_service() -> None:
    company_id = create_company_for_contact_service()

    with SessionLocal() as session:
        repository = ContactRepository(session)
        service = ContactService(repository)

        service.create(
            ContactCreate(
                company_id=company_id,
                first_name="Radia",
            )
        )

        contacts = service.get_by_company(company_id)

        assert len(contacts) >= 1
        assert contacts[0].company_id == company_id
