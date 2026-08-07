import json

import pytest
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.modules.agent import (
    AgentContactApplyConflictError,
    AgentContactApplyConsistencyError,
    AgentContactApplyInput,
    AgentContactApplyInternalError,
    AgentContactApplyNotEligibleError,
    AgentContactApplyNotFoundError,
    AgentContactApplyPersistenceError,
    AgentContactApplyResult,
    AgentContactApplyStaleHandoffError,
)
from app.modules.contact_discovery.models import ContactDiscoveryCandidateStatus

runner = CliRunner()
COMMAND = ["agent", "contact-select", "apply"]
TOKEN = "a" * 64
VALID = [
    "--project-id",
    "1",
    "--company-id",
    "2",
    "--candidate-id",
    "3",
    "--goal",
    "Create a qualified outreach lead",
    "--handoff-token",
    TOKEN,
]
INVALID = "Agent contact apply data is invalid.\n"


def _result() -> AgentContactApplyResult:
    return AgentContactApplyResult(
        project_id=1,
        company_id=2,
        candidate_id=3,
        contact_id=4,
        lead_id=5,
        task_id=6,
        candidate_status_before=ContactDiscoveryCandidateStatus.DISCOVERED,
        candidate_status_after=ContactDiscoveryCandidateStatus.PROMOTED,
        candidate_reviewed=True,
        candidate_promoted=True,
        contact_created=True,
        contact_reused=False,
        lead_created=True,
        lead_reused=False,
        task_created=True,
        task_reused=False,
        staging_mutated=True,
        crm_mutated=True,
        network_call_count=0,
        contact_mutation_count=1,
        lead_mutation_count=1,
        task_mutation_count=1,
        handoff_verified=True,
        human_confirmation_required=True,
        human_confirmation_received=True,
    )


def _invoke(*extra: str):
    return runner.invoke(app, COMMAND + VALID + list(extra))


def test_successful_text_json_and_default_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[AgentContactApplyInput, str]] = []

    def execute(data: AgentContactApplyInput, output: str) -> str:
        captured.append((data, output))
        return agent_cli.render_agent_contact_apply(_result(), output)

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", execute)
    text = _invoke("--yes")
    payload = _invoke("--yes", "--output", "json")
    assert text.exit_code == payload.exit_code == 0
    assert [line.split("=", 1)[0] for line in text.stdout.splitlines()] == list(
        AgentContactApplyResult.model_fields
    )
    assert (
        payload.stdout
        == json.dumps(
            _result().model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert [output for _, output in captured] == ["text", "json"]
    assert captured[0][0] == AgentContactApplyInput(
        project_id=1,
        company_id=2,
        candidate_id=3,
        goal="Create a qualified outreach lead",
        handoff_token=TOKEN,
        confirmed=True,
    )
    assert type(captured[0][0].confirmed) is bool and captured[0][0].confirmed is True


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--project-id"],
        ["--company-id"],
        ["--candidate-id"],
        ["--goal"],
        ["--handoff-token"],
        VALID + ["--yes", "--output"],
        VALID + ["--yes", "--unknown"],
        VALID + ["--yes", "positional"],
    ],
)
def test_parser_failures_are_sanitized_before_execution(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    session_calls = service_calls = apply_calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal session_calls, service_calls, apply_calls
        session_calls += 1
        service_calls += 1
        apply_calls += 1
        return "forbidden"

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", forbidden)
    outcome = runner.invoke(app, COMMAND + arguments)
    assert outcome.exit_code == 2
    assert outcome.stdout == ""
    assert outcome.stderr == INVALID
    assert "Usage" not in outcome.stderr and "Traceback" not in outcome.stderr
    assert (session_calls, service_calls, apply_calls) == (0, 0, 0)


@pytest.mark.parametrize(
    "option",
    ["--project-id", "--company-id", "--candidate-id", "--goal", "--handoff-token"],
)
def test_each_required_option_is_independently_required_before_execution(
    monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "forbidden"

    arguments = VALID.copy()
    index = arguments.index(option)
    del arguments[index : index + 2]
    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", forbidden)
    outcome = runner.invoke(app, COMMAND + arguments + ["--yes"])
    assert outcome.exit_code == 2
    assert outcome.stdout == "" and outcome.stderr == INVALID
    assert calls == 0


def test_missing_and_malformed_confirmation_precede_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "forbidden"

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", forbidden)
    for arguments in (VALID, VALID + ["--yes=true"]):
        outcome = runner.invoke(app, COMMAND + arguments)
        assert outcome.exit_code == 3
        assert outcome.stdout == ""
        assert outcome.stderr == "Agent contact apply requires --yes.\n"
    assert calls == 0


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--project-id", "9"),
        ("--company-id", "9"),
        ("--candidate-id", "9"),
        ("--goal", "Another goal"),
        ("--handoff-token", "b" * 64),
        ("--output", "json"),
        ("--yes", None),
    ],
)
def test_duplicate_options_are_rejected(option: str, value: str | None) -> None:
    if option == "--output":
        duplicate = ["--output", "text", "--output", "json"]
    else:
        duplicate = [option] if value is None else [option, value]
    outcome = _invoke("--yes", *duplicate)
    assert outcome.exit_code == 2
    assert outcome.stdout == "" and outcome.stderr == INVALID


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--project-id", "x"),
        ("--project-id", "0"),
        ("--project-id", "-1"),
        ("--company-id", "x"),
        ("--company-id", "0"),
        ("--company-id", "-1"),
        ("--candidate-id", "x"),
        ("--candidate-id", "0"),
        ("--candidate-id", "-1"),
        ("--goal", " "),
        ("--goal", "x" * 2001),
        ("--handoff-token", "x"),
        ("--handoff-token", "A" * 64),
        ("--handoff-token", f"{TOKEN[:-1]} "),
        ("--output", "yaml"),
    ],
)
def test_invalid_values_are_rejected_without_transforming_input(option: str, value: str) -> None:
    arguments = VALID.copy()
    if option == "--output":
        arguments.extend((option, value))
    else:
        index = arguments.index(option)
        arguments[index + 1] = value
    outcome = runner.invoke(app, COMMAND + arguments + ["--yes"])
    assert outcome.exit_code == 2
    assert outcome.stdout == "" and outcome.stderr == INVALID


def test_help_is_valid_and_lists_required_contract() -> None:
    outcome = runner.invoke(app, COMMAND + ["--help"])
    assert outcome.exit_code == 0
    for option in (
        "--project-id",
        "--company-id",
        "--candidate-id",
        "--goal",
        "--handoff-token",
        "--yes",
        "--output",
    ):
        assert option in outcome.stdout


def test_renderer_is_exact_strict_compact_and_unicode() -> None:
    result = _result()
    text = agent_cli.render_agent_contact_apply(result, "text")
    assert [line.split("=", 1)[0] for line in text.splitlines()] == list(
        AgentContactApplyResult.model_fields
    )
    assert 'candidate_status_before="DISCOVERED"' in text
    payload = agent_cli.render_agent_contact_apply(result, "json")
    assert payload == json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "\\u" not in payload


def test_renderer_rejects_model_construct_bypass() -> None:
    invalid = AgentContactApplyResult.model_construct(**_result().model_dump())
    object.__setattr__(invalid, "network_call_count", 1)
    with pytest.raises(AgentContactApplyInternalError, match="Agent contact apply failed"):
        agent_cli.render_agent_contact_apply(invalid, "json")


class _Session:
    def __init__(self, *, commit_error: BaseException | None = None) -> None:
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def _install_executor(
    monkeypatch: pytest.MonkeyPatch,
    session: _Session,
    service_result: object,
) -> tuple[list[AgentContactApplyInput], dict[str, object]]:
    dependencies: dict[str, object] = {}
    for name in (
        "CompanyRepository",
        "ContactRepository",
        "ContactDiscoveryRepository",
        "ContactDiscoveryCandidateReviewService",
        "ContactDiscoveryCandidatePromotionService",
        "LeadRepository",
        "TaskRepository",
    ):
        monkeypatch.setattr(agent_cli, name, lambda *args, _name=name: f"{_name}-instance")
    received: list[AgentContactApplyInput] = []

    class Service:
        def __init__(self, **values: object) -> None:
            dependencies.update(values)

        def apply(self, data: AgentContactApplyInput) -> object:
            received.append(data)
            if isinstance(service_result, BaseException):
                raise service_result
            return service_result

    monkeypatch.setattr(agent_cli, "AgentContactApplyService", Service)
    monkeypatch.setattr(agent_cli, "SessionLocal", lambda: session)
    return received, dependencies


def _data() -> AgentContactApplyInput:
    return AgentContactApplyInput(
        project_id=1,
        company_id=2,
        candidate_id=3,
        goal="Goal",
        handoff_token=TOKEN,
        confirmed=True,
    )


def test_executor_composes_service_applies_once_and_commits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    received, dependencies = _install_executor(monkeypatch, session, _result())
    rendered = agent_cli._execute_agent_contact_apply(
        _data(), "json", session_factory=lambda: session
    )
    assert json.loads(rendered)["contact_id"] == 4
    assert received == [_data()]
    assert set(dependencies) == {
        "company_repository",
        "contact_repository",
        "discovery_repository",
        "review_service",
        "promotion_service",
        "lead_repository",
        "task_repository",
    }
    assert (session.commits, session.rollbacks, session.closes) == (1, 0, 1)


@pytest.mark.parametrize(
    "error",
    [AgentContactApplyNotFoundError("Agent contact apply target was not found."), RuntimeError()],
)
def test_executor_rolls_back_known_and_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    session = _Session()
    _install_executor(monkeypatch, session, error)
    with pytest.raises(type(error)):
        agent_cli._execute_agent_contact_apply(_data(), "text", session_factory=lambda: session)
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


def test_commit_and_invalid_result_fail_without_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_session = _Session(commit_error=RuntimeError("secret"))
    _install_executor(monkeypatch, commit_session, _result())
    with pytest.raises(AgentContactApplyPersistenceError):
        agent_cli._execute_agent_contact_apply(
            _data(), "text", session_factory=lambda: commit_session
        )
    assert (commit_session.commits, commit_session.rollbacks, commit_session.closes) == (1, 1, 1)

    invalid_session = _Session()
    invalid = _result().model_copy()
    object.__setattr__(invalid, "task_mutation_count", 0)
    _install_executor(monkeypatch, invalid_session, invalid)
    with pytest.raises(AgentContactApplyInternalError):
        agent_cli._execute_agent_contact_apply(
            _data(), "json", session_factory=lambda: invalid_session
        )
    assert (invalid_session.commits, invalid_session.rollbacks, invalid_session.closes) == (0, 1, 1)


def test_custom_base_exception_is_cleaned_up_and_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop(BaseException):
        pass

    error = Stop()
    session = _Session()
    _install_executor(monkeypatch, session, error)
    with pytest.raises(Stop) as captured:
        agent_cli._execute_agent_contact_apply(_data(), "text", session_factory=lambda: session)
    assert captured.value is error
    assert (session.commits, session.rollbacks, session.closes) == (0, 1, 1)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AgentContactApplyInternalError("Agent contact apply failed."), 1),
        (AgentContactApplyNotFoundError("Agent contact apply target was not found."), 4),
        (AgentContactApplyStaleHandoffError("Agent contact apply handoff is stale."), 5),
        (AgentContactApplyNotEligibleError("Agent contact apply candidate is not eligible."), 6),
        (AgentContactApplyConsistencyError("Agent contact apply state is inconsistent."), 7),
        (AgentContactApplyConflictError("Agent contact apply found conflicting CRM state."), 8),
        (AgentContactApplyPersistenceError("Agent contact apply could not be persisted."), 9),
    ],
)
def test_domain_errors_have_fixed_exit_codes_and_messages(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    def execute(data: AgentContactApplyInput, output: str) -> str:
        raise error

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", execute)
    outcome = _invoke("--yes")
    assert outcome.exit_code == code
    assert outcome.stdout == ""
    assert outcome.stderr == f"{error}\n"
    assert "Traceback" not in outcome.stderr


def test_unexpected_public_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(data: AgentContactApplyInput, output: str) -> str:
        raise RuntimeError("database URL and API key")

    monkeypatch.setattr(agent_cli, "_execute_agent_contact_apply", execute)
    outcome = _invoke("--yes")
    assert outcome.exit_code == 1
    assert outcome.stdout == ""
    assert outcome.stderr == "Agent contact apply failed.\n"
