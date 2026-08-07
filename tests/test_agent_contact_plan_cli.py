import json

import pytest
from typer.testing import CliRunner

import app.cli.agent as agent_cli
from app.cli.main import app
from app.modules.agent.contact_plan import (
    AgentContactPlanInternalError,
    AgentContactPlanProjectNotFoundError,
)
from app.modules.agent.contact_plan_schemas import (
    AgentContactDecision,
    AgentContactDiscoveryStatus,
    AgentContactPlanResult,
)
from app.modules.contact_discovery.models import ContactDiscoverySourceType
from tests.cli_output import plain_cli_output

runner = CliRunner()


def result() -> AgentContactPlanResult:
    return AgentContactPlanResult(
        project_id=1,
        company_id=2,
        company_name="Meyer Davis",
        company_website="https://example.com",
        goal="Find café partner",
        decision=AgentContactDecision.SELECT,
        discovery_status=AgentContactDiscoveryStatus.SUCCEEDED,
        provider_name="website",
        provider_call_count=1,
        attempted_pages=2,
        successful_pages=2,
        selected_urls=1,
        limited_link_scan=False,
        candidate_upsert_count=1,
        staged_candidate_count=1,
        eligible_candidate_count=1,
        selected_candidate_id=3,
        selected_contact_name="Zoë",
        selected_contact_title="Founder",
        selected_contact_email=None,
        selected_contact_phone=None,
        selected_contact_source_url="https://example.com/team",
        selected_contact_source_type=ContactDiscoverySourceType.TEAM_PAGE,
        selected_contact_confidence=0.8,
        selection_rationale="Best deterministic fit",
        proposed_lead_title="Bohemia Bali partnership — Meyer Davis",
        proposed_task_title="Review and prepare outreach to Zoë",
        proposed_task_description="A human must verify Zoë; no outreach has been sent.",
        handoff_token="a" * 64,
        human_review_required=True,
        staging_mutated=True,
        contact_mutation_count=0,
        lead_mutation_count=0,
        task_mutation_count=0,
    )


def no_selection_result() -> AgentContactPlanResult:
    values = result().model_dump()
    values.update(
        decision=AgentContactDecision.NO_SELECTION,
        discovery_status=AgentContactDiscoveryStatus.NOT_FOUND,
        candidate_upsert_count=0,
        staged_candidate_count=0,
        eligible_candidate_count=0,
        selected_candidate_id=None,
        selected_contact_name=None,
        selected_contact_title=None,
        selected_contact_email=None,
        selected_contact_phone=None,
        selected_contact_source_url=None,
        selected_contact_source_type=None,
        selected_contact_confidence=None,
        selection_rationale="No eligible candidate",
        proposed_lead_title=None,
        proposed_task_title=None,
        proposed_task_description=None,
        handoff_token=None,
    )
    return AgentContactPlanResult.model_validate(values)


def invoke(*extra: str):
    return runner.invoke(
        app,
        [
            "agent",
            "contact-select",
            "plan",
            "--project-id",
            "1",
            "--company-id",
            "2",
            "--goal",
            "Find partner",
            *extra,
        ],
    )


def test_help_exposes_contact_plan_options() -> None:
    root = runner.invoke(app, ["agent", "--help"])
    group = runner.invoke(app, ["agent", "contact-select", "--help"])
    command = runner.invoke(app, ["agent", "contact-select", "plan", "--help"])
    assert root.exit_code == group.exit_code == command.exit_code == 0
    assert "contact-select" in root.stdout and "plan" in group.stdout
    output = plain_cli_output(command.stdout)
    for option in ("--project-id", "--company-id", "--goal", "--output"):
        assert option in output


def test_text_and_json_output_are_exact_ordered_and_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_cli, "execute_agent_contact_plan", lambda data: result())
    text = invoke()
    payload = invoke("--output", "json")
    assert text.exit_code == payload.exit_code == 0
    assert f'handoff_token="{"a" * 64}"' in text.stdout
    assert [line.split("=", 1)[0] for line in text.stdout.splitlines()] == list(
        AgentContactPlanResult.model_fields
    )
    assert "Zoë" in text.stdout
    expected = (
        json.dumps(
            result().model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert payload.stdout == expected and set(json.loads(payload.stdout)) == set(
        AgentContactPlanResult.model_fields
    )
    assert json.loads(payload.stdout)["handoff_token"] == "a" * 64


def test_no_selection_text_and_json_include_null_handoff_in_model_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_cli, "execute_agent_contact_plan", lambda data: no_selection_result())
    text = invoke()
    payload = invoke("--output", "json")
    assert text.exit_code == payload.exit_code == 0
    lines = text.stdout.splitlines()
    token_index = list(AgentContactPlanResult.model_fields).index("handoff_token")
    assert lines[token_index] == "handoff_token=null"
    assert lines[token_index + 1] == "human_review_required=true"
    parsed = json.loads(payload.stdout)
    assert parsed["decision"] == "NO_SELECTION"
    assert parsed["handoff_token"] is None
    for field in (
        "selected_candidate_id",
        "selected_contact_name",
        "selected_contact_title",
        "selected_contact_email",
        "selected_contact_phone",
        "selected_contact_source_url",
        "selected_contact_source_type",
        "selected_contact_confidence",
        "proposed_lead_title",
        "proposed_task_title",
        "proposed_task_description",
    ):
        assert parsed[field] is None


@pytest.mark.parametrize(
    "args", [("--project-id", "0"), ("--company-id", "bad"), ("--goal", " "), ("--output", "yaml")]
)
def test_invalid_values_exit_two(args: tuple[str, str]) -> None:
    base = [
        "agent",
        "contact-select",
        "plan",
        "--project-id",
        "1",
        "--company-id",
        "2",
        "--goal",
        "Find",
    ]
    index = base.index(args[0]) if args[0] in base else -1
    if index >= 0:
        base[index + 1] = args[1]
    else:
        base.extend(args)
    outcome = runner.invoke(app, base)
    assert outcome.exit_code == 2 and outcome.stdout == ""
    assert outcome.stderr == "Agent contact plan data is invalid.\n"


@pytest.mark.parametrize("option", ["--project-id", "--company-id", "--goal", "--output"])
def test_duplicate_options_are_rejected(option: str) -> None:
    if option == "--output":
        outcome = invoke("--output", "text", "--output", "json")
    else:
        extra = "Again" if option == "--goal" else "3"
        outcome = invoke(option, extra)
    assert outcome.exit_code == 2 and outcome.stderr == "Agent contact plan data is invalid.\n"


def test_controlled_and_unexpected_errors_are_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(data: object):
        raise AgentContactPlanProjectNotFoundError("Project was not found.")

    monkeypatch.setattr(agent_cli, "execute_agent_contact_plan", missing)
    outcome = invoke()
    assert outcome.exit_code == 3 and outcome.stderr == "Project was not found.\n"

    def secret(data: object):
        raise RuntimeError("secret API key database URL")

    monkeypatch.setattr(agent_cli, "execute_agent_contact_plan", secret)
    outcome = invoke()
    assert outcome.exit_code == 1 and outcome.stderr == "Agent contact plan failed.\n"


def test_renderer_revalidates_constructed_values_without_enum_value_access() -> None:
    values = result().model_dump()
    values["decision"] = "SELECT"
    values["discovery_status"] = "SUCCEEDED"
    values["selected_contact_source_type"] = "TEAM_PAGE"
    bypassed = AgentContactPlanResult.model_construct(**values)
    with pytest.raises(AgentContactPlanInternalError):
        agent_cli.render_agent_contact_plan(bypassed, "json")
