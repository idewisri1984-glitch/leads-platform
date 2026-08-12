import json

import pytest
from typer.testing import CliRunner

import app.cli.crm as crm_cli
from app.cli.main import app
from app.modules.crm import CRMOverviewRow
from tests.cli_output import plain_cli_output

runner = CliRunner()


def row() -> CRMOverviewRow:
    return CRMOverviewRow(
        company_id=11,
        company="Simon Wallace Design",
        contact_id=2,
        contact="Hillary Wallace",
        role="Principal Designer",
        email="hillary@simonwallacedesign.com",
        lead_id=2,
        lead_status="NEW",
        task_id=3,
        task="Follow up",
        task_status="TODO",
        draft_id=1,
        draft_task_id=2,
        draft_status="APPROVED",
        outreach_status="MANUALLY_SENT",
        last_sent_at=None,
    )


def test_crm_group_and_list_help_are_registered() -> None:
    root = plain_cli_output(runner.invoke(app, ["--help"]).output)
    group = plain_cli_output(runner.invoke(app, ["crm", "--help"]).output)
    command = plain_cli_output(runner.invoke(app, ["crm", "list", "--help"]).output)
    assert "crm" in root
    assert "list" in group
    assert "--project-id" in command and "--company-id" in command and "--output" in command


def test_empty_text_and_json_outputs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(crm_cli, "_load_rows", lambda _project, _company: ())
    text_result = runner.invoke(app, ["crm", "list"])
    json_result = runner.invoke(app, ["crm", "list", "--output", "json"])
    assert text_result.exit_code == 0
    assert "No CRM records found." in text_result.output
    assert json.loads(json_result.output) == []


def test_text_and_json_render_stable_fields(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(crm_cli, "_load_rows", lambda _project, _company: (row(),))
    text_result = runner.invoke(app, ["crm", "list"])
    json_result = runner.invoke(app, ["crm", "list", "--output", "json"])
    assert text_result.exit_code == 0
    assert "Simon Wallace Design" in text_result.output
    assert "MANUALLY_SENT" in text_result.output
    payload = json.loads(json_result.output)
    assert payload[0]["task_id"] == 3
    assert payload[0]["draft_task_id"] == 2
    assert payload[0]["outreach_status"] == "MANUALLY_SENT"


def test_text_renderer_keeps_pipe_characters_inside_one_company_cell(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    selected = row()
    pipe_name = "IA Interior Architects | Designing People-Centric Experiences | IA"
    selected = CRMOverviewRow(**{**selected.as_dict(), "company": pipe_name})
    monkeypatch.setattr(crm_cli, "_load_rows", lambda _project, _company: (selected,))

    result = runner.invoke(app, ["crm", "list"])

    assert result.exit_code == 0
    output = plain_cli_output(result.output)
    assert pipe_name in output
    assert "Field" in output and "Value" in output
    assert "Hillary Wallace" in output
    assert "Follow up" in output


@pytest.mark.parametrize(
    "literal_value",
    [
        "[bold]Literal[/bold]",
        "[link=https://example.com]Company[/link]",
    ],
)
def test_text_renderer_preserves_rich_markup_like_business_values_literally(
    monkeypatch, literal_value: str
) -> None:  # type: ignore[no-untyped-def]
    selected = row()
    selected = CRMOverviewRow(**{**selected.as_dict(), "company": literal_value})
    monkeypatch.setattr(crm_cli, "_load_rows", lambda _project, _company: (selected,))

    result = runner.invoke(app, ["crm", "list"])

    assert result.exit_code == 0
    assert literal_value in plain_cli_output(result.output)


def test_filters_are_forwarded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[int | None, int | None]] = []
    monkeypatch.setattr(
        crm_cli,
        "_load_rows",
        lambda project_id, company_id: calls.append((project_id, company_id)) or (),
    )
    result = runner.invoke(app, ["crm", "list", "--project-id", "1", "--company-id", "11"])
    assert result.exit_code == 0
    assert calls == [(1, 11)]


def test_invalid_filters_and_output_do_not_load_data(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    def load(_project_id: int | None, _company_id: int | None) -> tuple[CRMOverviewRow, ...]:
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(crm_cli, "_load_rows", load)
    for arguments in (
        ["--project-id", "0"],
        ["--company-id", "0"],
        ["--output", "csv"],
    ):
        assert runner.invoke(app, ["crm", "list", *arguments]).exit_code != 0
    assert calls == 0
