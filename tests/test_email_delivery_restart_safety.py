import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_email_delivery_service import _records

_ROOT = Path(__file__).resolve().parents[1]

_PROCESS_SEND = r"""
import json
import os

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryAttempt
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendService,
    EmailDeliveryServiceError,
    TrustedEmailSenderConfig,
)
from app.providers.smtp.contracts import (
    SMTPDeliveryReceipt,
    SMTPSecurityMode,
)
from app.providers.smtp.errors import (
    SMTPAuthenticationFailedError,
    SMTPConnectionFailedError,
    SMTPDeliveryOutcomeUnknownError,
)

ids = json.loads(os.environ["STAGE5C4_IDS"])
scenario = os.environ["STAGE5C4_SCENARIO"]
marker = os.environ["STAGE5C4_MARKER"]

class Transport:
    def send(self, message):
        with open(marker, "a", encoding="ascii") as stream:
            stream.write("smtp\n")
        if scenario == "TRANSIENT_FAILURE":
            raise SMTPConnectionFailedError()
        if scenario == "PERMANENT_FAILURE":
            raise SMTPAuthenticationFailedError()
        if scenario == "UNKNOWN":
            raise SMTPDeliveryOutcomeUnknownError()
        return SMTPDeliveryReceipt(
            accepted=True,
            recipient=message.envelope_to,
            message_id=message.message_id,
            smtp_code=250,
            provider="fake-smtp",
            security_mode=SMTPSecurityMode.STARTTLS,
        )

sender = TrustedEmailSenderConfig(
    envelope_from="bounce@example.test",
    header_from_email="sender@example.test",
    header_from_name="Alex Sender",
    reply_to="reply@example.test",
    message_id_domain="mail.example.test",
    transport_name="fake-smtp",
    security_mode=SMTPSecurityMode.STARTTLS,
)
command = ConfirmedEmailSendCommand(
    project_id=ids[0],
    company_id=ids[1],
    contact_id=ids[2],
    email_draft_id=ids[3],
    confirmed=True,
)
with SessionLocal() as session:
    service = ConfirmedEmailSendService(
        session=session,
        repository=EmailDeliveryAttemptRepository(session),
        transport=Transport(),
        sender=sender,
    )
    try:
        service.send(command)
    except EmailDeliveryServiceError:
        pass
with SessionLocal() as session:
    attempt = session.scalar(select(EmailDeliveryAttempt))
    assert attempt is not None
    print(attempt.outcome)
"""

_PROCESS_BLOCKED_RETRY = r"""
import json
import os

from app.core.database import SessionLocal
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    TrustedEmailSenderConfig,
)
from app.providers.smtp.contracts import SMTPSecurityMode

ids = json.loads(os.environ["STAGE5C4_IDS"])

class ForbiddenTransport:
    def send(self, message):
        raise AssertionError("SMTP must not be called")

sender = TrustedEmailSenderConfig(
    envelope_from="bounce@example.test",
    header_from_email="sender@example.test",
    header_from_name="Alex Sender",
    reply_to="reply@example.test",
    message_id_domain="mail.example.test",
    transport_name="fake-smtp",
    security_mode=SMTPSecurityMode.STARTTLS,
)
command = ConfirmedEmailSendCommand(
    project_id=ids[0],
    company_id=ids[1],
    contact_id=ids[2],
    email_draft_id=ids[3],
    confirmed=True,
)
with SessionLocal() as session:
    service = ConfirmedEmailSendService(
        session=session,
        repository=EmailDeliveryAttemptRepository(session),
        transport=ForbiddenTransport(),
        sender=sender,
    )
    try:
        service.send(command)
    except EmailDeliveryAlreadyAttemptedError as error:
        print(error.outcome)
    else:
        raise AssertionError("retry was not blocked")
"""

_PROCESS_RESERVE_ONLY = r"""
import json
import os
from datetime import UTC, datetime

from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.schemas import EmailDeliveryAttemptCreate
from app.modules.email_delivery.service import build_delivery_attempt_key, build_delivery_message_id
from app.modules.email_draft.models import EmailDraft

ids = json.loads(os.environ["STAGE5C4_IDS"])
with SessionLocal() as session:
    draft = session.get(EmailDraft, ids[3])
    assert draft is not None
    key = build_delivery_attempt_key(
        project_id=draft.project_id,
        email_draft_id=draft.id,
        content_hash=draft.content_hash,
        recipient_email=draft.recipient_email,
        message_id_domain="mail.example.test",
    )
    EmailDeliveryAttemptRepository(session).reserve(
        EmailDeliveryAttemptCreate(
            email_draft_id=draft.id,
            attempt_key=key,
            outcome=EmailDeliveryOutcome.RESERVED,
            recipient_email=draft.recipient_email,
            envelope_from="bounce@example.test",
            header_from_email="sender@example.test",
            header_from_name="Alex Sender",
            reply_to="reply@example.test",
            message_id=build_delivery_message_id(key, "mail.example.test"),
            content_hash=draft.content_hash,
            transport_name="fake-smtp",
            security_mode="STARTTLS",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
print("RESERVED")
"""

_PROCESS_TX2_CRASH = _PROCESS_SEND.replace(
    'scenario = os.environ["STAGE5C4_SCENARIO"]',
    'scenario = os.environ["STAGE5C4_SCENARIO"]',
).replace(
    "service = ConfirmedEmailSendService(",
    """real_commit = session.commit
    commit_count = 0
    def fail_tx2():
        global commit_count
        commit_count += 1
        if commit_count == 2:
            raise SQLAlchemyError(\"terminal commit failed\")
        real_commit()
    session.commit = fail_tx2
    service = ConfirmedEmailSendService(""",
    1,
)


def _environment(ids: tuple[int, int, int, int], marker: Path, scenario: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_ROOT)
    environment["STAGE5C4_IDS"] = json.dumps(ids)
    environment["STAGE5C4_MARKER"] = str(marker)
    environment["STAGE5C4_SCENARIO"] = scenario
    environment["DEBUG"] = "false"
    return environment


def _run(proof: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", proof],
        cwd=_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize(
    "outcome",
    ["ACCEPTED", "TRANSIENT_FAILURE", "PERMANENT_FAILURE", "UNKNOWN"],
)
def test_terminal_outcome_survives_process_restart_and_blocks_retry(
    tmp_path: Path, outcome: str
) -> None:
    ids = _records()
    marker = tmp_path / "smtp-calls.txt"
    environment = _environment(ids, marker, outcome)
    first = _run(_PROCESS_SEND, environment)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == outcome
    assert marker.read_text(encoding="ascii").splitlines() == ["smtp"]

    second = _run(_PROCESS_BLOCKED_RETRY, environment)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == outcome
    assert marker.read_text(encoding="ascii").splitlines() == ["smtp"]


def test_pre_send_reserved_crash_survives_restart_and_blocks_retry(tmp_path: Path) -> None:
    ids = _records()
    marker = tmp_path / "smtp-calls.txt"
    environment = _environment(ids, marker, "RESERVED")
    first = _run(_PROCESS_RESERVE_ONLY, environment)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "RESERVED"
    assert not marker.exists()

    second = _run(_PROCESS_BLOCKED_RETRY, environment)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "RESERVED"
    assert not marker.exists()


def test_post_smtp_pre_tx2_crash_survives_restart_without_resend(tmp_path: Path) -> None:
    ids = _records()
    marker = tmp_path / "smtp-calls.txt"
    environment = _environment(ids, marker, "ACCEPTED")
    first = _run(_PROCESS_TX2_CRASH, environment)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "RESERVED"
    assert marker.read_text(encoding="ascii").splitlines() == ["smtp"]

    second = _run(_PROCESS_BLOCKED_RETRY, environment)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "RESERVED"
    assert marker.read_text(encoding="ascii").splitlines() == ["smtp"]


_CONCURRENT_PROCESS = r"""
import json
import os
import time

from app.core.database import SessionLocal
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendCommand,
    ConfirmedEmailSendService,
    EmailDeliveryServiceError,
    TrustedEmailSenderConfig,
)
from app.providers.smtp.contracts import SMTPDeliveryReceipt, SMTPSecurityMode

ids = json.loads(os.environ["STAGE5C4_IDS"])
ready = os.environ["STAGE5C4_READY"]
release = os.environ["STAGE5C4_RELEASE"]
marker = os.environ["STAGE5C4_MARKER"]

class BarrierRepository(EmailDeliveryAttemptRepository):
    def get_by_email_draft_id(self, email_draft_id):
        result = super().get_by_email_draft_id(email_draft_id)
        with open(ready, "w", encoding="ascii") as stream:
            stream.write("ready")
        deadline = time.monotonic() + 10
        while not os.path.exists(release):
            if time.monotonic() >= deadline:
                raise RuntimeError("release timeout")
            time.sleep(0.01)
        return result

class Transport:
    def send(self, message):
        with open(marker, "a", encoding="ascii") as stream:
            stream.write("smtp\n")
        return SMTPDeliveryReceipt(
            accepted=True,
            recipient=message.envelope_to,
            message_id=message.message_id,
            smtp_code=250,
            provider="fake-smtp",
            security_mode=SMTPSecurityMode.STARTTLS,
        )

sender = TrustedEmailSenderConfig(
    envelope_from="bounce@example.test",
    header_from_email="sender@example.test",
    header_from_name="Alex Sender",
    reply_to="reply@example.test",
    message_id_domain="mail.example.test",
    transport_name="fake-smtp",
    security_mode=SMTPSecurityMode.STARTTLS,
)
command = ConfirmedEmailSendCommand(
    project_id=ids[0], company_id=ids[1], contact_id=ids[2],
    email_draft_id=ids[3], confirmed=True,
)
with SessionLocal() as session:
    try:
        ConfirmedEmailSendService(
            session=session,
            repository=BarrierRepository(session),
            transport=Transport(),
            sender=sender,
        ).send(command)
    except EmailDeliveryServiceError as error:
        print(type(error).__name__)
    else:
        print("winner")
"""


def test_two_process_reservation_race_invokes_smtp_at_most_once(tmp_path: Path) -> None:
    ids = _records()
    marker = tmp_path / "smtp-calls.txt"
    release = tmp_path / "release"
    processes: list[subprocess.Popen[str]] = []
    ready_paths = [tmp_path / f"ready-{index}" for index in range(2)]
    for ready in ready_paths:
        environment = _environment(ids, marker, "ACCEPTED")
        environment["STAGE5C4_READY"] = str(ready)
        environment["STAGE5C4_RELEASE"] = str(release)
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", _CONCURRENT_PROCESS],
                cwd=_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    deadline = time.monotonic() + 10
    while not all(path.exists() for path in ready_paths):
        if time.monotonic() >= deadline:
            for process in processes:
                process.kill()
            raise AssertionError("process synchronization timed out")
        time.sleep(0.01)
    release.write_text("go", encoding="ascii")
    results = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    outcomes = sorted(stdout.strip() for stdout, _stderr in results)
    assert outcomes.count("winner") == 1
    assert len(outcomes) == 2
    assert marker.read_text(encoding="ascii").splitlines() == ["smtp"]
