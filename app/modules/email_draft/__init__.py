from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import EMAIL_DRAFT_PROMPT_VERSION, build_email_personalization_context
    from .fake_provider import FakeEmailDraftGenerator
    from .models import EmailDraft, EmailDraftStatus
    from .provider_interfaces import EmailDraftGenerator
    from .repository import EmailDraftRepository
    from .schemas import (
        EmailDraftGenerationInput,
        EmailDraftGenerationResult,
        EmailDraftRead,
        EmailDraftReviewInput,
        EmailDraftScopeInput,
        EmailLanguage,
        EmailPersonalizationContext,
        EmailTone,
    )
    from .service import EmailDraftService

_EXPORT_MODULES = {
    "EMAIL_DRAFT_PROMPT_VERSION": ".context",
    "EmailDraft": ".models",
    "EmailDraftGenerationInput": ".schemas",
    "EmailDraftGenerationResult": ".schemas",
    "EmailDraftGenerator": ".provider_interfaces",
    "EmailDraftRead": ".schemas",
    "EmailDraftRepository": ".repository",
    "EmailDraftReviewInput": ".schemas",
    "EmailDraftScopeInput": ".schemas",
    "EmailDraftService": ".service",
    "EmailDraftStatus": ".models",
    "EmailLanguage": ".schemas",
    "EmailPersonalizationContext": ".schemas",
    "EmailTone": ".schemas",
    "FakeEmailDraftGenerator": ".fake_provider",
    "build_email_personalization_context": ".context",
}

__all__ = [
    "EMAIL_DRAFT_PROMPT_VERSION",
    "EmailDraft",
    "EmailDraftGenerationInput",
    "EmailDraftGenerationResult",
    "EmailDraftGenerator",
    "EmailDraftRead",
    "EmailDraftRepository",
    "EmailDraftReviewInput",
    "EmailDraftScopeInput",
    "EmailDraftService",
    "EmailDraftStatus",
    "EmailLanguage",
    "EmailPersonalizationContext",
    "EmailTone",
    "FakeEmailDraftGenerator",
    "build_email_personalization_context",
]


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
