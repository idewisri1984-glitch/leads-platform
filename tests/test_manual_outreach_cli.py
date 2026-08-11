import json

from typer.testing import CliRunner

from app.cli.main import app

from .test_email_delivery_service import _command, _records


def _arguments(ids: dict[str, int]) -> list[str]:
    command = _command(ids)
    return [
        "--project-id",
        str(command.project_id),
        "--company-id",
        str(command.company_id),
        "--contact-id",
        str(command.contact_id),
        "--email-draft-id",
        str(command.email_draft_id),
    ]


def test_manual_export_json_and_mark_sent_work_without_smtp_configuration(
    monkeypatch,
) -> None:
    for name in (
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_ENVELOPE_FROM",
        "SMTP_HEADER_FROM_EMAIL",
        "SMTP_MESSAGE_ID_DOMAIN",
    ):
        monkeypatch.delenv(name, raising=False)
    ids = _records()
    runner = CliRunner()
    exported = runner.invoke(
        app,
        ["agent", "email-draft", "export", *_arguments(ids), "--output", "json"],
    )
    assert exported.exit_code == 0, exported.output
    payload = json.loads(exported.stdout)
    assert payload["outreach_status"] == "READY_FOR_MANUAL_SEND"
    assert payload["subject"] == "Reviewed subject"

    missing_confirmation = runner.invoke(
        app,
        ["agent", "email-draft", "mark-sent", *_arguments(ids)],
    )
    assert missing_confirmation.exit_code == 3
    assert "requires --confirm" in missing_confirmation.stderr

    marked = runner.invoke(
        app,
        [
            "agent",
            "email-draft",
            "mark-sent",
            *_arguments(ids),
            "--confirm",
            "--output",
            "json",
        ],
    )
    assert marked.exit_code == 0, marked.output
    assert json.loads(marked.stdout)["outreach_status"] == "MANUALLY_SENT"


def test_manual_export_text_is_copy_ready_and_preserves_body() -> None:
    ids = _records()
    result = CliRunner().invoke(
        app,
        ["agent", "email-draft", "export", *_arguments(ids)],
    )
    assert result.exit_code == 0, result.output
    assert "TO: recipient@example.test" in result.stdout
    assert "SUBJECT: Reviewed subject" in result.stdout
    assert "BODY:\nReviewed plain-text body with sufficient stable content.\n---" in result.stdout
