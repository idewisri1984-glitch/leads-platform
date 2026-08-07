from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from math import isfinite

from ..contact_discovery.models import ContactDiscoverySourceType

_VERSION = "agent-contact-plan-handoff-v1"


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _text(value: object, field: str, maximum: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise ValueError(f"{field} must be a string.")
    normalized = " ".join(value.split())
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must be valid UTF-8.") from exc
    if not normalized or "\x00" in normalized or len(normalized) > maximum:
        raise ValueError(f"{field} is invalid.")
    return normalized


def canonicalize_handoff_datetime(value: object) -> str:
    if type(value) is not datetime:
        raise ValueError("discovery_checked_at must be a datetime.")
    try:
        if value.tzinfo is None:
            utc_value = value.replace(tzinfo=UTC)
        else:
            if value.utcoffset() is None:
                raise ValueError("discovery_checked_at must have a valid offset.")
            utc_value = value.astimezone(UTC)
    except Exception as exc:
        raise ValueError("discovery_checked_at must have a valid offset.") from exc
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_agent_contact_plan_handoff_token(
    *,
    project_id: object,
    company_id: object,
    company_name: object,
    company_website: object,
    goal: object,
    provider_name: object,
    discovery_checked_at: object,
    candidate_id: object,
    candidate_deduplication_key: object,
    candidate_name: object,
    candidate_title: object,
    candidate_email: object,
    candidate_phone: object,
    candidate_source_url: object,
    candidate_source_type: object,
    candidate_confidence: object,
    proposed_lead_title: object,
    proposed_task_title: object,
    proposed_task_description: object,
) -> str:
    if type(candidate_source_type) is not ContactDiscoverySourceType:
        raise ValueError("candidate_source_type is invalid.")
    if type(candidate_confidence) is not float or not isfinite(candidate_confidence):
        raise ValueError("candidate_confidence is invalid.")
    if not 0.0 <= candidate_confidence <= 1.0:
        raise ValueError("candidate_confidence is invalid.")

    payload = {
        "version": _VERSION,
        "project_id": _positive_int(project_id, "project_id"),
        "company": {
            "id": _positive_int(company_id, "company_id"),
            "name": _text(company_name, "company_name", 255),
            "website": _text(company_website, "company_website", 255),
        },
        "goal": _text(goal, "goal", 2000),
        "provider_name": _text(provider_name, "provider_name", 100),
        "discovery_checked_at": canonicalize_handoff_datetime(discovery_checked_at),
        "selected_candidate": {
            "id": _positive_int(candidate_id, "candidate_id"),
            "deduplication_key": _text(
                candidate_deduplication_key, "candidate_deduplication_key", 255
            ),
            "name": _text(candidate_name, "candidate_name", 255, optional=True),
            "title": _text(candidate_title, "candidate_title", 255, optional=True),
            "email": _text(candidate_email, "candidate_email", 255, optional=True),
            "phone": _text(candidate_phone, "candidate_phone", 100, optional=True),
            "source_url": _text(candidate_source_url, "candidate_source_url", 500, optional=True),
            "source_type": candidate_source_type.value,
            "confidence": candidate_confidence,
        },
        "proposals": {
            "lead_title": _text(proposed_lead_title, "proposed_lead_title", 255),
            "task_title": _text(proposed_task_title, "proposed_task_title", 255),
            "task_description": _text(proposed_task_description, "proposed_task_description", 4000),
        },
    }
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


__all__ = ["build_agent_contact_plan_handoff_token", "canonicalize_handoff_datetime"]
