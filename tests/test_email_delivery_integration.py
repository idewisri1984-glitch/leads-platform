import socketserver
import threading
from email import policy
from email.parser import BytesParser

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    TrustedEmailSenderConfig,
)
from app.providers.smtp.client import SMTPClient
from app.providers.smtp.contracts import SMTPSecurityMode, SMTPTransportConfig

from .test_email_delivery_service import NOW, RecordingTransport, _command, _records, _sender


class BarrierRepository(EmailDeliveryAttemptRepository):
    def __init__(self, session, barrier: threading.Barrier) -> None:
        super().__init__(session)
        self.barrier = barrier

    def get_by_email_draft_id(self, email_draft_id: int):
        result = super().get_by_email_draft_id(email_draft_id)
        self.barrier.wait(timeout=10)
        return result


def test_concurrent_services_create_one_attempt_and_invoke_transport_once() -> None:
    ids = _records()
    barrier = threading.Barrier(2)
    transport = RecordingTransport()
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        with SessionLocal() as session:
            service = ConfirmedEmailSendService(
                session=session,
                repository=BarrierRepository(session, barrier),
                transport=transport,
                sender=_sender(),
                clock=lambda: NOW,
            )
            try:
                service.send(_command(ids))
            except EmailDeliveryAlreadyAttemptedError:
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
    assert len(transport.calls) == 1
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EmailDeliveryAttempt)) == 1


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _SMTPServer)
        self.wfile.write(b"220 localhost test SMTP\r\n")
        data_mode = False
        state = "CONNECTED"
        content: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line == b".\r\n":
                    server.messages.append(b"".join(content))
                    self.wfile.write(b"250 accepted\r\n")
                    data_mode = False
                    state = "DONE"
                    continue
                content.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.decode("ascii").strip()
            server.commands.append(command)
            upper = command.upper()
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-localhost\r\n250 HELP\r\n")
                state = "GREETED"
            elif upper.startswith("MAIL FROM:") and state == "GREETED":
                self.wfile.write(b"250 sender ok\r\n")
                state = "MAIL"
            elif upper.startswith("RCPT TO:") and state == "MAIL":
                self.wfile.write(b"250 recipient ok\r\n")
                state = "RCPT"
            elif upper == "DATA" and state == "RCPT":
                self.wfile.write(b"354 end with dot\r\n")
                data_mode = True
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"503 bad command sequence\r\n")


class _SMTPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SMTPHandler)
        self.commands: list[str] = []
        self.messages: list[bytes] = []


def test_confirmed_service_uses_real_adapter_once_against_loopback() -> None:
    ids = _records()
    server = _SMTPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = SMTPClient(
            SMTPTransportConfig(
                host="127.0.0.1",
                port=int(server.server_address[1]),
                security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
                username=None,
                password=None,
                connection_timeout_seconds=5.0,
            )
        )
        sender = TrustedEmailSenderConfig(
            envelope_from="bounce@example.test",
            header_from_email="sender@example.test",
            header_from_name="Alex Sender",
            reply_to="reply@example.test",
            message_id_domain="mail.example.test",
            transport_name="stdlib-smtp",
            security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
        )
        with SessionLocal() as session:
            result = ConfirmedEmailSendService(
                session=session,
                repository=EmailDeliveryAttemptRepository(session),
                transport=transport,
                sender=sender,
                clock=lambda: NOW,
            ).send(_command(ids))
            attempt = session.scalar(select(EmailDeliveryAttempt))
            assert attempt is not None
            assert result.outcome is EmailDeliveryOutcome.ACCEPTED
            assert attempt.outcome == EmailDeliveryOutcome.ACCEPTED.value
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(server.messages) == 1
    command_names = [command.split(" ", 1)[0].upper() for command in server.commands]
    assert command_names == ["EHLO", "MAIL", "RCPT", "DATA", "QUIT"]
    assert any(command.upper() == "MAIL FROM:<BOUNCE@EXAMPLE.TEST>" for command in server.commands)
    assert any(command.upper() == "RCPT TO:<RECIPIENT@EXAMPLE.TEST>" for command in server.commands)
    parsed = BytesParser(policy=policy.default).parsebytes(server.messages[0])
    assert parsed["From"].addresses[0].addr_spec == "sender@example.test"
    assert parsed["To"] == "recipient@example.test"
    assert parsed["Subject"] == "Reviewed subject"
    assert parsed["Message-ID"] == attempt.message_id
    assert (
        parsed.get_content().strip() == "Reviewed plain-text body with sufficient stable content."
    )
