import socket
from collections.abc import Callable
from typing import Any

import pytest
from typer.testing import CliRunner

import app.cli.search_profile as search_profile_cli
from app.cli.main import app
from app.core.database.session import SessionLocal
from app.modules.project import ProjectCreate, ProjectRepository, ProjectService

runner = CliRunner()


@pytest.fixture
def project_id() -> int:
    with SessionLocal() as session:
        project = ProjectService(ProjectRepository(session)).create(
            ProjectCreate(name="CLI Project")
        )
        return project.id


def create_profile(project_id: int, *extra: str) -> Any:
    result = runner.invoke(
        app,
        [
            "search-profile",
            "create",
            "--project-id",
            str(project_id),
            "--name",
            "Universal buyers",
            "--product-or-service",
            "Business offering",
            "--target-customer-type",
            "buyers",
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    profile_id = int(result.output.split("ID: ", maxsplit=1)[1].splitlines()[0])
    return profile_id, result


def test_search_profile_commands_are_registered() -> None:
    result = runner.invoke(app, ["search-profile", "--help"])

    assert result.exit_code == 0
    for command in ("create", "list", "show", "delete", "preview-queries"):
        assert command in result.output


@pytest.mark.parametrize(
    ("name", "product", "target_option", "target"),
    [
        ("Furniture buyers", "handcrafted furniture", "--target-customer-type", "buyers"),
        ("Accounting SaaS", "accounting SaaS", "--target-industry", "technology"),
        ("Pump distributors", "industrial pumps", "--positive-keyword", "distributors"),
    ],
)
def test_create_unrelated_profiles(
    project_id: int,
    name: str,
    product: str,
    target_option: str,
    target: str,
) -> None:
    result = runner.invoke(
        app,
        [
            "search-profile",
            "create",
            "--project-id",
            str(project_id),
            "--name",
            name,
            "--product-or-service",
            product,
            target_option,
            target,
        ],
    )

    assert result.exit_code == 0
    assert "OK: Search profile created" in result.output
    assert "ID:" in result.output


def test_create_accepts_repeated_options_and_preserves_schema_normalization(
    project_id: int,
) -> None:
    profile_id, _ = create_profile(
        project_id,
        "--target-customer-type",
        " Buyers ",
        "--target-industry",
        " Retail ",
        "--target-industry",
        "retail",
        "--country",
        " USA ",
        "--country",
        "usa",
    )

    shown = runner.invoke(app, ["search-profile", "show", str(profile_id)])
    assert "Target Customer Types: buyers" in shown.output
    assert "Target Industries: Retail" in shown.output
    assert "Countries: USA" in shown.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--name", "   ", "--target-customer-type", "buyers"],
        ["--name", "No targeting"],
        ["--name", "Bad limit", "--target-customer-type", "buyers", "--result-limit", "0"],
        [
            "--name",
            "Bad query count",
            "--target-customer-type",
            "buyers",
            "--max-queries-per-run",
            "101",
        ],
        [
            "--name",
            "Bad ceiling",
            "--target-customer-type",
            "buyers",
            "--total-result-ceiling",
            "1001",
        ],
    ],
)
def test_create_validation_is_controlled(project_id: int, arguments: list[str]) -> None:
    result = runner.invoke(
        app,
        [
            "search-profile",
            "create",
            "--project-id",
            str(project_id),
            "--product-or-service",
            "recruitment services",
            *arguments,
        ],
    )

    assert result.exit_code == 1
    assert "Invalid search profile:" in result.output
    assert "Traceback" not in result.output


def test_list_all_and_filter_by_project(project_id: int) -> None:
    first_id, _ = create_profile(project_id)
    with SessionLocal() as session:
        other = ProjectService(ProjectRepository(session)).create(
            ProjectCreate(name="Other Project")
        )
    second_id, _ = create_profile(other.id, "--country", "Australia")

    all_result = runner.invoke(app, ["search-profile", "list"])
    filtered = runner.invoke(app, ["search-profile", "list", "--project-id", str(project_id)])

    assert str(first_id) in all_result.output
    assert str(second_id) in all_result.output
    assert str(first_id) in filtered.output
    assert f"ID: {second_id} " not in filtered.output
    assert "Product or Service: Business offering" in filtered.output


def test_show_existing_and_missing_profile(project_id: int) -> None:
    profile_id, _ = create_profile(project_id, "--city", "Singapore", "--language", "English")

    shown = runner.invoke(app, ["search-profile", "show", str(profile_id)])
    missing = runner.invoke(app, ["search-profile", "show", "999999"])

    assert shown.exit_code == 0
    assert "Cities: Singapore" in shown.output
    assert "Result Limit: 10" in shown.output
    assert missing.exit_code == 1
    assert "not found" in missing.output


def test_delete_existing_and_missing_profile(project_id: int) -> None:
    profile_id, _ = create_profile(project_id)

    deleted = runner.invoke(app, ["search-profile", "delete", str(profile_id)])
    missing = runner.invoke(app, ["search-profile", "delete", str(profile_id)])

    assert deleted.exit_code == 0
    assert deleted.output.isascii()
    assert "OK:" in deleted.output
    assert missing.exit_code == 1
    assert missing.output.isascii()


def test_preview_default_templates_and_lowered_options(project_id: int) -> None:
    profile_id, _ = create_profile(
        project_id,
        "--country",
        "Germany",
        "--result-limit",
        "20",
        "--max-queries-per-run",
        "8",
        "--total-result-ceiling",
        "200",
    )

    result = runner.invoke(
        app,
        [
            "search-profile",
            "preview-queries",
            str(profile_id),
            "--max-queries",
            "1",
            "--result-limit-per-query",
            "5",
            "--total-result-ceiling",
            "50",
        ],
    )

    assert result.exit_code == 0
    assert "Query Count: 1" in result.output
    assert "Estimated Provider Requests: 1" in result.output
    assert "Result Limit Per Query: 5" in result.output
    assert "Total Result Ceiling: 50" in result.output
    assert "buyers Germany" in result.output


def test_preview_custom_template_and_global_profile(project_id: int) -> None:
    profile_id, _ = create_profile(
        project_id,
        "--query-template",
        "{product_or_service} for {target_customer_type}",
    )

    result = runner.invoke(app, ["search-profile", "preview-queries", str(profile_id)])

    assert result.exit_code == 0
    assert "Business offering for buyers" in result.output
    assert "Country: \n" in result.output
    assert "City: \n" in result.output


def test_preview_options_cannot_raise_profile_limits(project_id: int) -> None:
    profile_id, _ = create_profile(
        project_id,
        "--query-template",
        "{target_customer_type}",
        "--result-limit",
        "7",
        "--max-queries-per-run",
        "2",
        "--total-result-ceiling",
        "70",
    )
    result = runner.invoke(
        app,
        [
            "search-profile",
            "preview-queries",
            str(profile_id),
            "--max-queries",
            "99",
            "--result-limit-per-query",
            "99",
            "--total-result-ceiling",
            "999",
        ],
    )

    assert result.exit_code == 0
    assert "Result Limit Per Query: 7" in result.output
    assert "Total Result Ceiling: 70" in result.output
    assert "Limit: 7" in result.output


def test_preview_invalid_template_is_controlled(project_id: int) -> None:
    profile_id, _ = create_profile(project_id, "--query-template", "{unknown} buyers")

    result = runner.invoke(app, ["search-profile", "preview-queries", str(profile_id)])

    assert result.exit_code == 1
    assert "Query preview error:" in result.output
    assert "Traceback" not in result.output


def test_preview_does_not_use_network_or_execution_services(
    project_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id, _ = create_profile(project_id)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Preview attempted an execution side effect.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(
        "app.modules.company_discovery.service.CompanyDiscoveryService.discover_from_serpapi",
        forbidden,
    )
    monkeypatch.setattr(
        "app.modules.company_import.ingestion.CompanyIngestionService.ingest",
        forbidden,
    )

    result = runner.invoke(app, ["search-profile", "preview-queries", str(profile_id)])

    assert result.exit_code == 0, result.output


def test_session_closes_after_success_and_controlled_error(
    project_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    real_factory = SessionLocal

    class TrackingContext:
        def __init__(self) -> None:
            self.session = real_factory()

        def __enter__(self) -> Any:
            return self.session

        def __exit__(self, *args: object) -> None:
            self.session.close()
            closed.append(True)

    factory: Callable[[], TrackingContext] = TrackingContext
    monkeypatch.setattr(search_profile_cli, "SessionLocal", factory)

    success = runner.invoke(app, ["search-profile", "list", "--project-id", str(project_id)])
    missing = runner.invoke(app, ["search-profile", "show", "999999"])

    assert success.exit_code == 0
    assert missing.exit_code == 1
    assert closed == [True, True]
