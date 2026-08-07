import json

import pytest
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.modules.agent import (
    AgentCompanyPlanBindingError,
    AgentCompanyPlanDecisionError,
    AgentCompanyPlanDiscoveryDataError,
    AgentCompanyPlanInput,
    AgentCompanyPlanInternalError,
    AgentCompanyPlanPersistenceError,
    AgentCompanyPlanProjectNotFoundError,
    AgentCompanyPlanResult,
    AgentCompanyPlanSearchProfileNotReadyError,
    AgentCompanyPlanSearchProviderError,
    AgentCompanyPlanSelectionError,
)
from app.modules.company_discovery.models import CompanyDiscoveryRunStatus
from app.providers.openai_decision import OpenAICompanyFit, OpenAIDecisionKind
from tests.cli_output import plain_cli_output

runner = CliRunner()


def result(*, selected: bool = True) -> AgentCompanyPlanResult:
    return AgentCompanyPlanResult(
        project_id=1,
        search_profile_id=2,
        discovery_run_id=3,
        query="café\ncompany",
        discovery_run_status=CompanyDiscoveryRunStatus.SUCCEEDED,
        staged_candidate_count=1,
        eligible_candidate_count=1,
        decision=OpenAIDecisionKind.SELECT if selected else None,
        selected_candidate_id=41 if selected else None,
        selected_candidate_index=1 if selected else None,
        confidence=0.9 if selected else None,
        company_fit=OpenAICompanyFit.HIGH if selected else None,
        rationale="Strong fit" if selected else None,
        next_action_title="Review" if selected else None,
        next_action_description="Review before outreach" if selected else None,
        human_review_required=True if selected else None,
        serpapi_call_count=1,
        openai_call_count=1 if selected else 0,
        crm_mutated=False,
        candidate_promoted=False,
    )


def invoke(*extra: str) -> object:
    return runner.invoke(
        app,
        [
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--search-profile-id",
            "2",
            "--goal",
            "Choose",
            *extra,
        ],
    )


def test_hierarchy_and_required_options() -> None:
    root = runner.invoke(app, ["agent", "--help"])
    subgroup = runner.invoke(app, ["agent", "company-select", "--help"])
    command = runner.invoke(app, ["agent", "company-select", "plan", "--help"])
    assert root.exit_code == subgroup.exit_code == command.exit_code == 0
    assert "company-select" in root.stdout
    assert "plan" in subgroup.stdout
    output = plain_cli_output(command.stdout)
    for option in ("--project-id", "--search-profile-id", "--goal", "--output"):
        assert option in output
    for forbidden in ("--api-key", "--query", "--yes", "--provider", "--model"):
        assert forbidden not in output


def test_default_text_output_is_ordered_scalar_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_cli, "execute_agent_company_plan", lambda data: result())
    outcome = invoke()
    assert outcome.exit_code == 0
    lines = outcome.stdout.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == list(AgentCompanyPlanResult.model_fields)
    assert 'query="café\\ncompany"' in lines
    assert lines[-2:] == ["crm_mutated=false", "candidate_promoted=false"]


def test_json_output_is_compact_sorted_unicode_and_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = result()
    monkeypatch.setattr(agent_cli, "execute_agent_company_plan", lambda data: expected)
    outcome = invoke("--output", "json")
    assert outcome.exit_code == 0
    expected_line = json.dumps(
        expected.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert outcome.stdout == expected_line + "\n"
    assert set(json.loads(outcome.stdout)) == set(AgentCompanyPlanResult.model_fields)


def test_null_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cli, "execute_agent_company_plan", lambda data: result(selected=False)
    )
    outcome = invoke()
    assert outcome.exit_code == 0
    assert "decision=null\n" in outcome.stdout
    assert "selected_candidate_id=null\n" in outcome.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ("--project-id", "bad"),
        ("--search-profile-id", "0"),
        ("--goal", "   "),
        ("--goal", "x" * 1001),
        ("--output", "yaml"),
    ],
)
def test_invalid_input_has_fixed_exit_two(extra: tuple[str, str]) -> None:
    base = [
        "agent",
        "company-select",
        "plan",
        "--project-id",
        "1",
        "--search-profile-id",
        "2",
        "--goal",
        "Choose",
    ]
    if extra[0] == "--output":
        base.extend(extra)
    else:
        option = base.index(extra[0])
        base[option + 1] = extra[1]
    outcome = runner.invoke(app, base)
    assert outcome.exit_code == 2
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent company plan data is invalid.\n"


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            AgentCompanyPlanProjectNotFoundError("Project was not found."),
            3,
            "Project was not found.",
        ),
        (
            AgentCompanyPlanSearchProfileNotReadyError(
                "Search profile is not ready for agent planning."
            ),
            4,
            "Search profile is not ready for agent planning.",
        ),
        (
            AgentCompanyPlanSearchProviderError("Company search provider failed."),
            5,
            "Company search provider failed.",
        ),
        (
            AgentCompanyPlanDiscoveryDataError("Company discovery results are invalid."),
            6,
            "Company discovery results are invalid.",
        ),
        (
            AgentCompanyPlanPersistenceError("Company discovery state could not be persisted."),
            7,
            "Company discovery state could not be persisted.",
        ),
        (
            AgentCompanyPlanSelectionError("Agent company selection failed."),
            8,
            "Agent company selection failed.",
        ),
        (
            AgentCompanyPlanDecisionError("Company decision provider failed."),
            9,
            "Company decision provider failed.",
        ),
        (
            AgentCompanyPlanBindingError("Agent company decision binding is inconsistent."),
            10,
            "Agent company decision binding is inconsistent.",
        ),
        (
            AgentCompanyPlanInternalError("Agent company plan failed."),
            1,
            "Agent company plan failed.",
        ),
    ],
)
def test_controlled_error_exit_mapping(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: int,
    message: str,
) -> None:
    def fail(data: object) -> AgentCompanyPlanResult:
        raise error

    monkeypatch.setattr(agent_cli, "execute_agent_company_plan", fail)
    outcome = invoke()
    assert outcome.exit_code == code
    assert outcome.stdout == ""
    assert outcome.stderr == message + "\n"
    assert "Traceback" not in outcome.stderr


def test_unexpected_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(data: object) -> AgentCompanyPlanResult:
        raise RuntimeError("API key and SQL")

    monkeypatch.setattr(agent_cli, "execute_agent_company_plan", fail)
    outcome = invoke()
    assert outcome.exit_code == 1
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent company plan failed.\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ("agent", "company-select", "plan"),
        ("agent", "company-select", "plan", "--project-id", "1"),
        (
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--search-profile-id",
            "2",
        ),
        (*("agent", "company-select", "plan"), "--unknown"),
        (*("agent", "company-select", "plan"), "unexpected"),
        (*("agent", "company-select", "plan"), "--project-id"),
        (
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--project-id",
            "2",
            "--search-profile-id",
            "2",
            "--goal",
            "Choose",
        ),
        (
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--search-profile-id",
            "2",
            "--search-profile-id",
            "3",
            "--goal",
            "Choose",
        ),
        (
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--search-profile-id",
            "2",
            "--goal",
            "Choose",
            "--goal",
            "Again",
        ),
        (
            "agent",
            "company-select",
            "plan",
            "--project-id",
            "1",
            "--search-profile-id",
            "2",
            "--goal",
            "Choose",
            "--output",
            "text",
            "--output=json",
        ),
    ],
)
def test_framework_parse_errors_are_command_local_and_sanitized(
    arguments: tuple[str, ...],
) -> None:
    outcome = runner.invoke(app, list(arguments))

    assert outcome.exit_code == 2
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent company plan data is invalid.\n"
    assert "Usage:" not in outcome.stderr
    assert "Traceback" not in outcome.stderr


@pytest.mark.parametrize(
    "changes",
    [
        {"crm_mutated": True},
        {"candidate_promoted": True},
        {"serpapi_call_count": 99},
        {"openai_call_count": 2},
        {"query": "unsafe\ud800"},
        {"human_review_required": False},
    ],
)
def test_renderer_deeply_rejects_model_construct_bypass(changes: dict[str, object]) -> None:
    values = result().model_dump()
    values.update(changes)
    bypassed = AgentCompanyPlanResult.model_construct(**values)

    with pytest.raises(AgentCompanyPlanInternalError) as caught:
        agent_cli.render_agent_company_plan(bypassed, "json")

    assert str(caught.value) == "Agent company plan failed."
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


class _SessionSpy:
    def __init__(self) -> None:
        self.rollbacks = 0
        self.closes = 0

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def _patch_execute_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_cli, "CompanyDiscoveryStagingRepository", lambda session: object())
    monkeypatch.setattr(agent_cli, "SearchProfileQueryGenerator", lambda: object())
    monkeypatch.setattr(agent_cli, "ProjectRepository", lambda session: object())
    monkeypatch.setattr(agent_cli, "SearchProfileRepository", lambda session: object())
    monkeypatch.setattr(agent_cli, "SearchProfileService", lambda repository: object())
    monkeypatch.setattr(agent_cli, "CompanyDiscoveryStagingService", lambda **kwargs: object())
    monkeypatch.setattr(agent_cli, "AgentCompanySelectionService", lambda repository: object())


def test_provider_factory_failure_rolls_back_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionSpy()
    _patch_execute_construction(monkeypatch)

    def fail_provider(**kwargs: object) -> object:
        raise RuntimeError("secret provider configuration")

    with pytest.raises(AgentCompanyPlanSearchProviderError) as caught:
        agent_cli.execute_agent_company_plan(
            AgentCompanyPlanInput(project_id=1, search_profile_id=2, goal="Choose"),
            session_factory=lambda: session,  # type: ignore[arg-type]
            serpapi_client_factory=fail_provider,  # type: ignore[arg-type]
        )

    assert str(caught.value) == "Company search provider failed."
    assert session.rollbacks == session.closes == 1


def test_decision_factory_is_lazy_and_failure_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _SessionSpy()
    _patch_execute_construction(monkeypatch)
    factory_calls = 0

    class FakePlanService:
        def __init__(self, **kwargs: object) -> None:
            self.decision_factory = kwargs["decision_factory"]

        def plan(self, data: object) -> AgentCompanyPlanResult:
            self.decision_factory()  # type: ignore[operator]
            raise AssertionError("unreachable")

    def fail_factory() -> object:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("secret OpenAI configuration")

    monkeypatch.setattr(agent_cli, "SerpApiDiscoveryProvider", lambda client: object())
    monkeypatch.setattr(agent_cli, "AgentCompanyPlanService", FakePlanService)

    with pytest.raises(AgentCompanyPlanDecisionError) as caught:
        agent_cli.execute_agent_company_plan(
            AgentCompanyPlanInput(project_id=1, search_profile_id=2, goal="Choose"),
            session_factory=lambda: session,  # type: ignore[arg-type]
            decision_factory_factory=fail_factory,  # type: ignore[arg-type]
            serpapi_client_factory=lambda **kwargs: object(),  # type: ignore[arg-type]
        )

    assert str(caught.value) == "Company decision provider failed."
    assert factory_calls == 1
    assert session.rollbacks == session.closes == 1
