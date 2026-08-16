from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from app.cli.agent import app
from app.modules.agent.lead_acquisition import (
    LeadAcquisitionExecutionError,
    LeadAcquisitionExportStatus,
    LeadAcquisitionFailureStage,
    LeadAcquisitionResult,
    LeadAcquisitionStatus,
)

runner = CliRunner()
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def result() -> LeadAcquisitionResult:
    return LeadAcquisitionResult(
        project_id=1,
        search_profile_id=3,
        requested_limit=1,
        completed_count=1,
        person_scoped_count=1,
        company_scoped_count=0,
        companies_created=1,
        companies_reused=0,
        contacts_created=1,
        contacts_reused=0,
        leads_created=1,
        leads_reused=0,
        tasks_created=1,
        tasks_reused=0,
        drafts_created=1,
        drafts_reused=0,
        duplicates_skipped=0,
        no_selection_count=0,
        no_contact_count=0,
        no_email_count=0,
        draft_failure_count=0,
        attempt_count=1,
        attempt_budget=10,
        budget_exhausted=False,
        discovery_run_count=1,
        candidate_count=5,
        completed_company_ids=(10,),
        completed_contact_ids=(20,),
        completed_lead_ids=(30,),
        completed_task_ids=(35,),
        completed_draft_ids=(40,),
        company_discovery_call_count=1,
        company_decision_call_count=1,
        contact_discovery_call_count=1,
        contact_decision_call_count=1,
        draft_generation_call_count=1,
        export_file=None,
        export_status=LeadAcquisitionExportStatus.NOT_REQUESTED,
        status=LeadAcquisitionStatus.COMPLETE,
    )


def test_help_exposes_required_contract() -> None:
    completed = runner.invoke(app, ["acquire-leads", "--help"], color=False)
    assert completed.exit_code == 0
    normalized_stdout = ANSI_ESCAPE.sub("", completed.stdout)
    for option in (
        "--project-id",
        "--search-profile-id",
        "--limit",
        "--goal",
        "--export-excel",
        "--overwrite-export",
        "--output",
    ):
        assert option in normalized_stdout


def test_json_output_is_deterministic_and_stdout_only(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.agent.execute_agent_lead_acquisition", lambda _data: result())
    completed = runner.invoke(
        app,
        [
            "acquire-leads",
            "--project-id",
            "1",
            "--search-profile-id",
            "3",
            "--limit",
            "1",
            "--goal",
            "Find design firms",
            "--output",
            "json",
        ],
        color=False,
    )
    assert completed.exit_code == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["completed_count"] == 1
    assert completed.stdout == completed.stdout.strip() + "\n"


def test_text_output_contains_operator_fields(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.agent.execute_agent_lead_acquisition", lambda _data: result())
    completed = runner.invoke(
        app,
        [
            "acquire-leads",
            "--project-id",
            "1",
            "--search-profile-id",
            "3",
            "--limit",
            "1",
            "--goal",
            "Find design firms",
        ],
        color=False,
    )
    assert completed.exit_code == 0
    assert "Requested: 1" in completed.stdout
    assert "Completed: 1" in completed.stdout
    assert "Status: COMPLETE" in completed.stdout


def test_limit_outside_one_to_fifty_is_rejected() -> None:
    for limit in ("0", "51"):
        completed = runner.invoke(
            app,
            [
                "acquire-leads",
                "--project-id",
                "1",
                "--search-profile-id",
                "3",
                "--limit",
                limit,
                "--goal",
                "Find design firms",
            ],
            color=False,
        )
        assert completed.exit_code == 2
        assert completed.stdout == ""
        assert completed.stderr == "Agent lead acquisition data is invalid.\n"


def test_nested_executor_value_error_is_safely_classified_without_sentinel(monkeypatch) -> None:
    sentinel = "SECRET_RUNTIME_VALUE api_key=abc"

    def fail(_data) -> None:
        raise ValueError(sentinel)

    monkeypatch.setattr("app.cli.agent.execute_agent_lead_acquisition", fail)
    completed = runner.invoke(
        app,
        [
            "acquire-leads",
            "--project-id",
            "1",
            "--search-profile-id",
            "3",
            "--limit",
            "1",
            "--goal",
            "Find design firms",
        ],
        color=False,
    )

    assert completed.exit_code == 1
    assert completed.stdout == ""
    assert completed.stderr == "Agent lead acquisition failed during execution.\n"
    assert "data is invalid" not in completed.stderr
    assert sentinel not in completed.output


def test_result_serialization_value_error_is_safely_classified(monkeypatch) -> None:
    sentinel = "SECRET_RESULT_VALUE api_key=abc"
    monkeypatch.setattr("app.cli.agent.execute_agent_lead_acquisition", lambda _data: result())

    def fail(_result, _output) -> str:
        raise ValueError(sentinel)

    monkeypatch.setattr("app.cli.agent.render_agent_lead_acquisition", fail)
    completed = runner.invoke(
        app,
        [
            "acquire-leads",
            "--project-id",
            "1",
            "--search-profile-id",
            "3",
            "--limit",
            "1",
            "--goal",
            "Find design firms",
        ],
        color=False,
    )

    assert completed.exit_code == 1
    assert completed.stdout == ""
    assert completed.stderr == "Agent lead acquisition failed during result serialization.\n"
    assert sentinel not in completed.output


def test_safe_runtime_error_contains_only_allowlisted_diagnostic() -> None:
    error = LeadAcquisitionExecutionError(LeadAcquisitionFailureStage.COMPANY_ENRICHMENT)

    assert error.category == "execution_error"
    assert error.failure_stage is LeadAcquisitionFailureStage.COMPANY_ENRICHMENT
    assert error.args == ("Agent lead acquisition failed during company enrichment.",)
    assert "SECRET" not in str(error)
    assert "SECRET" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_explicit_export_overwrite_flag_is_delegated(monkeypatch, tmp_path) -> None:
    captured = []

    def execute(data):
        captured.append(data)
        return result()

    monkeypatch.setattr("app.cli.agent.execute_agent_lead_acquisition", execute)
    completed = runner.invoke(
        app,
        [
            "acquire-leads",
            "--project-id",
            "1",
            "--search-profile-id",
            "3",
            "--limit",
            "1",
            "--goal",
            "Find design firms",
            "--export-excel",
            str(tmp_path / "crm.xlsx"),
            "--overwrite-export",
        ],
        color=False,
    )

    assert completed.exit_code == 0
    assert len(captured) == 1
    assert captured[0].overwrite_export is True
