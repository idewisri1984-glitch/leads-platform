from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from typing import Annotated, Never, Protocol

import typer
from pydantic import ValidationError
from sqlalchemy.orm import Session
from typer import _click as click
from typer._click.exceptions import UsageError
from typer.core import TyperCommand

from app.cli._lazy_dependencies import SessionLocal
from app.modules.email_draft.context import EMAIL_DRAFT_PROMPT_VERSION
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

app = typer.Typer(help="Persisted AI email-draft and human-review commands.")

_INVALID = "Email draft data is invalid."
_CONFIRMATION = "Email draft review requires --yes."
_INTERNAL = "Email draft operation failed."
_OPTIONS = (
    "--project-id",
    "--company-id",
    "--contact-id",
    "--lead-id",
    "--task-id",
    "--draft-id",
    "--sender-name",
    "--sender-company",
    "--language",
    "--tone",
    "--purpose",
    "--value-proposition",
    "--yes",
    "--output",
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


class _SessionFactory(Protocol):
    def __call__(self) -> Session: ...


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


class _OpenAIGeneratorFactory:
    def __call__(self) -> EmailDraftGenerator:
        from app.core.config.settings import settings
        from app.providers.openai_email import OpenAIEmailDraftGenerator

        return OpenAIEmailDraftGenerator(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
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
    session = session_factory()
    generator: EmailDraftGenerator | None = None
    committed = False
    failed = False
    try:
        factory = generator_factory or _OpenAIGeneratorFactory()
        generator = factory()
        service = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=generator,
        )
        rendered = render_email_draft(service.generate(data), output)
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
        if generator is not None:
            close = getattr(generator, "close", None)
            if callable(close):
                _cleanup(close) if failed else close()
        _cleanup(session.close) if failed else session.close()


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


def _scope(project_id: str, company_id: str, contact_id: str, draft_id: str) -> dict[str, int]:
    return {
        "project_id": _positive(project_id),
        "company_id": _positive(company_id),
        "contact_id": _positive(contact_id),
        "draft_id": _positive(draft_id),
    }


@app.command("show", cls=_EmailDraftCommand)
def show(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
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
    contact_id: str,
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
    contact_id: Annotated[str, typer.Option("--contact-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    _review_command("approve", project_id, company_id, contact_id, draft_id, yes, output)


@app.command("reject", cls=_ReviewCommand)
def reject(
    project_id: Annotated[str, typer.Option("--project-id")],
    company_id: Annotated[str, typer.Option("--company-id")],
    contact_id: Annotated[str, typer.Option("--contact-id")],
    draft_id: Annotated[str, typer.Option("--draft-id")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
    output: Annotated[str, typer.Option("--output")] = "text",
) -> None:
    _review_command("reject", project_id, company_id, contact_id, draft_id, yes, output)


__all__ = ["app", "execute_generate", "execute_review", "execute_show", "render_email_draft"]
