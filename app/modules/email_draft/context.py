import hashlib
import html
import json
import re
from dataclasses import dataclass
from typing import cast

from app.modules.contact_discovery.normalization import normalize_discovered_email

from .schemas import (
    EmailDraftGenerationInput,
    EmailDraftProviderRequest,
    EmailPersonalizationContext,
)

EMAIL_DRAFT_PROMPT_VERSION = "email-outreach-draft-v1"
_TAG = re.compile(r"<!--.*?-->|<\s*/?\s*[A-Za-z][^>]*>", re.DOTALL)
_DANGEROUS_MARKUP = re.compile(
    r"<\s*/?\s*(?:script|style|form|iframe)\b[^>]*>?", re.IGNORECASE | re.DOTALL
)
_SPACE = re.compile(r"[ \t\f\v]+")
_ENTITY_DECODE_LIMIT = 2


class EmailDraftContextError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EmailDraftSourceRecords:
    project: object
    company: object
    contact: object | None
    lead: object
    task: object
    company_email: str | None = None


def sanitize_context_data(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise EmailDraftContextError("Email draft context is invalid.")
    text = value
    for _ in range(_ENTITY_DECODE_LIMIT):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _DANGEROUS_MARKUP.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = "\n".join(_SPACE.sub(" ", line).strip() for line in text.splitlines()).strip()
    text = "\n".join(line for line in text.splitlines() if line)
    if not text:
        return None
    return text[:maximum]


def _text(record: object, name: str, maximum: int, *, required: bool = False) -> str | None:
    value = sanitize_context_data(getattr(record, name, None), maximum)
    if required and value is None:
        raise EmailDraftContextError("Email draft context is invalid.")
    return value


def _id(record: object, name: str = "id") -> int:
    value = getattr(record, name, None)
    if type(value) is not int or value <= 0:
        raise EmailDraftContextError("Email draft context is invalid.")
    return value


def build_email_personalization_context(
    records: EmailDraftSourceRecords,
) -> EmailPersonalizationContext:
    contact = records.contact
    if contact is None:
        company_name = cast(str, _text(records.company, "name", 255, required=True))
        recipient_name = f"{company_name} team"
        recipient_role = None
        contact_id = None
        raw_email = records.company_email
    else:
        first_name = cast(str, _text(contact, "first_name", 100, required=True))
        last_name = _text(contact, "last_name", 100)
        recipient_name = first_name if last_name is None else f"{first_name} {last_name}"
        recipient_role = _text(contact, "job_title", 150)
        contact_id = _id(contact)
        raw_email = getattr(contact, "email", None)
    try:
        email = normalize_discovered_email(raw_email) if type(raw_email) is str else None
    except (TypeError, ValueError):
        email = None
    if email is None:
        raise EmailDraftContextError("Email draft recipient email is unusable.")
    return EmailPersonalizationContext(
        project_id=_id(records.project),
        project_name=cast(str, _text(records.project, "name", 255, required=True)),
        company_id=_id(records.company),
        company_name=cast(str, _text(records.company, "name", 255, required=True)),
        company_website=_text(records.company, "website", 255),
        company_city=_text(records.company, "city", 100),
        company_country=_text(records.company, "country", 100),
        company_industry=_text(records.company, "industry", 100),
        company_notes_data=_text(records.company, "notes", 1000),
        contact_id=contact_id,
        recipient_name=recipient_name,
        recipient_role=recipient_role,
        recipient_email=email,
        lead_id=_id(records.lead),
        lead_status=cast(str, _text(records.lead, "status", 50, required=True)),
        lead_source=_text(records.lead, "source", 100),
        task_id=_id(records.task),
        task_title=cast(str, _text(records.task, "title", 255, required=True)),
        task_description_data=_text(records.task, "description", 1000),
    )


def canonical_sha256(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def build_context_fingerprint(
    context: EmailPersonalizationContext, generation: EmailDraftGenerationInput
) -> str:
    return canonical_sha256(
        {
            "context": context.model_dump(mode="json"),
            "language": generation.language.value,
            "purpose": generation.purpose,
            "sender_company": generation.sender_company,
            "sender_name": generation.sender_name,
            "tone": generation.tone.value,
            "value_proposition": generation.value_proposition,
            "prompt_version": generation.prompt_version,
        }
    )


def build_provider_request(
    context: EmailPersonalizationContext, generation: EmailDraftGenerationInput
) -> EmailDraftProviderRequest:
    return EmailDraftProviderRequest(
        context=context,
        sender_name=generation.sender_name,
        sender_company=generation.sender_company,
        language=generation.language,
        tone=generation.tone,
        purpose=generation.purpose,
        value_proposition=generation.value_proposition,
        prompt_version=generation.prompt_version,
    )


def build_content_hash(
    *, recipient_email: str, subject: str, text_body: str, prompt_version: str
) -> str:
    return canonical_sha256(
        {
            "prompt_version": prompt_version,
            "recipient_email": recipient_email,
            "subject": subject,
            "text_body": text_body,
        }
    )
