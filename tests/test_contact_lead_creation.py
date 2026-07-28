import traceback
from dataclasses import dataclass
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import app.modules.lead.contact_lead_creation as creation_module
from app.modules.lead import (
    ContactLeadCreationConsistencyError,
    ContactLeadCreationError,
    ContactLeadCreationInvalidDataError,
    ContactLeadCreationNotFoundError,
    ContactLeadCreationResult,
    ContactLeadCreationService,
)
from app.modules.lead.contact_lead_creation import (
    ContactLeadCreationContactRepository,
    ContactLeadCreationLeadRepository,
)


@dataclass
class ContactRecord:
    id: Any
    company_id: Any


@dataclass
class LeadRecord:
    id: Any
    company_id: Any
    contact_id: Any
    status: Any = "NEW"
    source: Any = None
    notes: Any = None


class ContactRepositoryFake:
    def __init__(
        self,
        *,
        contact: ContactRecord | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.contact = contact if contact is not None else ContactRecord(2, 1)
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[tuple[int, int]] = []

    def get_for_company(self, company_id: int, contact_id: int) -> ContactRecord | None:
        self.operations.append("contact_lookup")
        self.calls.append((company_id, contact_id))
        if self.error is not None:
            raise self.error
        return self.contact

    def get(self, contact_id: int) -> None:
        raise AssertionError("ContactRepository.get must not be called.")


class LeadRepositoryFake:
    def __init__(
        self,
        *,
        leads: list[LeadRecord] | None = None,
        error: BaseException | None = None,
        operations: list[str] | None = None,
    ) -> None:
        self.leads = list(leads) if leads is not None else [LeadRecord(3, 1, 2)]
        self.error = error
        self.operations = operations if operations is not None else []
        self.calls: list[dict[str, object]] = []

    def create_for_contact(
        self,
        *,
        company_id: int,
        contact_id: int,
        status: str = "NEW",
        source: str | None = None,
    ) -> LeadRecord:
        self.operations.append("lead_creation")
        self.calls.append(
            {
                "company_id": company_id,
                "contact_id": contact_id,
                "status": status,
                "source": source,
            }
        )
        if self.error is not None:
            raise self.error
        return self.leads.pop(0)

    def create(self, **kwargs: object) -> None:
        raise AssertionError("Legacy LeadRepository.create must not be called.")

    def get(self, lead_id: int) -> None:
        raise AssertionError("LeadRepository.get must not be called.")

    def get_by_contact(self, contact_id: int) -> None:
        raise AssertionError("LeadRepository.get_by_contact must not be called.")

    def get_by_company(self, company_id: int) -> None:
        raise AssertionError("LeadRepository.get_by_company must not be called.")


def make_service(
    *,
    contact: ContactRecord | None = None,
    contact_error: BaseException | None = None,
    leads: list[LeadRecord] | None = None,
    lead_error: BaseException | None = None,
) -> tuple[ContactLeadCreationService, ContactRepositoryFake, LeadRepositoryFake]:
    operations: list[str] = []
    contact_repository = ContactRepositoryFake(
        contact=contact,
        error=contact_error,
        operations=operations,
    )
    lead_repository = LeadRepositoryFake(
        leads=leads,
        error=lead_error,
        operations=operations,
    )
    service = ContactLeadCreationService(
        cast(ContactLeadCreationContactRepository, contact_repository),
        cast(ContactLeadCreationLeadRepository, lead_repository),
    )
    return service, contact_repository, lead_repository


def test_result_schema_is_strict_frozen_and_contains_no_pii() -> None:
    result = ContactLeadCreationResult(
        lead_id=3,
        company_id=1,
        contact_id=2,
        status="NEW",
    )

    assert result.model_dump() == {
        "lead_id": 3,
        "company_id": 1,
        "contact_id": 2,
        "status": "NEW",
    }
    assert set(ContactLeadCreationResult.model_fields) == {
        "lead_id",
        "company_id",
        "contact_id",
        "status",
    }
    with pytest.raises(ValidationError):
        result.lead_id = 4


@pytest.mark.parametrize("field", ["lead_id", "company_id", "contact_id"])
@pytest.mark.parametrize("invalid_value", [True, 0, -1])
def test_result_schema_rejects_invalid_identifiers(
    field: str,
    invalid_value: object,
) -> None:
    values: dict[str, object] = {
        "lead_id": 3,
        "company_id": 1,
        "contact_id": 2,
        "status": "NEW",
    }
    values[field] = invalid_value

    with pytest.raises(ValidationError):
        ContactLeadCreationResult.model_validate(values)


def test_result_schema_rejects_wrong_status_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ContactLeadCreationResult(
            lead_id=3,
            company_id=1,
            contact_id=2,
            status="QUALIFIED",  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        ContactLeadCreationResult.model_validate(
            {
                "lead_id": 3,
                "company_id": 1,
                "contact_id": 2,
                "status": "NEW",
                "email": "private@example.com",
            }
        )


def test_success_uses_exact_call_order_mapping_and_sanitized_result() -> None:
    service, contact_repository, lead_repository = make_service()

    result = service.create(1, 2)

    assert contact_repository.operations == ["contact_lookup", "lead_creation"]
    assert contact_repository.calls == [(1, 2)]
    assert lead_repository.calls == [
        {
            "company_id": 1,
            "contact_id": 2,
            "status": "NEW",
            "source": None,
        }
    ]
    assert result == ContactLeadCreationResult(
        lead_id=3,
        company_id=1,
        contact_id=2,
        status="NEW",
    )
    assert "private@example.com" not in repr(result)
    assert not any(isinstance(value, ContactRecord | LeadRecord) for value in result)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("company_id", True),
        ("company_id", 0),
        ("company_id", -1),
        ("company_id", "1"),
        ("company_id", 1.0),
        ("company_id", None),
        ("company_id", object()),
        ("company_id", type("CompanyIntSubclass", (int,), {})(1)),
        ("contact_id", False),
        ("contact_id", 0),
        ("contact_id", -1),
        ("contact_id", "2"),
        ("contact_id", 2.0),
        ("contact_id", None),
        ("contact_id", object()),
        ("contact_id", type("ContactIntSubclass", (int,), {})(2)),
    ],
)
def test_invalid_input_fails_before_repository_calls(
    field: str,
    invalid_value: object,
) -> None:
    service, contact_repository, lead_repository = make_service()
    values: dict[str, object] = {"company_id": 1, "contact_id": 2}
    values[field] = invalid_value

    with pytest.raises(
        ContactLeadCreationInvalidDataError,
        match=r"^Lead creation data is invalid\.$",
    ) as captured:
        service.create(**values)  # type: ignore[arg-type]

    assert contact_repository.operations == []
    assert contact_repository.calls == []
    assert lead_repository.calls == []
    assert "1" not in str(captured.value)
    assert "2" not in repr(captured.value)


@pytest.mark.parametrize("contact", [None])
def test_missing_or_hidden_contact_uses_fixed_not_found_error(
    contact: ContactRecord | None,
) -> None:
    service, contact_repository, lead_repository = make_service(contact=contact)
    contact_repository.contact = None

    with pytest.raises(
        ContactLeadCreationNotFoundError,
        match=r"^Contact was not found\.$",
    ):
        service.create(1, 2)

    assert contact_repository.calls == [(1, 2)]
    assert lead_repository.calls == []


@pytest.mark.parametrize(
    "contact",
    [
        ContactRecord(True, 1),
        ContactRecord(0, 1),
        ContactRecord(9, 1),
        ContactRecord(2, True),
        ContactRecord(2, 0),
        ContactRecord(2, 9),
    ],
)
def test_malformed_or_mismatched_contact_is_inconsistent(
    contact: ContactRecord,
) -> None:
    service, _, lead_repository = make_service(contact=contact)

    with pytest.raises(
        ContactLeadCreationConsistencyError,
        match=r"^Lead creation state is inconsistent\.$",
    ):
        service.create(1, 2)

    assert lead_repository.calls == []


@pytest.mark.parametrize(
    "repository_error",
    [
        TypeError("repository secret: sqlite:///private.db"),
        ValueError("contact_email@example.test company_id=731"),
    ],
    ids=["type-error", "value-error"],
)
def test_controlled_contact_repository_errors_are_sanitized(
    repository_error: Exception,
) -> None:
    service, _, lead_repository = make_service(contact_error=repository_error)

    with pytest.raises(ContactLeadCreationConsistencyError) as captured:
        service.create(1, 2)

    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    raw_message = str(repository_error)
    assert str(captured.value) == "Lead creation state is inconsistent."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert raw_message not in str(captured.value)
    assert raw_message not in repr(captured.value)
    assert raw_message not in rendered
    assert lead_repository.calls == []


@pytest.mark.parametrize(
    "repository_error",
    [
        SQLAlchemyError("database unavailable"),
        RuntimeError("runtime failure"),
        KeyboardInterrupt(),
    ],
)
def test_contact_infrastructure_errors_propagate_unchanged(
    repository_error: BaseException,
) -> None:
    service, _, lead_repository = make_service(contact_error=repository_error)

    with pytest.raises(type(repository_error)) as captured:
        service.create(1, 2)

    assert captured.value is repository_error
    assert lead_repository.calls == []


@pytest.mark.parametrize(
    "repository_error",
    [
        TypeError("repository secret: sqlite:///private.db"),
        ValueError("contact_email@example.test company_id=731"),
    ],
    ids=["type-error", "value-error"],
)
def test_controlled_lead_repository_errors_are_sanitized(
    repository_error: Exception,
) -> None:
    service, _, _ = make_service(lead_error=repository_error)

    with pytest.raises(ContactLeadCreationConsistencyError) as captured:
        service.create(1, 2)

    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    raw_message = str(repository_error)
    assert str(captured.value) == "Lead creation state is inconsistent."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert raw_message not in str(captured.value)
    assert raw_message not in repr(captured.value)
    assert raw_message not in rendered


@pytest.mark.parametrize(
    "repository_error",
    [
        IntegrityError("insert", {}, RuntimeError("foreign key")),
        SQLAlchemyError("database unavailable"),
        RuntimeError("runtime failure"),
        KeyboardInterrupt(),
    ],
)
def test_lead_infrastructure_errors_propagate_unchanged(
    repository_error: BaseException,
) -> None:
    service, _, _ = make_service(lead_error=repository_error)

    with pytest.raises(type(repository_error)) as captured:
        service.create(1, 2)

    assert captured.value is repository_error


@pytest.mark.parametrize(
    "lead",
    [
        LeadRecord(True, 1, 2),
        LeadRecord(0, 1, 2),
        LeadRecord(3, 9, 2),
        LeadRecord(3, 1, 9),
        LeadRecord(3, 1, None),
        LeadRecord(3, 1, 2, status=type("StatusSubclass", (str,), {})("NEW")),
        LeadRecord(3, 1, 2, status="QUALIFIED"),
        LeadRecord(3, 1, 2, source="contact"),
        LeadRecord(3, 1, 2, notes="private"),
    ],
)
def test_malformed_or_mismatched_lead_is_inconsistent(lead: LeadRecord) -> None:
    service, _, _ = make_service(leads=[lead])

    with pytest.raises(
        ContactLeadCreationConsistencyError,
        match=r"^Lead creation state is inconsistent\.$",
    ):
        service.create(1, 2)


def test_repeated_calls_are_intentionally_non_idempotent() -> None:
    service, contact_repository, lead_repository = make_service(
        leads=[LeadRecord(3, 1, 2), LeadRecord(4, 1, 2)]
    )

    first = service.create(1, 2)
    second = service.create(1, 2)

    assert contact_repository.calls == [(1, 2), (1, 2)]
    assert len(lead_repository.calls) == 2
    assert first.lead_id == 3
    assert second.lead_id == 4
    assert first != second
    assert not hasattr(lead_repository, "find_duplicate")


def test_service_module_has_no_forbidden_domain_dependencies() -> None:
    assert not hasattr(creation_module, "CompanyRepository")
    assert not hasattr(creation_module, "TaskRepository")
    assert not hasattr(creation_module, "SessionLocal")
    assert not hasattr(creation_module, "LeadRepository")
    assert not hasattr(creation_module, "ContactRepository")


def test_error_inheritance_and_public_exports() -> None:
    assert issubclass(ContactLeadCreationError, ValueError)
    assert issubclass(ContactLeadCreationNotFoundError, ContactLeadCreationError)
    assert issubclass(ContactLeadCreationInvalidDataError, ContactLeadCreationError)
    assert issubclass(ContactLeadCreationConsistencyError, ContactLeadCreationError)

    import app.modules.lead as lead_module

    expected = {
        "ContactLeadCreationConsistencyError",
        "ContactLeadCreationError",
        "ContactLeadCreationInvalidDataError",
        "ContactLeadCreationNotFoundError",
        "ContactLeadCreationResult",
        "ContactLeadCreationService",
    }
    assert expected <= set(lead_module.__all__)
    assert all(hasattr(lead_module, name) for name in expected)
    assert "ContactLeadCreationContactRepository" not in lead_module.__all__
    assert "ContactLeadCreationLeadRepository" not in lead_module.__all__
