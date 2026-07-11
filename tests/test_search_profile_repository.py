from app.core.database.session import SessionLocal
from app.modules.project.models import Project
from app.modules.search_profile import SearchProfile, SearchProfileRepository


def create_project(name: str = "Search Profile Repository Project") -> int:
    with SessionLocal() as session:
        project = Project(name=name)
        session.add(project)
        session.commit()
        return project.id


def make_profile(
    *,
    project_id: int,
    name: str = "Handcrafted furniture buyers",
    product_or_service: str = "handcrafted furniture",
) -> SearchProfile:
    return SearchProfile(
        project_id=project_id,
        name=name,
        product_or_service=product_or_service,
    )


def test_create_search_profile() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))

        assert profile.id is not None
        assert profile.project_id == project_id
        assert profile.name == "Handcrafted furniture buyers"
        assert profile.product_or_service == "handcrafted furniture"


def test_create_profiles_for_unrelated_industries() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        furniture = repository.create(
            make_profile(
                project_id=project_id,
                name="Handcrafted furniture buyers",
                product_or_service="handcrafted furniture",
            )
        )
        accounting = repository.create(
            make_profile(
                project_id=project_id,
                name="Accounting SaaS customers",
                product_or_service="accounting SaaS",
            )
        )
        industrial = repository.create(
            make_profile(
                project_id=project_id,
                name="Industrial equipment distributors",
                product_or_service="industrial equipment",
            )
        )

        assert [profile.name for profile in repository.get_all()] == [
            furniture.name,
            accounting.name,
            industrial.name,
        ]


def test_get_existing_search_profile() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))

        found = repository.get(profile.id)

        assert found is not None
        assert found.id == profile.id
        assert found.name == "Handcrafted furniture buyers"


def test_get_nonexistent_search_profile_returns_none() -> None:
    with SessionLocal() as session:
        assert SearchProfileRepository(session).get(999_999) is None


def test_get_all_returns_profiles_in_id_order() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        first = repository.create(
            make_profile(project_id=project_id, name="Accounting SaaS customers")
        )
        second = repository.create(
            make_profile(project_id=project_id, name="Recruitment service buyers")
        )

        profiles = repository.get_all()

        assert [profile.id for profile in profiles] == [first.id, second.id]
        assert [profile.name for profile in profiles] == [
            "Accounting SaaS customers",
            "Recruitment service buyers",
        ]


def test_get_by_project_excludes_profiles_from_other_projects() -> None:
    first_project_id = create_project("First Project")
    second_project_id = create_project("Second Project")

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        included = repository.create(
            make_profile(
                project_id=first_project_id,
                name="Accounting SaaS customers",
                product_or_service="accounting SaaS",
            )
        )
        repository.create(
            make_profile(
                project_id=second_project_id,
                name="Industrial equipment distributors",
                product_or_service="industrial equipment",
            )
        )

        profiles = repository.get_by_project(first_project_id)

        assert [profile.id for profile in profiles] == [included.id]
        assert profiles[0].project_id == first_project_id


def test_get_by_project_returns_multiple_profiles_for_same_project() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        first = repository.create(
            make_profile(
                project_id=project_id,
                name="Handcrafted furniture buyers",
                product_or_service="handcrafted furniture",
            )
        )
        second = repository.create(
            make_profile(
                project_id=project_id,
                name="Accounting SaaS customers",
                product_or_service="accounting SaaS",
            )
        )

        profiles = repository.get_by_project(project_id)

        assert [profile.id for profile in profiles] == [first.id, second.id]


def test_update_scalar_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))

        profile.name = "Updated accounting SaaS customers"
        profile.product_or_service = "accounting SaaS"
        profile.result_limit = 25
        profile.enabled = False
        updated = repository.update(profile)

        assert updated.name == "Updated accounting SaaS customers"
        assert updated.product_or_service == "accounting SaaS"
        assert updated.result_limit == 25
        assert updated.enabled is False

    with SessionLocal() as session:
        persisted = SearchProfileRepository(session).get(profile.id)

        assert persisted is not None
        assert persisted.name == "Updated accounting SaaS customers"
        assert persisted.product_or_service == "accounting SaaS"
        assert persisted.result_limit == 25
        assert persisted.enabled is False


def test_update_json_list_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))

        profile.target_customer_types = ["small businesses", "accounting firms"]
        profile.countries = ["Germany"]
        profile.positive_keywords = ["bookkeeping software", "tax accounting"]
        updated = repository.update(profile)

        assert updated.target_customer_types == ["small businesses", "accounting firms"]
        assert updated.countries == ["Germany"]
        assert updated.positive_keywords == ["bookkeeping software", "tax accounting"]

    with SessionLocal() as session:
        persisted = SearchProfileRepository(session).get(profile.id)

        assert persisted is not None
        assert persisted.target_customer_types == ["small businesses", "accounting firms"]
        assert persisted.countries == ["Germany"]
        assert persisted.positive_keywords == ["bookkeeping software", "tax accounting"]


def test_delete_search_profile() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))
        profile_id = profile.id

        repository.delete(profile)

        assert repository.get(profile_id) is None


def test_deleting_search_profile_does_not_delete_project() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        repository = SearchProfileRepository(session)
        profile = repository.create(make_profile(project_id=project_id))

        repository.delete(profile)

        assert session.get(Project, project_id) is not None
