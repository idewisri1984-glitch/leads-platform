from enum import Enum, StrEnum
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.contact_discovery import (
    ContactDiscoveryCandidateNotEligibleError,
    ContactDiscoveryCandidatePromotionConsistencyError,
    ContactDiscoveryCandidatePromotionInvalidDataError,
    ContactDiscoveryCandidatePromotionNotFoundError,
    ContactDiscoveryCandidatePromotionResult,
    ContactDiscoveryCandidatePromotionService,
    ContactDiscoveryCandidateStatus,
)


def candidate(**values: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "id": 7,
        "company_id": 3,
        "name": "Ada Lovelace",
        "title": "Mathematician",
        "email": "ADA@Example.com",
        "normalized_email": "ada@example.com",
        "phone": "+1 555 0100",
        "discovery_status": ContactDiscoveryCandidateStatus.REVIEWED,
        "promoted_contact_id": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


class FakeStagingRepository:
    def __init__(self, item: SimpleNamespace | None = None) -> None:
        self.item = item if item is not None else candidate()
        self.calls: list[tuple[object, ...]] = []
        self.link_error: BaseException | None = None
        self.linked_override: SimpleNamespace | None = None

    def get_candidate_for_promotion(
        self, company_id: int, candidate_id: int
    ) -> SimpleNamespace | None:
        self.calls.append(("candidate", company_id, candidate_id))
        return self.item

    def link_promoted_contact(
        self, company_id: int, candidate_id: int, contact_id: int
    ) -> SimpleNamespace:
        self.calls.append(("link", company_id, candidate_id, contact_id))
        if self.link_error is not None:
            raise self.link_error
        if self.linked_override is not None:
            return self.linked_override
        assert self.item is not None
        self.item.discovery_status = ContactDiscoveryCandidateStatus.PROMOTED
        self.item.promoted_contact_id = contact_id
        return self.item


class FakeContactRepository:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self.calls = calls
        self.duplicate: SimpleNamespace | None = None
        self.linked: SimpleNamespace | None = None
        self.scope_error: BaseException | None = None
        self.lookup_error: BaseException | None = None
        self.create_error: BaseException | None = None
        self.created_values: dict[str, object] | None = None

    def acquire_promotion_scope(self, company_id: int) -> None:
        self.calls.append(("scope", company_id))
        if self.scope_error is not None:
            raise self.scope_error

    def get_for_company(self, company_id: int, contact_id: int) -> SimpleNamespace | None:
        self.calls.append(("linked-contact", company_id, contact_id))
        return self.linked

    def find_promotion_duplicate_by_email(
        self, company_id: int, normalized_email: str
    ) -> SimpleNamespace | None:
        self.calls.append(("duplicate", company_id, normalized_email))
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.duplicate

    def create_for_promotion(self, **values: object) -> SimpleNamespace:
        self.calls.append(("create",))
        if self.create_error is not None:
            raise self.create_error
        self.created_values = values
        return SimpleNamespace(id=11, company_id=values["company_id"])


def service(
    item: SimpleNamespace | None = None,
) -> tuple[
    ContactDiscoveryCandidatePromotionService,
    FakeStagingRepository,
    FakeContactRepository,
    list[tuple[object, ...]],
]:
    calls: list[tuple[object, ...]] = []
    staging = FakeStagingRepository(item)
    contacts = FakeContactRepository(calls)
    original_get = staging.get_candidate_for_promotion
    original_link = staging.link_promoted_contact

    def get(company_id: int, candidate_id: int) -> SimpleNamespace | None:
        calls.append(("candidate", company_id, candidate_id))
        return original_get(company_id, candidate_id)

    def link(company_id: int, candidate_id: int, contact_id: int) -> SimpleNamespace:
        calls.append(("link", company_id, candidate_id, contact_id))
        return original_link(company_id, candidate_id, contact_id)

    staging.get_candidate_for_promotion = get
    staging.link_promoted_contact = link
    return ContactDiscoveryCandidatePromotionService(staging, contacts), staging, contacts, calls


@pytest.mark.parametrize("value", [0, -1, True, False, "1", None])
def test_invalid_ids_fail_before_repository_calls(value: object) -> None:
    promotion, _, _, calls = service()
    with pytest.raises(
        ContactDiscoveryCandidatePromotionInvalidDataError,
        match=r"^Candidate promotion data is invalid\.$",
    ):
        promotion.promote(value, 7)  # type: ignore[arg-type]
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion.promote(3, value)  # type: ignore[arg-type]
    assert calls == []


def test_scope_precedes_candidate_duplicate_create_and_link() -> None:
    promotion, _, contacts, calls = service()
    result = promotion.promote(3, 7)
    assert calls == [
        ("scope", 3),
        ("candidate", 3, 7),
        ("duplicate", 3, "ada@example.com"),
        ("create",),
        ("link", 3, 7, 11),
    ]
    assert result.created_contact is True
    assert result.changed is True
    assert contacts.created_values == {
        "company_id": 3,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "job_title": "Mathematician",
        "email": "ada@example.com",
        "phone": "+1 555 0100",
        "source": "CONTACT_DISCOVERY",
        "external_id": "contact-discovery-candidate:7",
        "status": "NEW",
    }


def test_existing_duplicate_is_reused_without_mutation_or_create() -> None:
    promotion, _, contacts, calls = service()
    existing = SimpleNamespace(id=5, company_id=3, name="Keep", email="keep@example.com")
    snapshot = vars(existing).copy()
    contacts.duplicate = existing
    result = promotion.promote(3, 7)
    assert calls == [
        ("scope", 3),
        ("candidate", 3, 7),
        ("duplicate", 3, "ada@example.com"),
        ("link", 3, 7, 5),
    ]
    assert vars(existing) == snapshot
    assert result.contact_id == 5
    assert result.created_contact is False
    assert result.changed is True


@pytest.mark.parametrize("error", [TypeError("raw"), ValueError("raw")])
def test_scope_failure_is_sanitized_not_found(error: Exception) -> None:
    promotion, _, contacts, _ = service()
    contacts.scope_error = error
    with pytest.raises(
        ContactDiscoveryCandidatePromotionNotFoundError,
        match=r"^Candidate was not found\.$",
    ) as raised:
        promotion.promote(3, 7)
    assert "raw" not in str(raised.value)


def test_missing_and_mismatched_candidates_are_sanitized() -> None:
    promotion, staging, _, _ = service()
    staging.item = None
    with pytest.raises(ContactDiscoveryCandidatePromotionNotFoundError):
        promotion.promote(3, 7)
    for item in (candidate(id=8), candidate(company_id=4)):
        promotion, _, _, _ = service(item)
        with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
            promotion.promote(3, 7)


class OtherStatus(Enum):
    REVIEWED = "REVIEWED"


class OtherStringStatus(StrEnum):
    REVIEWED = "REVIEWED"


@pytest.mark.parametrize(
    "value",
    [
        "reviewed",
        " REVIEWED ",
        "UNKNOWN",
        True,
        1,
        OtherStatus.REVIEWED,
        OtherStringStatus.REVIEWED,
        None,
        object(),
    ],
)
def test_malformed_persisted_status_is_rejected_without_raw_value(value: object) -> None:
    promotion, _, _, _ = service(candidate(discovery_status=value))
    with pytest.raises(
        ContactDiscoveryCandidatePromotionConsistencyError,
        match=r"^Candidate promotion state is inconsistent\.$",
    ) as raised:
        promotion.promote(3, 7)
    assert str(value) not in str(raised.value)


@pytest.mark.parametrize(
    "status",
    [ContactDiscoveryCandidateStatus.DISCOVERED, ContactDiscoveryCandidateStatus.REJECTED],
)
def test_non_reviewed_candidate_is_not_eligible(status: ContactDiscoveryCandidateStatus) -> None:
    promotion, _, _, _ = service(candidate(discovery_status=status))
    with pytest.raises(
        ContactDiscoveryCandidateNotEligibleError,
        match=r"^Candidate is not eligible for promotion\.$",
    ):
        promotion.promote(3, 7)


def test_reviewed_candidate_with_link_is_inconsistent() -> None:
    promotion, _, _, _ = service(candidate(promoted_contact_id=9))
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


@pytest.mark.parametrize("link", [None, 0, -1, True, "9"])
def test_promoted_candidate_requires_valid_link(link: object) -> None:
    promotion, _, _, _ = service(
        candidate(
            discovery_status=ContactDiscoveryCandidateStatus.PROMOTED,
            promoted_contact_id=link,
        )
    )
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


def test_consistent_already_promoted_candidate_is_idempotent() -> None:
    promotion, _, contacts, calls = service(
        candidate(
            discovery_status="PROMOTED",
            promoted_contact_id=9,
        )
    )
    contacts.linked = SimpleNamespace(id=9, company_id=3)
    result = promotion.promote(3, 7)
    assert calls == [("scope", 3), ("candidate", 3, 7), ("linked-contact", 3, 9)]
    assert result == ContactDiscoveryCandidatePromotionResult(
        candidate_id=7,
        company_id=3,
        contact_id=9,
        previous_status=ContactDiscoveryCandidateStatus.PROMOTED,
        current_status=ContactDiscoveryCandidateStatus.PROMOTED,
        created_contact=False,
        changed=False,
    )


@pytest.mark.parametrize(
    "linked",
    [None, SimpleNamespace(id=10, company_id=3), SimpleNamespace(id=9, company_id=4)],
)
def test_already_promoted_linked_contact_is_validated(
    linked: SimpleNamespace | None,
) -> None:
    promotion, _, contacts, _ = service(
        candidate(
            discovery_status=ContactDiscoveryCandidateStatus.PROMOTED,
            promoted_contact_id=9,
        )
    )
    contacts.linked = linked
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


@pytest.mark.parametrize("name", [None, "", "   ", "<Ada>", "Ada\x00Lovelace", 1])
def test_candidate_name_is_required_and_safe(name: object) -> None:
    promotion, _, _, _ = service(candidate(name=name))
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion.promote(3, 7)


@pytest.mark.parametrize(
    ("name", "first", "last"),
    [
        (" Madonna ", "Madonna", None),
        (" Ada   Lovelace ", "Ada", "Lovelace"),
        ("Juan Carlos de la Vega", "Juan", "Carlos de la Vega"),
        ("Anne-Marie O'Neill", "Anne-Marie", "O'Neill"),
    ],
)
def test_name_mapping_is_deterministic(name: str, first: str, last: str | None) -> None:
    promotion, _, contacts, _ = service(candidate(name=name))
    promotion.promote(3, 7)
    assert contacts.created_values is not None
    assert contacts.created_values["first_name"] == first
    assert contacts.created_values["last_name"] == last


@pytest.mark.parametrize("name", ["x" * 101, f"A {'x' * 101}"])
def test_overlength_name_parts_are_rejected(name: str) -> None:
    promotion, _, _, _ = service(candidate(name=name))
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion.promote(3, 7)


def test_title_and_phone_are_normalized_without_punctuation_loss() -> None:
    promotion, _, contacts, _ = service(
        candidate(title="  Chief   Scientist ", phone=" +1  (555)   0100 ")
    )
    promotion.promote(3, 7)
    assert contacts.created_values is not None
    assert contacts.created_values["job_title"] == "Chief Scientist"
    assert contacts.created_values["phone"] == "+1 (555) 0100"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * 151),
        ("phone", "x" * 101),
        ("title", "<title>"),
        ("phone", "1\x002"),
        ("title", 1),
        ("phone", True),
    ],
)
def test_invalid_optional_candidate_text_is_rejected(field: str, value: object) -> None:
    promotion, _, _, _ = service(candidate(**{field: value}))
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion.promote(3, 7)


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("x", None),
        ("", None),
        (None, "x"),
        (None, " ADA@example.com "),
        (None, "ADA@example.com"),
        (1, None),
        (None, True),
    ],
)
def test_invalid_email_representations_are_rejected(raw: object, stored: object) -> None:
    promotion, _, _, _ = service(candidate(email=raw, normalized_email=stored))
    with pytest.raises(ContactDiscoveryCandidatePromotionInvalidDataError):
        promotion.promote(3, 7)


def test_email_mismatch_is_consistency_error() -> None:
    promotion, _, _, _ = service(
        candidate(email="one@example.com", normalized_email="two@example.com")
    )
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


@pytest.mark.parametrize(
    ("raw", "stored"),
    [(" ADA@Example.COM ", None), (None, "ada@example.com")],
)
def test_single_email_representation_is_canonicalized(raw: str | None, stored: str | None) -> None:
    promotion, _, contacts, calls = service(candidate(email=raw, normalized_email=stored))
    promotion.promote(3, 7)
    assert ("duplicate", 3, "ada@example.com") in calls
    assert contacts.created_values is not None
    assert contacts.created_values["email"] == "ada@example.com"


def test_no_email_skips_duplicate_lookup() -> None:
    promotion, _, contacts, calls = service(candidate(email=None, normalized_email=None))
    promotion.promote(3, 7)
    assert not any(call[0] == "duplicate" for call in calls)
    assert contacts.created_values is not None
    assert contacts.created_values["email"] is None


@pytest.mark.parametrize(
    "returned",
    [SimpleNamespace(id=0, company_id=3), SimpleNamespace(id=9, company_id=4)],
)
def test_duplicate_result_is_validated(returned: SimpleNamespace) -> None:
    promotion, _, contacts, _ = service()
    contacts.duplicate = returned
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


def test_created_contact_and_linked_candidate_are_validated() -> None:
    promotion, staging, contacts, _ = service()
    contacts.create_for_promotion = lambda **values: SimpleNamespace(id=0, company_id=3)
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)
    promotion, staging, _, _ = service()
    staging.linked_override = candidate(
        discovery_status=ContactDiscoveryCandidateStatus.PROMOTED,
        promoted_contact_id=12,
    )
    with pytest.raises(ContactDiscoveryCandidatePromotionConsistencyError):
        promotion.promote(3, 7)


@pytest.mark.parametrize("error", [TypeError("raw"), ValueError("raw")])
def test_link_failure_is_sanitized_consistency_error(error: Exception) -> None:
    promotion, staging, _, _ = service()
    staging.link_error = error
    with pytest.raises(
        ContactDiscoveryCandidatePromotionConsistencyError,
        match=r"^Candidate promotion state is inconsistent\.$",
    ) as raised:
        promotion.promote(3, 7)
    assert "raw" not in str(raised.value)


def test_result_schema_rejects_inconsistent_combinations() -> None:
    base: dict[str, Any] = {
        "candidate_id": 7,
        "company_id": 3,
        "contact_id": 9,
        "previous_status": ContactDiscoveryCandidateStatus.REVIEWED,
        "current_status": ContactDiscoveryCandidateStatus.PROMOTED,
        "created_contact": True,
        "changed": True,
    }
    for changes in (
        {"current_status": ContactDiscoveryCandidateStatus.REVIEWED},
        {"previous_status": ContactDiscoveryCandidateStatus.PROMOTED},
        {
            "previous_status": ContactDiscoveryCandidateStatus.PROMOTED,
            "changed": False,
            "created_contact": True,
        },
        {"candidate_id": True},
        {"contact_id": 0},
    ):
        with pytest.raises(ValidationError):
            ContactDiscoveryCandidatePromotionResult(**(base | changes))
