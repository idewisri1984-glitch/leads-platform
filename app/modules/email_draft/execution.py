from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.modules.email_draft.provider_interfaces import EmailDraftGenerator
    from app.modules.email_draft.schemas import EmailDraftGenerationInput, EmailDraftRead


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class OpenAIEmailGeneratorFactory:
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


def execute_email_draft_generation(
    data: EmailDraftGenerationInput,
    *,
    session_factory: SessionFactory,
    generator_factory: Callable[[], EmailDraftGenerator] | None = None,
) -> EmailDraftRead:
    from app.modules.email_draft.repository import EmailDraftRepository
    from app.modules.email_draft.service import EmailDraftPersistenceError, EmailDraftService

    session = session_factory()
    generator: EmailDraftGenerator | None = None
    committed = False
    failed = False
    try:
        generator = (generator_factory or OpenAIEmailGeneratorFactory())()
        result = EmailDraftService(
            session=session,
            repository=EmailDraftRepository(session),
            generator=generator,
        ).generate(data)
        try:
            session.commit()
            committed = True
        except Exception:
            raise EmailDraftPersistenceError("Email draft could not be persisted.") from None
        return result
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


__all__ = ["OpenAIEmailGeneratorFactory", "execute_email_draft_generation"]
