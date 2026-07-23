from app.core.database.session import SessionLocal
from app.modules.company.repository import CompanyRepository
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


def test_create_generic_contact_with_social_channels() -> None:
    company_id = create_company_for_contact()
    with SessionLocal() as session:
        contact = ContactRepository(session).create(
            company_id=company_id,
            first_name=None,
            email="info@example.com",
            linkedin_url="https://www.linkedin.com/company/example",
            instagram_url="https://www.instagram.com/example",
        )
        assert contact.first_name is None
        assert contact.linkedin_url == "https://www.linkedin.com/company/example"
        assert contact.instagram_url == "https://www.instagram.com/example"


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
