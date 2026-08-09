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
