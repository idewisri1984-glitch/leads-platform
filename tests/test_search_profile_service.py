import pytest
from pydantic import ValidationError

from app.core.database.session import SessionLocal
from app.modules.project.models import Project
from app.modules.search_profile import (
    SearchProfileCreate,
    SearchProfileRead,
    SearchProfileRepository,
    SearchProfileService,
    SearchProfileUpdate,
)


def create_project(name: str = "Search Profile Service Project") -> int:
    with SessionLocal() as session:
        project = Project(name=name)
        session.add(project)
        session.commit()
        return project.id


def make_create_data(
    *,
    project_id: int,
    name: str = "Accounting SaaS customers",
    product_or_service: str = "accounting SaaS",
) -> SearchProfileCreate:
    return SearchProfileCreate(
        project_id=project_id,
        name=name,
        product_or_service=product_or_service,
        target_customer_types=["small businesses"],
        countries=["Germany"],
    )


def test_create_returns_search_profile_read() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))

        profile = service.create(make_create_data(project_id=project_id))

        assert isinstance(profile, SearchProfileRead)
        assert profile.id is not None
        assert profile.project_id == project_id
        assert profile.name == "Accounting SaaS customers"


def test_get_existing_profile() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        found = service.get(created.id)

        assert found == created


def test_get_nonexistent_profile_returns_none() -> None:
    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))

        assert service.get(999_999) is None


def test_get_all_profiles() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        first = service.create(make_create_data(project_id=project_id, name="Accounting SaaS"))
        second = service.create(
            make_create_data(
                project_id=project_id,
                name="Industrial equipment",
                product_or_service="industrial equipment",
            )
        )

        profiles = service.get_all()

        assert [profile.id for profile in profiles] == [first.id, second.id]


def test_get_by_project() -> None:
    first_project_id = create_project("First Project")
    second_project_id = create_project("Second Project")

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        included = service.create(make_create_data(project_id=first_project_id))
        service.create(
            make_create_data(
                project_id=second_project_id,
                name="Furniture buyers",
                product_or_service="handcrafted furniture",
            )
        )

        profiles = service.get_by_project(first_project_id)

        assert [profile.id for profile in profiles] == [included.id]


def test_update_scalar_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        updated = service.update(
            created.id,
            SearchProfileUpdate(
                name="Updated recruitment buyers",
                product_or_service="recruitment services",
                result_limit=25,
                enabled=False,
            ),
        )

        assert updated is not None
        assert updated.name == "Updated recruitment buyers"
        assert updated.product_or_service == "recruitment services"
        assert updated.result_limit == 25
        assert updated.enabled is False


def test_update_list_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        updated = service.update(
            created.id,
            SearchProfileUpdate(
                target_customer_types=["factories", "engineering contractors"],
                countries=["UAE"],
                positive_keywords=["industrial equipment distributors"],
            ),
        )

        assert updated is not None
        assert updated.target_customer_types == ["factories", "engineering contractors"]
        assert updated.countries == ["UAE"]
        assert updated.positive_keywords == ["industrial equipment distributors"]


def test_partial_update_preserves_unspecified_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        updated = service.update(created.id, SearchProfileUpdate(name="Renamed profile"))

        assert updated is not None
        assert updated.name == "Renamed profile"
        assert updated.product_or_service == "accounting SaaS"
        assert updated.target_customer_types == ["small businesses"]
        assert updated.countries == ["Germany"]


def test_update_cannot_remove_all_targeting_dimensions() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        with pytest.raises(ValidationError, match="At least one targeting dimension"):
            service.update(
                created.id,
                SearchProfileUpdate(
                    target_customer_types=[],
                    target_industries=[],
                    positive_keywords=[],
                ),
            )

        unchanged = service.get(created.id)

        assert unchanged is not None
        assert unchanged.target_customer_types == ["small businesses"]


def test_update_nonexistent_profile_returns_none() -> None:
    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))

        assert service.update(999_999, SearchProfileUpdate(name="Missing")) is None


def test_delete_existing_profile_returns_true() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        deleted = service.delete(created.id)

        assert deleted is True
        assert service.get(created.id) is None


def test_delete_nonexistent_profile_returns_false() -> None:
    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))

        assert service.delete(999_999) is False


def test_service_returns_read_schemas_not_orm_objects() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        service = SearchProfileService(SearchProfileRepository(session))
        created = service.create(make_create_data(project_id=project_id))

        results = [
            created,
            service.get(created.id),
            *service.get_all(),
            *service.get_by_project(project_id),
        ]

        assert all(result is None or isinstance(result, SearchProfileRead) for result in results)
