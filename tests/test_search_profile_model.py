from sqlalchemy import delete, select

from app.core.database.session import SessionLocal
from app.modules.project.models import Project
from app.modules.search_profile import SearchProfile


def create_project(name: str = "Search Profile Project") -> int:
    with SessionLocal() as session:
        project = Project(name=name)
        session.add(project)
        session.commit()
        return project.id


def test_create_search_profile_with_required_fields() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        profile = SearchProfile(
            project_id=project_id,
            name="Handcrafted furniture buyers",
            product_or_service="handcrafted furniture",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)

        assert profile.id is not None
        assert profile.project_id == project_id
        assert profile.name == "Handcrafted furniture buyers"
        assert profile.description is None
        assert profile.product_or_service == "handcrafted furniture"


def test_scalar_defaults() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        profile = SearchProfile(
            project_id=project_id,
            name="Accounting SaaS customers",
            product_or_service="accounting SaaS",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)

        assert profile.result_limit == 10
        assert profile.max_queries_per_run == 10
        assert profile.total_result_ceiling == 100
        assert profile.enabled is True


def test_json_list_defaults_are_independent_between_instances() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        first = SearchProfile(
            project_id=project_id,
            name="Industrial equipment distributors",
            product_or_service="industrial equipment",
        )
        second = SearchProfile(
            project_id=project_id,
            name="Recruitment service buyers",
            product_or_service="recruitment services",
        )
        session.add_all([first, second])
        session.commit()
        session.refresh(first)
        session.refresh(second)

        assert first.target_customer_types == []
        assert second.target_customer_types == []
        assert first.target_customer_types is not second.target_customer_types

        first.target_customer_types.append("distributors")

        assert first.target_customer_types == ["distributors"]
        assert second.target_customer_types == []


def test_create_search_profile_with_all_json_values() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        profile = SearchProfile(
            project_id=project_id,
            name="Commercial cleaning Australia",
            description="Find commercial cleaning buyers across major Australian cities.",
            product_or_service="commercial cleaning",
            target_customer_types=["offices", "hotels", "property managers"],
            target_industries=["real estate", "hospitality"],
            positive_keywords=["commercial cleaning", "facility management"],
            negative_keywords=["jobs", "residential"],
            countries=["Australia"],
            cities=["Sydney", "Melbourne"],
            languages=["en"],
            query_templates=["{target_customer_type} {city} {country}"],
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)

        assert profile.target_customer_types == ["offices", "hotels", "property managers"]
        assert profile.target_industries == ["real estate", "hospitality"]
        assert profile.positive_keywords == ["commercial cleaning", "facility management"]
        assert profile.negative_keywords == ["jobs", "residential"]
        assert profile.countries == ["Australia"]
        assert profile.cities == ["Sydney", "Melbourne"]
        assert profile.languages == ["en"]
        assert profile.query_templates == ["{target_customer_type} {city} {country}"]


def test_search_profile_project_relationship() -> None:
    project_id = create_project("Accounting Project")

    with SessionLocal() as session:
        profile = SearchProfile(
            project_id=project_id,
            name="Accounting SaaS customers",
            product_or_service="accounting SaaS",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)

        assert profile.project.id == project_id
        assert profile.project.name == "Accounting Project"


def test_project_search_profiles_relationship() -> None:
    project_id = create_project("Industrial Project")

    with SessionLocal() as session:
        project = session.get_one(Project, project_id)
        project.search_profiles.append(
            SearchProfile(
                name="Industrial equipment distributors",
                product_or_service="industrial equipment",
            )
        )
        session.commit()
        session.refresh(project)

        assert len(project.search_profiles) == 1
        assert project.search_profiles[0].name == "Industrial equipment distributors"
        assert project.search_profiles[0].project_id == project_id


def test_deleting_project_cascades_search_profile_at_database_level() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        profile = SearchProfile(
            project_id=project_id,
            name="Recruitment service buyers",
            product_or_service="recruitment services",
        )
        session.add(profile)
        session.commit()

    with SessionLocal() as session:
        session.execute(delete(Project).where(Project.id == project_id))
        session.commit()

    with SessionLocal() as session:
        profiles = list(session.scalars(select(SearchProfile)))

    assert profiles == []


def test_multiple_search_profiles_can_belong_to_one_project() -> None:
    project_id = create_project()

    with SessionLocal() as session:
        session.add_all(
            [
                SearchProfile(
                    project_id=project_id,
                    name="Handcrafted furniture buyers",
                    product_or_service="handcrafted furniture",
                ),
                SearchProfile(
                    project_id=project_id,
                    name="Accounting SaaS customers",
                    product_or_service="accounting SaaS",
                ),
            ]
        )
        session.commit()

    with SessionLocal() as session:
        project = session.get_one(Project, project_id)

        assert [profile.name for profile in project.search_profiles] == [
            "Handcrafted furniture buyers",
            "Accounting SaaS customers",
        ]
