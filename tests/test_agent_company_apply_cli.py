import json
from typing import Any

import pytest
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.modules.agent import (
    AgentCompanyApplyConflictError,
    AgentCompanyApplyConsistencyError,
    AgentCompanyApplyInput,
    AgentCompanyApplyInternalError,
    AgentCompanyApplyNotEligibleError,
    AgentCompanyApplyNotFoundError,
    AgentCompanyApplyPersistenceError,
    AgentCompanyApplyResult,
    AgentCompanyApplyStaleHandoffError,
)
from app.modules.company_discovery.models import CompanyDiscoveryCandidateStatus

runner = CliRunner()
COMMAND = ["agent", "company-select", "apply"]
VALID = [
    "--project-id",
    "1",
    "--discovery-run-id",
    "2",
    "--candidate-id",
    "3",
]


def _result() -> AgentCompanyApplyResult:
    return AgentCompanyApplyResult(
        project_id=1,
        discovery_run_id=2,
        candidate_id=3,
        company_id=4,
        candidate_status_before=CompanyDiscoveryCandidateStatus.DISCOVERED,
        candidate_status_after=CompanyDiscoveryCandidateStatus.PROMOTED,
        company_created=True,
        company_reused=False,
        candidate_reviewed=True,
        candidate_promoted=True,
        crm_mutated=True,
        network_call_count=0,
        contact_mutation_count=0,
        lead_mutation_count=0,
        task_mutation_count=0,
        human_confirmation_required=True,
        human_confirmation_received=True,
    )


def test_apply_command_is_registered() -> None:
    result = runner.invoke(app, ["agent", "company-select", "--help"])
    assert result.exit_code == 0
    assert "apply" in result.stdout


def test_missing_confirmation_exits_before_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_cli,
        "_execute_agent_company_apply",
        lambda *args, **kwargs: pytest.fail("executor called"),
    )
    result = runner.invoke(app, COMMAND + VALID)
    assert result.exit_code == 3
    assert result.stderr == "Agent company apply requires --yes.\n"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--project-id", "0", "--discovery-run-id", "2", "--candidate-id", "3", "--yes"],
        ["--project-id", "x", "--discovery-run-id", "2", "--candidate-id", "3", "--yes"],
        VALID + ["--yes", "--output", "yaml"],
        VALID + ["--yes", "--project-id", "1"],
        VALID + ["--yes", "unexpected"],
    ],
)
def test_invalid_cli_input_has_fixed_exit(arguments: list[str]) -> None:
    result = runner.invoke(app, COMMAND + arguments)
    assert result.exit_code == 2
    assert result.stderr == "Agent company apply data is invalid.\n"


@pytest.mark.parametrize("output", ["text", "json"])
def test_valid_command_forwards_confirmed_input_and_output(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    captured: dict[str, Any] = {}

    def execute(data: AgentCompanyApplyInput, selected_output: str) -> str:
        captured.update(data=data, output=selected_output)
        return "rendered"

    monkeypatch.setattr(agent_cli, "_execute_agent_company_apply", execute)
    result = runner.invoke(app, COMMAND + VALID + ["--yes", "--output", output])
    assert result.exit_code == 0
    assert result.stdout == "rendered\n"
    assert captured["data"] == AgentCompanyApplyInput(
        project_id=1, discovery_run_id=2, candidate_id=3, confirmed=True
    )
    assert captured["output"] == output


def test_domain_error_uses_fixed_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(data: AgentCompanyApplyInput, output: str) -> str:
        raise AgentCompanyApplyNotFoundError("Agent company apply target was not found.")

    monkeypatch.setattr(agent_cli, "_execute_agent_company_apply", execute)
    result = runner.invoke(app, COMMAND + VALID + ["--yes"])
    assert result.exit_code == 4
    assert result.stderr == "Agent company apply target was not found.\n"


def test_json_renderer_is_exact_sorted_compact_json() -> None:
    rendered = agent_cli.render_agent_company_apply(_result(), "json")
    assert rendered == json.dumps(
        _result().model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_text_renderer_uses_schema_order_and_json_scalars() -> None:
    result = _result()
    lines = agent_cli.render_agent_company_apply(result, "text").splitlines()
    assert [line.split("=", 1)[0] for line in lines] == list(type(result).model_fields)
    assert lines[0] == "project_id=1"
    assert "human_confirmation_received=true" in lines


def test_renderer_revalidates_constructed_result() -> None:
    invalid = AgentCompanyApplyResult.model_construct(**_result().model_dump())
    object.__setattr__(invalid, "network_call_count", 1)
    with pytest.raises(Exception, match="Agent company apply failed"):
        agent_cli.render_agent_company_apply(invalid, "json")


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--project-id", "1"),
        ("--discovery-run-id", "2"),
        ("--candidate-id", "3"),
        ("--yes", None),
        ("--output", "text"),
    ],
)
def test_every_duplicate_option_is_rejected(option: str, value: str | None) -> None:
    if option == "--output":
        duplicate = ["--output", "text", "--output", "json"]
    else:
        duplicate = [option] if value is None else [option, value]
    result = runner.invoke(app, COMMAND + VALID + ["--yes"] + duplicate)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Agent company apply data is invalid.\n"
    assert "Usage" not in result.stderr and "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "option", ["--project-id", "--discovery-run-id", "--candidate-id", "--output"]
)
def test_every_missing_option_value_is_rejected(option: str) -> None:
    result = runner.invoke(app, COMMAND + VALID + ["--yes", option])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Agent company apply data is invalid.\n"


@pytest.mark.parametrize("value", ["-1", "true", "1.0"])
def test_additional_invalid_identifier_forms_are_rejected(value: str) -> None:
    arguments = VALID.copy()
    arguments[1] = value
    result = runner.invoke(app, COMMAND + arguments + ["--yes"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Agent company apply data is invalid.\n"


def test_apply_help_is_successful_without_confirmation() -> None:
    result = runner.invoke(app, COMMAND + ["--help"])
    assert result.exit_code == 0
    assert "--yes" in result.stdout


def test_unknown_option_is_rejected_before_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    constructions = 0

    def execute(*args: object, **kwargs: object) -> str:
        nonlocal constructions
        constructions += 1
        return "unexpected"

    monkeypatch.setattr(agent_cli, "_execute_agent_company_apply", execute)
    result = runner.invoke(app, COMMAND + VALID + ["--yes", "--unknown-option"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Agent company apply data is invalid.\n"
    assert "Usage" not in result.stderr and "Traceback" not in result.stderr
    assert constructions == 0


class _PublicBoundarySession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def _install_public_executor_boundary(
    monkeypatch: pytest.MonkeyPatch,
    service_result: object,
    session: _PublicBoundarySession,
) -> list[int]:
    monkeypatch.setattr(
        agent_cli, "CompanyDiscoveryStagingRepository", lambda active_session: object()
    )
    monkeypatch.setattr(agent_cli, "CompanyRepository", lambda active_session: object())
    monkeypatch.setattr(
        agent_cli, "CompanyDiscoveryCandidateReviewService", lambda repository: object()
    )
    monkeypatch.setattr(
        agent_cli,
        "CompanyDiscoveryCandidatePromotionService",
        lambda staging, companies: object(),
    )

    class Service:
        def __init__(self, **dependencies: object) -> None:
            assert len(dependencies) == 4

        def apply(self, data: AgentCompanyApplyInput) -> object:
            if isinstance(service_result, BaseException):
                raise service_result
            return service_result

    monkeypatch.setattr(agent_cli, "AgentCompanyApplyService", Service)
    real_execute = agent_cli._execute_agent_company_apply
    constructions = [0]

    def execute(data: AgentCompanyApplyInput, output: str) -> str:
        constructions[0] += 1
        return real_execute(data, output, session_factory=lambda: session)  # type: ignore[arg-type]

    monkeypatch.setattr(agent_cli, "_execute_agent_company_apply", execute)
    return constructions


def test_public_unexpected_executor_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PublicBoundarySession()
    constructions = _install_public_executor_boundary(
        monkeypatch, RuntimeError("unexpected secret"), session
    )
    result = runner.invoke(app, COMMAND + VALID + ["--yes"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Agent company apply failed.\n"
    assert "Usage" not in result.stderr and "Traceback" not in result.stderr
    assert "unexpected secret" not in result.stderr
    assert constructions == [1]
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


@pytest.mark.parametrize(
    "hostile_payload",
    ["\ud800", "line\nfeed", "carriage\rreturn", "tab\tvalue", "control\x01value"],
)
def test_public_real_renderer_rejects_hostile_result_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    hostile_payload: str,
) -> None:
    hostile = _result().model_copy()
    object.__setattr__(hostile, "company_id", hostile_payload)
    session = _PublicBoundarySession()
    constructions = _install_public_executor_boundary(monkeypatch, hostile, session)
    result = runner.invoke(app, COMMAND + VALID + ["--yes"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Agent company apply failed.\n"
    assert "Usage" not in result.stderr and "Traceback" not in result.stderr
    assert hostile_payload not in result.stderr
    assert constructions == [1]
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            AgentCompanyApplyInternalError("Agent company apply failed."),
            1,
            "Agent company apply failed.",
        ),
        (
            AgentCompanyApplyNotFoundError("Agent company apply target was not found."),
            4,
            "Agent company apply target was not found.",
        ),
        (
            AgentCompanyApplyStaleHandoffError("Agent company apply handoff is stale."),
            5,
            "Agent company apply handoff is stale.",
        ),
        (
            AgentCompanyApplyNotEligibleError("Agent company apply run is not eligible."),
            6,
            "Agent company apply run is not eligible.",
        ),
        (
            AgentCompanyApplyConsistencyError("Agent company apply state is inconsistent."),
            7,
            "Agent company apply state is inconsistent.",
        ),
        (
            AgentCompanyApplyConflictError("Agent company apply persistence conflict."),
            8,
            "Agent company apply persistence conflict.",
        ),
        (
            AgentCompanyApplyPersistenceError("Agent company apply could not be persisted."),
            9,
            "Agent company apply could not be persisted.",
        ),
    ],
)
def test_complete_domain_exit_code_matrix(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: int,
    message: str,
) -> None:
    def execute(data: AgentCompanyApplyInput, output: str) -> str:
        raise error

    monkeypatch.setattr(agent_cli, "_execute_agent_company_apply", execute)
    result = runner.invoke(app, COMMAND + VALID + ["--yes"])
    assert result.exit_code == code
    assert result.stdout == ""
    assert result.stderr == f"{message}\n"
    assert "Usage" not in result.stderr and "Traceback" not in result.stderr


def test_plan_help_has_no_apply_confirmation_requirement() -> None:
    result = runner.invoke(app, ["agent", "company-select", "plan", "--help"])
    assert result.exit_code == 0
    assert "requires --yes" not in result.stdout
