import json
from typing import Any

import pytest
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.modules.agent import (
    AgentCompanyApplyInput,
    AgentCompanyApplyNotFoundError,
    AgentCompanyApplyResult,
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
