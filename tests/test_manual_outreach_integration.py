import threading

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.email_delivery.manual_models import ManualEmailSendRecord
from app.modules.email_delivery.manual_repository import ManualEmailSendRecordRepository
from app.modules.email_delivery.manual_service import (
    ManualOutreachAlreadySentError,
    ManualOutreachAutomaticAttemptError,
    ManualOutreachService,
)
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
)

from .test_email_delivery_service import (
    RecordingTransport,
    _command,
    _records,
    _sender,
)
from .test_manual_outreach_service import _confirmed


class BarrierManualRepository(ManualEmailSendRecordRepository):
    def __init__(self, session, barrier: threading.Barrier) -> None:
        super().__init__(session)
        self.barrier = barrier
        self.waited = False

    def get_by_email_draft_id(self, email_draft_id: int):
        result = super().get_by_email_draft_id(email_draft_id)
        if not self.waited:
            self.waited = True
            self.barrier.wait(timeout=10)
        return result


def test_concurrent_manual_confirmation_persists_exactly_one_record() -> None:
    ids = _records()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        with SessionLocal() as session:
            service = ManualOutreachService(
                session,
                BarrierManualRepository(session, barrier),
            )
            try:
                service.mark_sent(_confirmed(ids))
                session.commit()
            except ManualOutreachAlreadySentError:
                outcome = "loser"
            else:
                outcome = "winner"
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["loser", "winner"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ManualEmailSendRecord)) == 1


class BarrierAttemptRepository(EmailDeliveryAttemptRepository):
    def __init__(self, session, barrier: threading.Barrier) -> None:
        super().__init__(session)
        self.barrier = barrier
        self.waited = False

    def get_by_email_draft_id(self, email_draft_id: int):
        result = super().get_by_email_draft_id(email_draft_id)
        if not self.waited:
            self.waited = True
            self.barrier.wait(timeout=10)
        return result


def test_concurrent_manual_and_automatic_modes_allow_exactly_one_winner() -> None:
    ids = _records()
    barrier = threading.Barrier(2)
    transport = RecordingTransport()
    outcomes: list[str] = []
    lock = threading.Lock()

    def manual_worker() -> None:
        with SessionLocal() as session:
            service = ManualOutreachService(
                session,
                BarrierManualRepository(session, barrier),
            )
            try:
                service.mark_sent(_confirmed(ids))
                session.commit()
            except (ManualOutreachAlreadySentError, ManualOutreachAutomaticAttemptError):
                outcome = "manual-loser"
            else:
                outcome = "manual-winner"
            with lock:
                outcomes.append(outcome)

    def automatic_worker() -> None:
        with SessionLocal() as session:
            try:
                ConfirmedEmailSendService(
                    session=session,
                    repository=BarrierAttemptRepository(session, barrier),
                    transport=transport,
                    sender=_sender(),
                ).send(_command(ids))
            except EmailDeliveryAlreadyAttemptedError:
                outcome = "automatic-loser"
            else:
                outcome = "automatic-winner"
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=manual_worker), threading.Thread(target=automatic_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(outcome.endswith("winner") for outcome in outcomes) == 1
    with SessionLocal() as session:
        manual_count = session.scalar(select(func.count()).select_from(ManualEmailSendRecord))
        automatic_count = session.scalar(select(func.count()).select_from(EmailDeliveryAttempt))
    assert manual_count + automatic_count == 1
    assert len(transport.calls) == automatic_count
