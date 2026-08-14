from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Annotated, Never, Protocol

import typer
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typer import _click as click
from typer._click.exceptions import UsageError
from typer.core import TyperCommand

from app.cli._lazy_dependencies import SessionLocal
from app.modules.email_delivery.manual_repository import ManualEmailSendRecordRepository
from app.modules.email_delivery.manual_schemas import (
    ConfirmedManualEmailSendCommand,
    ManualEmailCopyPackage,
    ManualEmailDraftScope,
)
from app.modules.email_delivery.manual_service import (
    ManualOutreachAlreadySentError,
    ManualOutreachAutomaticAttemptError,
    ManualOutreachConfirmationRequiredError,
    ManualOutreachError,
    ManualOutreachInvalidCommandError,
    ManualOutreachNotApprovedError,
    ManualOutreachNotFoundError,
    ManualOutreachPersistenceError,
    ManualOutreachService,
    ManualOutreachStaleContextError,
    ManualOutreachTransactionBoundaryError,
)
from app.modules.email_delivery.models import EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendResult,
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryConfigurationError,
    EmailDeliveryConfirmationRequiredError,
    EmailDeliveryInternalError,
    EmailDeliveryInvalidCommandError,
    EmailDeliveryNotApprovedError,
    EmailDeliveryNotFoundError,
    EmailDeliveryPermanentFailureError,
    EmailDeliveryPersistenceRecoveryRequiredError,
    EmailDeliveryServiceError,
    EmailDeliveryStaleContextError,
    EmailDeliveryTransactionBoundaryError,
    EmailDeliveryTransientFailureError,
    EmailDeliveryUnknownOutcomeError,
    TrustedEmailSenderConfig,
)
from app.modules.email_draft.context import EMAIL_DRAFT_PROMPT_VERSION
from app.modules.email_draft.execution import (
    OpenAIEmailGeneratorFactory as _OpenAIGeneratorFactory,
)
from app.modules.email_draft.provider_interfaces import EmailDraftGenerator
from app.modules.email_draft.repository import EmailDraftRepository
from app.modules.email_draft.schemas import (
    EmailDraftGenerationInput,
    EmailDraftRead,
    EmailDraftReviewInput,
    EmailDraftScopeInput,
    EmailLanguage,
    EmailTone,
)
from app.modules.email_draft.service import (
    EmailDraftAlreadyReviewedError,
    EmailDraftConfirmationRequiredError,
    EmailDraftConflictError,
    EmailDraftError,
    EmailDraftGenerationError,
    EmailDraftIntegrityError,
    EmailDraftInternalError,
    EmailDraftInvalidDataError,
    EmailDraftMalformedResultError,
    EmailDraftMissingEmailError,
    EmailDraftNotEligibleError,
    EmailDraftNotFoundError,
    EmailDraftPersistenceError,
    EmailDraftScopeError,
    EmailDraftService,
    EmailDraftStaleContextError,
)

if TYPE_CHECKING:
    from app.providers.smtp.interfaces import SMTPTransport

app = typer.Typer(help="Persisted AI email-draft and human-review commands.")

_INVALID = "Email draft data is invalid."
_CONFIRMATION = "Email draft review requires --yes."
_SEND_CONFIRMATION = "Email delivery requires --confirm."
_MANUAL_SEND_CONFIRMATION = "Manual sent recording requires --confirm."
_INTERNAL = "Email draft operation failed."
_DELIVERY_INTERNAL = "Email delivery failed."
_OPTIONS = (
    "--project-id",
    "--company-id",
    "--contact-id",
    "--lead-id",
    "--task-id",
    "--draft-id",
    "--email-draft-id",
    "--sender-name",
    "--sender-company",
    "--language",
    "--tone",
    "--purpose",
    "--value-proposition",
    "--yes",
    "--confirm",
    "--output",
)
_DELIVERY_ERROR_CODES: tuple[tuple[type[EmailDeliveryServiceError], int], ...] = (
    (EmailDeliveryInvalidCommandError, 2),
    (EmailDeliveryConfirmationRequiredError, 3),
    (EmailDeliveryNotFoundError, 4),
    (EmailDeliveryNotApprovedError, 7),
    (EmailDeliveryStaleContextError, 14),
    (EmailDeliveryAlreadyAttemptedError, 15),
    (EmailDeliveryConfigurationError, 16),
    (EmailDeliveryTransientFailureError, 17),
    (EmailDeliveryPermanentFailureError, 18),
    (EmailDeliveryUnknownOutcomeError, 19),
    (EmailDeliveryPersistenceRecoveryRequiredError, 20),
    (EmailDeliveryTransactionBoundaryError, 21),
    (EmailDeliveryInternalError, 1),
)
_ERROR_CODES: tuple[tuple[type[EmailDraftError], int], ...] = (
    (EmailDraftInvalidDataError, 2),
    (EmailDraftConfirmationRequiredError, 3),
    (EmailDraftNotFoundError, 4),
    (EmailDraftScopeError, 5),
    (EmailDraftMissingEmailError, 6),
    (EmailDraftNotEligibleError, 7),
    (EmailDraftGenerationError, 8),
    (EmailDraftMalformedResultError, 9),
    (EmailDraftPersistenceError, 10),
    (EmailDraftConflictError, 11),
    (EmailDraftAlreadyReviewedError, 12),
    (EmailDraftIntegrityError, 13),
    (EmailDraftStaleContextError, 14),
    (EmailDraftInternalError, 1),
)
_MANUAL_ERROR_CODES: tuple[tuple[type[ManualOutreachError], int], ...] = (
    (ManualOutreachInvalidCommandError, 2),
    (ManualOutreachConfirmationRequiredError, 3),
    (ManualOutreachNotFoundError, 4),
    (ManualOutreachNotApprovedError, 7),
    (ManualOutreachStaleContextError, 14),
    (ManualOutreachAlreadySentError, 15),
    (ManualOutreachAutomaticAttemptError, 15),
    (ManualOutreachPersistenceError, 20),
    (ManualOutreachTransactionBoundaryError, 21),
)


class _EmailDraftCommand(TyperCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(argument in {"--help", "-h"} for argument in args):
            for option in _OPTIONS:
                count = sum(
                    argument == option or argument.startswith(f"{option}=") for argument in args
                )
                if count > 1:
                    self._invalid()
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            self._invalid()

    @staticmethod
    def _invalid() -> Never:
        click.echo(_INVALID, err=True)
        raise click.exceptions.Exit(2)


class _ReviewCommand(_EmailDraftCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(argument in {"--help", "-h"} for argument in args) and (
            "--yes" not in args or any(argument.startswith("--yes=") for argument in args)
        ):
            click.echo(_CONFIRMATION, err=True)
            raise click.exceptions.Exit(3)
        return super().parse_args(ctx, args)


class _SendCommand(_EmailDraftCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(argument in {"--help", "-h"} for argument in args) and (
            "--confirm" not in args or any(argument.startswith("--confirm=") for argument in args)
        ):
            click.echo(_SEND_CONFIRMATION, err=True)
            raise click.exceptions.Exit(3)
        return super().parse_args(ctx, args)


class _ManualSendCommand(_EmailDraftCommand):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not any(argument in {"--help", "-h"} for argument in args) and (
            "--confirm" not in args or any(argument.startswith("--confirm=") for argument in args)
        ):
            click.echo(_MANUAL_SEND_CONFIRMATION, err=True)
            raise click.exceptions.Exit(3)
        return super().parse_args(ctx, args)


class _SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class _SMTPCompositionFactory:
    def __call__(self) -> tuple[SMTPTransport, TrustedEmailSenderConfig]:
        from app.providers.smtp.client import SMTPClient
        from app.providers.smtp.contracts import SMTPSecurityMode, SMTPTransportConfig

        try:
            from app.core.config.settings import settings

            security_mode = SMTPSecurityMode(settings.smtp_security_mode)
            if (
                settings.smtp_host is None
                or settings.smtp_envelope_from is None
                or settings.smtp_header_from_email is None
                or settings.smtp_message_id_domain is None
            ):
                raise ValueError("Missing SMTP settings.")
            transport = SMTPClient(
                SMTPTransportConfig(
                    host=settings.smtp_host,
                    port=settings.smtp_port,
                    security_mode=security_mode,
                    username=settings.smtp_username,
                    password=settings.smtp_password,
                    connection_timeout_seconds=settings.smtp_timeout_seconds,
                )
            )
            sender = TrustedEmailSenderConfig(
                envelope_from=settings.smtp_envelope_from,
                header_from_email=settings.smtp_header_from_email,
                header_from_name=settings.smtp_header_from_name,
                reply_to=settings.smtp_reply_to,
                message_id_domain=settings.smtp_message_id_domain,
                transport_name=settings.smtp_transport_name,
                security_mode=security_mode,
            )
        except (ValidationError, TypeError, ValueError):
            raise EmailDeliveryConfigurationError(
                "Email delivery configuration is invalid."
            ) from None
        return transport, sender


def _positive(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise EmailDraftInvalidDataError(_INVALID) from None
    if parsed <= 0 or str(parsed) != value:
        raise EmailDraftInvalidDataError(_INVALID)
    return parsed


def render_email_draft(result: EmailDraftRead, output: str) -> str:
    if type(result) is not EmailDraftRead or output not in {"text", "json"}:
        raise EmailDraftInternalError(_INTERNAL)
    try:
        validated = EmailDraftRead(**result.model_dump())
        values = validated.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        raise EmailDraftInternalError(_INTERNAL) from None
    if output == "json":
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join(
        f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
        for field in EmailDraftRead.model_fields
    )


def render_email_delivery(result: ConfirmedEmailSendResult, output: str) -> str:
    if type(result) is not ConfirmedEmailSendResult or output not in {"text", "json"}:
        raise EmailDeliveryInternalError(_DELIVERY_INTERNAL)
    try:
        validated = ConfirmedEmailSendResult(**result.model_dump())
        values = validated.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        raise EmailDeliveryInternalError(_DELIVERY_INTERNAL) from None
    if validated.outcome is not EmailDeliveryOutcome.ACCEPTED:
        raise EmailDeliveryInternalError(_DELIVERY_INTERNAL)
    if output == "json":
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "\n".join(
        f"{field}={json.dumps(values[field], ensure_ascii=False, separators=(',', ':'))}"
        for field in ConfirmedEmailSendResult.model_fields
    )


def render_manual_email_copy_package(result: ManualEmailCopyPackage, output: str) -> str:
    if type(result) is not ManualEmailCopyPackage or output not in {"text", "json"}:
        raise ManualOutreachInvalidCommandError(_INVALID)
    try:
        validated = ManualEmailCopyPackage(**result.model_dump())
        values = validated.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        raise ManualOutreachInvalidCommandError(_INVALID) from None
    if output == "json":
        return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metadata = (
        f"PROJECT_ID: {validated.project_id}\n"
        f"COMPANY_ID: {validated.company_id}\n"
        f"CONTACT_ID: {validated.contact_id}\n"
        f"LEAD_ID: {validated.lead_id}\n"
        f"TASK_ID: {validated.task_id}\n"
        f"EMAIL_DRAFT_ID: {validated.email_draft_id}\n"
        f"DRAFT_STATUS: {validated.draft_status}\n"
        f"CONTENT_HASH: {validated.content_hash}\n"
        f"MANUAL_SEND_RECORD_ID: {validated.manual_send_record_id}\n"
        f"SENT_AT: {values['sent_at']}"
    )
    return (
        f"OUTREACH_STATUS: {validated.outreach_status.value}\n"
        f"TO: {validated.recipient_email}\n"
        f"RECIPIENT_NAME: {validated.recipient_name}\n"
        f"COMPANY: {validated.company_name}\n"
        f"SUBJECT: {validated.subject}\n"
        f"BODY:\n{validated.text_body}\n"
        f"---\n{metadata}"
    )


def _cleanup(operation: Callable[[], object]) -> None:
    with suppress(BaseException):
        operation()


def execute_generate(
    data: EmailDraftGenerationInput,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
    generator_factory: Callable[[], EmailDraftGenerator] | None = None,
) -> str:
    from app.modules.email_draft.execution import execute_email_draft_generation

    rendered: list[str] = []

    def prepare_result(result: EmailDraftRead) -> None:
        rendered.append(render_email_draft(result, output))

    execute_email_draft_generation(
        data,
        session_factory=session_factory,
        generator_factory=generator_factory,
        before_commit=prepare_result,
    )
    return rendered[0]


def execute_generate_missing(
    *,
    project_id: int,
    limit: int,
    sender_name: str,
    sender_company: str,
    purpose: str,
    value_proposition: str | None,
    language: EmailLanguage,
    tone: EmailTone,
    dry_run: bool,
    output: str,
    session_factory: _SessionFactory = SessionLocal,
    generator_factory: Callable[[], EmailDraftGenerator] | None = None,
) -> str:
    from app.modules.email_draft.batch import (
        MissingDraftBatchOptions,
        MissingEmailDraftBatchService,
    )

    service = MissingEmailDraftBatchService(
        session_factory=session_factory,
        generator_factory=generator_factory or _OpenAIGeneratorFactory(),
    )
    result = service.run(
        MissingDraftBatchOptions(
            project_id=project_id,
            limit=limit,
            sender_name=sender_name,
            sender_company=sender_company,
            purpose=purpose,
            value_proposition=value_proposition,
            language=language,
            tone=tone,
            dry_run=dry_run,
        )
    )
    items_payload: list[dict[str, object]] = [
        {
            "company_id": item.company_id,
            "company_name": item.company_name,
            "recipient_type": item.recipient_type,
            "recipient_email": item.recipient_email,
            "decision_maker": item.decision_maker,
            "contact_id": item.contact_id,
            "result": item.result.value,
            "draft_id": item.draft_id,
            "subject": item.subject,
        }
        for item in result.items
    ]
    payload: dict[str, object] = {
        "candidate_count": result.candidate_count,
        "selected_limit": result.selected_limit,
        "ai_call_count": result.ai_call_count,
        "items": items_payload,
    }
    if output == "json":
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lines = [
        f"candidate_count={result.candidate_count}",
        f"selected_limit={result.selected_limit}",
        f"ai_call_count={result.ai_call_count}",
    ]
    lines.extend(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items_payload)
    return "\n".join(lines)


def execute_show(
    data: EmailDraftScopeInput,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    session = session_factory()
    failed = False
    try:
        service = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=None,
        )
        return render_email_draft(service.show(data), output)
    except BaseException:
        failed = True
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def execute_review(
    data: EmailDraftReviewInput,
    action: str,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    session = session_factory()
    committed = False
    failed = False
    try:
        service = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=None,
        )
        result = service.approve(data) if action == "approve" else service.reject(data)
        rendered = render_email_draft(result, output)
        try:
            session.commit()
            committed = True
        except Exception:
            raise EmailDraftPersistenceError("Email draft could not be persisted.") from None
        return rendered
    except BaseException:
        failed = True
        if not committed:
            _cleanup(session.rollback)
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def execute_send(
    data: ConfirmedEmailSendCommand,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
    composition_factory: Callable[[], tuple[SMTPTransport, TrustedEmailSenderConfig]] | None = None,
) -> str:
    factory = composition_factory or _SMTPCompositionFactory()
    transport, sender = factory()
    session = session_factory()
    failed = False
    try:
        repository = EmailDeliveryAttemptRepository(session)
        service = ConfirmedEmailSendService(
            session=session,
            repository=repository,
            transport=transport,
            sender=sender,
        )
        return render_email_delivery(service.send(data), output)
    except BaseException:
        failed = True
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def execute_manual_export(
    data: ManualEmailDraftScope,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    session = session_factory()
    failed = False
    try:
        service = ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
        )
        return render_manual_email_copy_package(service.export(data), output)
    except BaseException:
        failed = True
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def execute_manual_mark_sent(
    data: ConfirmedManualEmailSendCommand,
    output: str,
    *,
    session_factory: _SessionFactory = SessionLocal,
) -> str:
    session = session_factory()
    committed = False
    failed = False
    try:
        service = ManualOutreachService(
            session,
            ManualEmailSendRecordRepository(session),
        )
        rendered = render_manual_email_copy_package(service.mark_sent(data), output)
        try:
            session.commit()
            committed = True
        except Exception:
            raise ManualOutreachPersistenceError(
                "Manual sent record could not be persisted."
            ) from None
        return rendered
    except BaseException:
        failed = True
        if not committed:
            _cleanup(session.rollback)
        raise
    finally:
        _cleanup(session.close) if failed else session.close()


def _handle_error(error: BaseException) -> Never:
    if isinstance(error, ValidationError):
        typer.echo(_INVALID, err=True)
        raise typer.Exit(2) from None
    if isinstance(error, EmailDraftError):
        code = next(
            (value for error_type, value in _ERROR_CODES if isinstance(error, error_type)), 1
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(code) from None
    typer.echo(_INTERNAL, err=True)
    raise typer.Exit(1) from None


def _handle_delivery_error(error: BaseException) -> Never:
    if isinstance(error, ValidationError):
        typer.echo(_INVALID, err=True)
        raise typer.Exit(2) from None
    if isinstance(error, EmailDeliveryServiceError):
        code = next(
            (value for error_type, value in _DELIVERY_ERROR_CODES if isinstance(error, error_type)),
            1,
        )
        if isinstance(error, EmailDeliveryAlreadyAttemptedError):
            outcome = error.outcome or "UNKNOWN"
            message = f"Email delivery already has outcome={outcome}; retry is not supported."
        elif isinstance(error, EmailDeliveryPersistenceRecoveryRequiredError):
            message = (
                "Email delivery requires manual reconciliation; delivery receipt is not claimed "
                "and retry is not supported."
            )
        else:
            message = str(error)
        typer.echo(message, err=True)
        raise typer.Exit(code) from None
    typer.echo(_DELIVERY_INTERNAL, err=True)
    raise typer.Exit(1) from None


def _handle_manual_error(error: BaseException) -> Never:
    if isinstance(error, ValidationError):
        typer.echo(_INVALID, err=True)
        raise typer.Exit(2) from None
    if isinstance(error, ManualOutreachError):
        code = next(
            (value for error_type, value in _MANUAL_ERROR_CODES if isinstance(error, error_type)),
            1,
        )
        typer.echo(str(error), err=True)
        raise typer.Exit(code) from None
    typer.echo(_DELIVERY_INTERNAL, err=True)
    raise typer.Exit(1) from None


def _output(value: str) -> str:
    if value not in {"text", "json"}:
        raise EmailDraftInvalidDataError(_INVALID)
    return value


@app.command("generate", cls=_EmailDraftCommand)
def generate(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    lead_id: Annotated[str, typer.Option("--lead-id")],
    task_id: Annotated[str, typer.Option("--task-id")],
    sender_name: Annotated[str, typer.Option("--sender-name")],
    sender_company: Annotated[str, typer.Option("--sender-company")],
    purpose: Annotated[str, typer.Option("--purpose")],
    language: Annotated[str, typer.Option("--language")] = "en",
    tone: Annotated[str, typer.Option("--tone")] = "professional",
    value_proposition: Annotated[str | None, typer.Option("--value-proposition")] = None,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    try:
        data = EmailDraftGenerationInput(
            project_id=_positive(project_id),
            company_id=_positive(company_id),
            contact_id=_positive(contact_id),
            lead_id=_positive(lead_id),
            task_id=_positive(task_id),
            sender_name=sender_name,
            sender_company=sender_company,
            language=EmailLanguage(language),
            tone=EmailTone(tone),
            purpose=purpose,
            value_proposition=value_proposition,
            prompt_version=EMAIL_DRAFT_PROMPT_VERSION,
        )
        rendered = execute_generate(data, _output(output))
    except Exception as error:
        _handle_error(error)
    typer.echo(rendered)


@app.command("generate-missing", cls=_EmailDraftCommand)
def generate_missing(
    project_id: Annotated[str, typer.Option("--project-id")],
    sender_name: Annotated[str, typer.Option("--sender-name")],
    sender_company: Annotated[str, typer.Option("--sender-company")],
    purpose: Annotated[str, typer.Option("--purpose")],
    limit: Annotated[str, typer.Option("--limit")] = "20",
    language: Annotated[str, typer.Option("--language")] = "en",
    tone: Annotated[str, typer.Option("--tone")] = "professional",
    value_proposition: Annotated[str | None, typer.Option("--value-proposition")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    """Generate a bounded batch of eligible missing drafts without sending email."""
    try:
        rendered = execute_generate_missing(
            project_id=_positive(project_id),
            limit=_positive(limit),
            sender_name=sender_name,
            sender_company=sender_company,
            purpose=purpose,
            value_proposition=value_proposition,
            language=EmailLanguage(language),
            tone=EmailTone(tone),
            dry_run=dry_run,
            output=_output(output),
        )
    except Exception as error:
        _handle_error(error)
    typer.echo(rendered)


def _scope(
    project_id: str, company_id: str, contact_id: str | None, draft_id: str
) -> dict[str, int | None]:
    return {
        "project_id": _positive(project_id),
        "company_id": _positive(company_id),
        "contact_id": None if contact_id is None else _positive(contact_id),
        "draft_id": _positive(draft_id),
    }


@app.command("show", cls=_EmailDraftCommand)
def show(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
    contact_id: Annotated[str | None, typer.Option("--contact-id")] = None,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    try:
        rendered = execute_show(
            EmailDraftScopeInput(**_scope(project_id, company_id, contact_id, draft_id)),
            _output(output),
        )
    except Exception as error:
        _handle_error(error)
    typer.echo(rendered)


def _review_command(
    action: str,
    project_id: str,
    company_id: str,
    contact_id: str | None,
    draft_id: str,
    yes: bool,
    output: str,
) -> None:
    if not yes:
        _handle_error(EmailDraftConfirmationRequiredError(_CONFIRMATION))
    try:
        data = EmailDraftReviewInput(
            **_scope(project_id, company_id, contact_id, draft_id), confirmed=True
        )
        rendered = execute_review(data, action, _output(output))
    except Exception as error:
        _handle_error(error)
    typer.echo(rendered)


@app.command("approve", cls=_ReviewCommand)
def approve(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
    contact_id: Annotated[str | None, typer.Option("--contact-id")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    _review_command("approve", project_id, company_id, contact_id, draft_id, yes, output)


@app.command("reject", cls=_ReviewCommand)
def reject(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
    contact_id: Annotated[str | None, typer.Option("--contact-id")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    _review_command("reject", project_id, company_id, contact_id, draft_id, yes, output)


@app.command("send", cls=_SendCommand)
def send(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    email_draft_id: Annotated[str, typer.Option("--email-draft-id")],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    if not confirm:
        _handle_delivery_error(EmailDeliveryConfirmationRequiredError(_SEND_CONFIRMATION))
    try:
        command = ConfirmedEmailSendCommand(
            project_id=_positive(project_id),
            company_id=_positive(company_id),
            contact_id=_positive(contact_id),
            email_draft_id=_positive(email_draft_id),
            confirmed=True,
        )
        rendered = execute_send(command, _output(output))
    except Exception as error:
        _handle_delivery_error(error)
    typer.echo(rendered)


@app.command("export", cls=_EmailDraftCommand)
def export_draft(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    email_draft_id: Annotated[str, typer.Option("--email-draft-id")],
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    try:
        rendered = execute_manual_export(
            ManualEmailDraftScope(
                project_id=_positive(project_id),
                company_id=_positive(company_id),
                contact_id=_positive(contact_id),
                email_draft_id=_positive(email_draft_id),
            ),
            _output(output),
        )
    except Exception as error:
        _handle_manual_error(error)
    typer.echo(rendered)


@app.command("mark-sent", cls=_ManualSendCommand)
def mark_sent(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    email_draft_id: Annotated[str, typer.Option("--email-draft-id")],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    if not confirm:
        _handle_manual_error(ManualOutreachConfirmationRequiredError(_MANUAL_SEND_CONFIRMATION))
    try:
        rendered = execute_manual_mark_sent(
            ConfirmedManualEmailSendCommand(
                project_id=_positive(project_id),
                company_id=_positive(company_id),
                contact_id=_positive(contact_id),
                email_draft_id=_positive(email_draft_id),
                confirmed=True,
            ),
            _output(output),
        )
    except Exception as error:
        _handle_manual_error(error)
    typer.echo(rendered)


__all__ = [
    "app",
    "execute_generate",
    "execute_manual_export",
    "execute_manual_mark_sent",
    "execute_review",
    "execute_send",
    "execute_show",
    "render_email_delivery",
    "render_email_draft",
    "render_manual_email_copy_package",
]
