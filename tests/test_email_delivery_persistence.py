from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Thread

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.core.database.base import Base
from app.modules.company.models import Company
from app.modules.contact.models import Contact
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.schemas import (
    EmailDeliveryAttemptCreate,
    EmailDeliveryAttemptOutcomeUpdate,
    EmailDeliveryAttemptRead,
    EmailDeliverySMTPClassification,
)
from app.modules.email_draft.models import EmailDraft, EmailDraftStatus
from app.modules.lead.models import Lead
from app.modules.project.models import Project
from app.modules.task.models import Task


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'delivery.sqlite3'}")
    Base.metadata.create_all(engine)
    value = sessionmaker(bind=engine, expire_on_commit=False)
    yield value
    Base.metadata.drop_all(engine)
    engine.dispose()


def _draft(session: Session, marker: str) -> EmailDraft:
    project = Project(name=f"Project {marker}")
    session.add(project)
    session.flush()
    company = Company(project_id=project.id, name=f"Company {marker}")
    session.add(company)
    session.flush()
    contact = Contact(
        company_id=company.id,
        first_name="Ada",
        email=f"ada-{marker}@example.com",
        status="NEW",
    )
    session.add(contact)
    session.flush()
    lead = Lead(company_id=company.id, contact_id=contact.id, status="NEW", source="AGENT")
    session.add(lead)
    session.flush()
    task = Task(lead_id=lead.id, title="Send reviewed email", status="TODO")
    session.add(task)
    session.flush()
    fingerprint = marker.encode().hex().ljust(64, "0")[:64]
    draft = EmailDraft(
        project_id=project.id,
        company_id=company.id,
        contact_id=contact.id,
        lead_id=lead.id,
        task_id=task.id,
        recipient_email=contact.email,
        recipient_name="Ada",
        recipient_role=None,
        sender_name="Alex",
        sender_company="Bali Leads",
        generation_tone="professional",
        generation_purpose="Introduce the service",
        generation_value_proposition=None,
        subject="A reviewed introduction",
        text_body="This reviewed body is long enough for the persisted email draft contract.",
        language="en",
        prompt_version="email-outreach-draft-v1",
        provider="fake",
        model="deterministic",
        context_fingerprint=fingerprint,
        request_fingerprint=fingerprint,
        content_hash="a" * 64,
        status=EmailDraftStatus.APPROVED.value,
        reviewed_at=datetime(2026, 8, 9, tzinfo=UTC),
        approved_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    session.add(draft)
    session.commit()
    return draft


def _create(draft_id: int, marker: str = "a", **changes: object) -> EmailDeliveryAttemptCreate:
    values: dict[str, object] = {
        "email_draft_id": draft_id,
        "attempt_key": marker * 64,
        "outcome": EmailDeliveryOutcome.RESERVED,
        "recipient_email": f"recipient-{marker}@example.com",
        "envelope_from": "mailer@example.com",
        "header_from_email": "team@example.com",
        "header_from_name": "Bali Leads",
        "reply_to": "reply@example.com",
        "message_id": f"<delivery-{marker}@example.com>",
        "content_hash": "f" * 64,
        "transport_name": "stdlib-smtp",
        "security_mode": "STARTTLS",
        "created_at": datetime(2026, 8, 9, 12, tzinfo=UTC),
    }
    values.update(changes)
    return EmailDeliveryAttemptCreate(**values)


def _terminal(outcome: EmailDeliveryOutcome) -> EmailDeliveryAttemptOutcomeUpdate:
    completed = datetime(2026, 8, 9, 13, tzinfo=UTC)
    if outcome is EmailDeliveryOutcome.ACCEPTED:
        return EmailDeliveryAttemptOutcomeUpdate(
            outcome=outcome,
            smtp_classification=None,
            smtp_code=250,
            error_category=None,
            completed_at=completed,
            accepted_at=completed,
            unknown_at=None,
        )
    classification = {
        EmailDeliveryOutcome.TRANSIENT_FAILURE: EmailDeliverySMTPClassification.TRANSIENT,
        EmailDeliveryOutcome.PERMANENT_FAILURE: EmailDeliverySMTPClassification.PERMANENT,
        EmailDeliveryOutcome.UNKNOWN: EmailDeliverySMTPClassification.UNKNOWN,
    }[outcome]
    return EmailDeliveryAttemptOutcomeUpdate(
        outcome=outcome,
        smtp_classification=classification,
        smtp_code=451 if outcome is EmailDeliveryOutcome.TRANSIENT_FAILURE else None,
        error_category="transport_failure",
        completed_at=completed,
        accepted_at=None,
        unknown_at=completed if outcome is EmailDeliveryOutcome.UNKNOWN else None,
    )


def _outcome_marker(outcome: EmailDeliveryOutcome) -> str:
    return {
        EmailDeliveryOutcome.ACCEPTED: "a",
        EmailDeliveryOutcome.TRANSIENT_FAILURE: "b",
        EmailDeliveryOutcome.PERMANENT_FAILURE: "c",
        EmailDeliveryOutcome.UNKNOWN: "d",
    }[outcome]


def _apply_direct_outcome(
    attempt: EmailDeliveryAttempt,
    update_data: EmailDeliveryAttemptOutcomeUpdate,
) -> None:
    attempt.outcome = update_data.outcome.value
    attempt.smtp_classification = (
        None if update_data.smtp_classification is None else update_data.smtp_classification.value
    )
    attempt.smtp_code = update_data.smtp_code
    attempt.error_category = update_data.error_category
    attempt.completed_at = update_data.completed_at
    attempt.accepted_at = update_data.accepted_at
    attempt.unknown_at = update_data.unknown_at
    attempt.updated_at = update_data.completed_at


def test_reserved_attempt_persists_and_hydrates_from_fresh_session(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        draft = _draft(session, "01")
        attempt = EmailDeliveryAttemptRepository(session).reserve(_create(draft.id))
        attempt_id = attempt.id
        session.commit()
    with factory() as session:
        persisted = session.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        assert persisted.outcome == EmailDeliveryOutcome.RESERVED.value
        assert persisted.email_draft_id == draft.id
        assert persisted.smtp_classification is None
        assert persisted.completed_at is None
        read = EmailDeliveryAttemptRead.model_validate(persisted)
        assert read.model_dump(mode="json")["created_at"] == "2026-08-09T12:00:00Z"


def test_unique_email_draft_is_enforced_across_independent_sessions(
    factory: sessionmaker[Session],
) -> None:
    with factory() as seed:
        draft = _draft(seed, "02")
    with factory() as first:
        EmailDeliveryAttemptRepository(first).reserve(_create(draft.id, "b"))
        first.commit()
    with factory() as second:
        with pytest.raises(IntegrityError):
            EmailDeliveryAttemptRepository(second).reserve(_create(draft.id, "c"))
        second.rollback()
    with factory() as check:
        assert check.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1


def test_unique_attempt_key_and_message_id_are_database_enforced(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        first_draft = _draft(session, "03")
        second_draft = _draft(session, "04")
        EmailDeliveryAttemptRepository(session).reserve(_create(first_draft.id, "d"))
        session.commit()
        with pytest.raises(IntegrityError):
            EmailDeliveryAttemptRepository(session).reserve(
                _create(second_draft.id, "d", message_id="<different@example.com>")
            )
        session.rollback()
        with pytest.raises(IntegrityError):
            EmailDeliveryAttemptRepository(session).reserve(
                _create(second_draft.id, "e", message_id="<delivery-d@example.com>")
            )
        session.rollback()


def test_foreign_key_and_outcome_checks_are_database_enforced(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session:
        with pytest.raises(IntegrityError):
            EmailDeliveryAttemptRepository(session).reserve(_create(999, "f"))
        session.rollback()
        draft = _draft(session, "05")
        attempt = EmailDeliveryAttemptRepository(session).reserve(_create(draft.id, "f"))
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                update(EmailDeliveryAttempt)
                .where(EmailDeliveryAttempt.id == attempt.id)
                .values(outcome="INVALID")
            )
            session.commit()
        session.rollback()


@pytest.mark.parametrize(
    "outcome",
    [
        EmailDeliveryOutcome.ACCEPTED,
        EmailDeliveryOutcome.TRANSIENT_FAILURE,
        EmailDeliveryOutcome.PERMANENT_FAILURE,
        EmailDeliveryOutcome.UNKNOWN,
    ],
)
def test_repository_applies_each_legal_terminal_transition(
    factory: sessionmaker[Session], outcome: EmailDeliveryOutcome
) -> None:
    with factory() as session:
        draft = _draft(session, outcome.value)
        attempt = EmailDeliveryAttemptRepository(session).reserve(
            _create(draft.id, _outcome_marker(outcome))
        )
        transitioned = EmailDeliveryAttemptRepository(session).transition(
            attempt.id, _terminal(outcome)
        )
        session.commit()
        assert transitioned.outcome == outcome.value
        assert transitioned.completed_at is not None
        assert (transitioned.accepted_at is not None) is (outcome is EmailDeliveryOutcome.ACCEPTED)
        assert (transitioned.unknown_at is not None) is (outcome is EmailDeliveryOutcome.UNKNOWN)


@pytest.mark.parametrize(
    ("initial", "replacement"),
    [
        (EmailDeliveryOutcome.ACCEPTED, EmailDeliveryOutcome.UNKNOWN),
        (EmailDeliveryOutcome.UNKNOWN, EmailDeliveryOutcome.ACCEPTED),
        (EmailDeliveryOutcome.TRANSIENT_FAILURE, EmailDeliveryOutcome.ACCEPTED),
        (EmailDeliveryOutcome.PERMANENT_FAILURE, EmailDeliveryOutcome.ACCEPTED),
    ],
)
def test_terminal_outcomes_cannot_transition(
    factory: sessionmaker[Session],
    initial: EmailDeliveryOutcome,
    replacement: EmailDeliveryOutcome,
) -> None:
    with factory() as session:
        draft = _draft(session, f"{initial.value}-{replacement.value}")
        attempt = EmailDeliveryAttemptRepository(session).reserve(
            _create(draft.id, _outcome_marker(initial))
        )
        repository = EmailDeliveryAttemptRepository(session)
        repository.transition(attempt.id, _terminal(initial))
        session.commit()
        with pytest.raises(ValueError, match="transition is invalid"):
            repository.transition(attempt.id, _terminal(replacement))
        session.rollback()
        session.refresh(attempt)
        assert attempt.outcome == initial.value


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("email_draft_id", 999),
        ("attempt_key", "1" * 64),
        ("recipient_email", "other@example.com"),
        ("envelope_from", "other@example.com"),
        ("header_from_email", "other@example.com"),
        ("header_from_name", "Other"),
        ("reply_to", "other@example.com"),
        ("message_id", "<other@example.com>"),
        ("content_hash", "1" * 64),
        ("transport_name", "other"),
        ("security_mode", "TLS_IMPLICIT"),
        ("created_at", datetime(2026, 8, 10, tzinfo=UTC)),
    ],
)
def test_identity_fields_are_immutable(
    factory: sessionmaker[Session], field: str, replacement: object
) -> None:
    with factory() as session:
        draft = _draft(session, f"immutable-{field}")
        attempt = EmailDeliveryAttemptRepository(session).reserve(_create(draft.id, "a"))
        session.commit()
        setattr(attempt, field, replacement)
        with pytest.raises(ValueError, match="identity is immutable"):
            session.flush()
        session.rollback()


@pytest.mark.parametrize(
    "values",
    [
        {"outcome": "ACCEPTED", "completed_at": datetime(2026, 8, 9, tzinfo=UTC)},
        {
            "outcome": "UNKNOWN",
            "completed_at": datetime(2026, 8, 9, tzinfo=UTC),
            "smtp_classification": "UNKNOWN",
        },
        {"completed_at": datetime(2026, 8, 9, tzinfo=UTC)},
        {
            "outcome": "TRANSIENT_FAILURE",
            "completed_at": datetime(2026, 8, 9, tzinfo=UTC),
            "accepted_at": datetime(2026, 8, 9, tzinfo=UTC),
            "smtp_classification": "TRANSIENT",
        },
        {
            "outcome": "UNKNOWN",
            "completed_at": datetime(2026, 8, 9, tzinfo=UTC),
            "accepted_at": datetime(2026, 8, 9, tzinfo=UTC),
            "unknown_at": datetime(2026, 8, 9, tzinfo=UTC),
            "smtp_classification": "UNKNOWN",
        },
    ],
)
def test_database_rejects_contradictory_outcome_timestamps(
    factory: sessionmaker[Session], values: dict[str, object]
) -> None:
    with factory() as session:
        draft = _draft(session, f"timestamps-{len(values)}")
        attempt = EmailDeliveryAttemptRepository(session).reserve(_create(draft.id, "a"))
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                update(EmailDeliveryAttempt)
                .where(EmailDeliveryAttempt.id == attempt.id)
                .values(**values)
            )
            session.commit()
        session.rollback()


@pytest.mark.parametrize(("code", "valid"), [(199, False), (200, True), (599, True), (600, False)])
def test_smtp_code_database_bounds(factory: sessionmaker[Session], code: int, valid: bool) -> None:
    with factory() as session:
        draft = _draft(session, f"smtp-{code}")
        attempt = EmailDeliveryAttemptRepository(session).reserve(_create(draft.id, "a"))
        session.commit()
        statement = (
            update(EmailDeliveryAttempt)
            .where(EmailDeliveryAttempt.id == attempt.id)
            .values(
                outcome="ACCEPTED",
                completed_at=datetime(2026, 8, 9, tzinfo=UTC),
                accepted_at=datetime(2026, 8, 9, tzinfo=UTC),
                smtp_code=code,
            )
        )
        if valid:
            session.execute(statement)
            session.commit()
            assert session.get(EmailDeliveryAttempt, attempt.id).smtp_code == code
        else:
            with pytest.raises(IntegrityError):
                session.execute(statement)
                session.commit()
            session.rollback()


@pytest.mark.parametrize(
    "address",
    [
        ".a@example.com",
        "a..b@example.com",
        "a@example..com",
        "a@example.com;",
        "t\u00e9st@example.com",
        "a@example.com\r\nBcc:x@example.com",
        "a@example.com\x00",
        "a@example.com,b@example.com",
    ],
)
def test_create_schema_rejects_transport_invalid_addresses(address: str) -> None:
    with pytest.raises(ValidationError):
        _create(1, recipient_email=address)


@pytest.mark.parametrize(
    "message_id",
    [
        "<a @example.com>",
        "<@example.com>",
        "<a@>",
        "<a@example..com>",
        "<a@example.com>\r\nBcc:x@example.com",
        "<a@example.com>\x00",
    ],
)
def test_create_schema_rejects_malformed_message_ids(message_id: str) -> None:
    with pytest.raises(ValidationError):
        _create(1, message_id=message_id)


def test_schema_rejects_invalid_hashes_and_outcome() -> None:
    with pytest.raises(ValidationError):
        _create(1, attempt_key="A" * 64)
    with pytest.raises(ValidationError):
        _create(1, content_hash="g" * 64)
    with pytest.raises(ValidationError):
        _create(1, outcome=EmailDeliveryOutcome.ACCEPTED)


def test_datetime_is_normalized_before_sqlite_and_serialized_as_utc_z(
    factory: sessionmaker[Session],
) -> None:
    local_time = datetime(2026, 8, 9, 19, tzinfo=timezone(timedelta(hours=7)))
    with factory() as session:
        draft = _draft(session, "datetime")
        attempt = EmailDeliveryAttemptRepository(session).reserve(
            _create(draft.id, "a", created_at=local_time)
        )
        attempt_id = attempt.id
        session.commit()
    with factory() as session:
        persisted = session.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        values = EmailDeliveryAttemptRead.model_validate(persisted).model_dump(mode="json")
        assert values["created_at"] == "2026-08-09T12:00:00Z"
        assert values["updated_at"] == "2026-08-09T12:00:00Z"


@pytest.mark.parametrize(
    ("winner", "stale_replacement"),
    [
        (EmailDeliveryOutcome.ACCEPTED, EmailDeliveryOutcome.UNKNOWN),
        (
            EmailDeliveryOutcome.TRANSIENT_FAILURE,
            EmailDeliveryOutcome.PERMANENT_FAILURE,
        ),
        (EmailDeliveryOutcome.PERMANENT_FAILURE, EmailDeliveryOutcome.ACCEPTED),
        (EmailDeliveryOutcome.UNKNOWN, EmailDeliveryOutcome.TRANSIENT_FAILURE),
    ],
)
def test_stale_direct_orm_write_cannot_overwrite_terminal_outcome(
    factory: sessionmaker[Session],
    winner: EmailDeliveryOutcome,
    stale_replacement: EmailDeliveryOutcome,
) -> None:
    with factory() as seed:
        draft = _draft(seed, f"stale-{winner.value}")
        attempt = EmailDeliveryAttemptRepository(seed).reserve(
            _create(draft.id, _outcome_marker(winner))
        )
        seed.commit()
        attempt_id = attempt.id

    first = factory()
    stale = factory()
    try:
        first_attempt = first.get(EmailDeliveryAttempt, attempt_id)
        stale_attempt = stale.get(EmailDeliveryAttempt, attempt_id)
        assert first_attempt is not None
        assert stale_attempt is not None
        assert first_attempt.outcome == EmailDeliveryOutcome.RESERVED.value
        assert stale_attempt.outcome == EmailDeliveryOutcome.RESERVED.value

        _apply_direct_outcome(first_attempt, _terminal(winner))
        first.commit()
        _apply_direct_outcome(stale_attempt, _terminal(stale_replacement))
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    finally:
        first.close()
        stale.close()

    with factory() as check:
        persisted = check.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        assert persisted.outcome == winner.value


def test_stale_same_outcome_direct_orm_write_is_rejected(
    factory: sessionmaker[Session],
) -> None:
    with factory() as seed:
        draft = _draft(seed, "stale-same-outcome")
        attempt = EmailDeliveryAttemptRepository(seed).reserve(_create(draft.id, "a"))
        seed.commit()
        attempt_id = attempt.id

    first = factory()
    stale = factory()
    try:
        first_attempt = first.get(EmailDeliveryAttempt, attempt_id)
        stale_attempt = stale.get(EmailDeliveryAttempt, attempt_id)
        assert first_attempt is not None
        assert stale_attempt is not None
        _apply_direct_outcome(first_attempt, _terminal(EmailDeliveryOutcome.ACCEPTED))
        first.commit()
        stale_update = _terminal(EmailDeliveryOutcome.ACCEPTED).model_copy(
            update={"smtp_code": 251}
        )
        _apply_direct_outcome(stale_attempt, stale_update)
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    finally:
        first.close()
        stale.close()

    with factory() as check:
        persisted = check.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        assert persisted.outcome == EmailDeliveryOutcome.ACCEPTED.value
        assert persisted.smtp_code == 250


def test_repository_rejects_stale_terminal_transition(
    factory: sessionmaker[Session],
) -> None:
    with factory() as seed:
        draft = _draft(seed, "stale-repository")
        attempt = EmailDeliveryAttemptRepository(seed).reserve(_create(draft.id, "a"))
        seed.commit()
        attempt_id = attempt.id

    first = factory()
    stale = factory()
    try:
        assert first.get(EmailDeliveryAttempt, attempt_id).outcome == "RESERVED"
        assert stale.get(EmailDeliveryAttempt, attempt_id).outcome == "RESERVED"
        EmailDeliveryAttemptRepository(first).transition(
            attempt_id,
            _terminal(EmailDeliveryOutcome.ACCEPTED),
        )
        first.commit()
        with pytest.raises(ValueError, match="transition is invalid"):
            EmailDeliveryAttemptRepository(stale).transition(
                attempt_id,
                _terminal(EmailDeliveryOutcome.UNKNOWN),
            )
        stale.rollback()
    finally:
        first.close()
        stale.close()

    with factory() as check:
        persisted = check.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        assert persisted.outcome == EmailDeliveryOutcome.ACCEPTED.value


def test_competing_repository_transitions_have_exactly_one_winner(
    factory: sessionmaker[Session],
) -> None:
    with factory() as seed:
        draft = _draft(seed, "competing-repository")
        attempt = EmailDeliveryAttemptRepository(seed).reserve(_create(draft.id, "a"))
        seed.commit()
        attempt_id = attempt.id

    loaded = Barrier(2)
    winner_committed = Event()
    results: Queue[tuple[str, str]] = Queue()

    def transition(outcome: EmailDeliveryOutcome, *, winner: bool) -> None:
        with factory() as session:
            attempt = session.get(EmailDeliveryAttempt, attempt_id)
            assert attempt is not None
            assert attempt.outcome == EmailDeliveryOutcome.RESERVED.value
            loaded.wait()
            if not winner:
                winner_committed.wait()
            try:
                EmailDeliveryAttemptRepository(session).transition(
                    attempt_id,
                    _terminal(outcome),
                )
                session.commit()
            except ValueError:
                session.rollback()
                results.put((outcome.value, "stale"))
            else:
                results.put((outcome.value, "committed"))
            finally:
                if winner:
                    winner_committed.set()

    first = Thread(
        target=transition,
        args=(EmailDeliveryOutcome.TRANSIENT_FAILURE,),
        kwargs={"winner": True},
    )
    second = Thread(
        target=transition,
        args=(EmailDeliveryOutcome.PERMANENT_FAILURE,),
        kwargs={"winner": False},
    )
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive()
    assert not second.is_alive()
    assert {results.get_nowait(), results.get_nowait()} == {
        (EmailDeliveryOutcome.TRANSIENT_FAILURE.value, "committed"),
        (EmailDeliveryOutcome.PERMANENT_FAILURE.value, "stale"),
    }

    with factory() as check:
        persisted = check.get(EmailDeliveryAttempt, attempt_id)
        assert persisted is not None
        assert persisted.outcome == EmailDeliveryOutcome.TRANSIENT_FAILURE.value
