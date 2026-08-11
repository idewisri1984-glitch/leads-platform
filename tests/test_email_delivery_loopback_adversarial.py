import socket
import socketserver
import threading
from enum import StrEnum

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.email_delivery.models import EmailDeliveryAttempt, EmailDeliveryOutcome
from app.modules.email_delivery.repository import EmailDeliveryAttemptRepository
from app.modules.email_delivery.service import (
    ConfirmedEmailSendService,
    EmailDeliveryAlreadyAttemptedError,
    EmailDeliveryUnknownOutcomeError,
)
from app.providers.smtp.client import SMTPClient
from app.providers.smtp.contracts import SMTPSecurityMode, SMTPTransportConfig
from app.providers.smtp.fake import FakeSMTPTransport

from .test_email_delivery_service import NOW, _command, _records, _sender, _service


class FailureMode(StrEnum):
    DROP_BEFORE_DATA = "DROP_BEFORE_DATA"
    DROP_AFTER_DATA = "DROP_AFTER_DATA"
    MALFORMED_AFTER_DATA = "MALFORMED_AFTER_DATA"
    TIMEOUT_AFTER_DATA = "TIMEOUT_AFTER_DATA"


class _AdversarialHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _AdversarialServer)
        self.wfile.write(b"220 localhost test SMTP\r\n")
        data_mode = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line != b".\r\n":
                    server.body_seen = True
                    continue
                server.data_complete.set()
                if server.mode is FailureMode.DROP_AFTER_DATA:
                    return
                if server.mode is FailureMode.MALFORMED_AFTER_DATA:
                    self.wfile.write(b"not-an-smtp-response\r\n")
                    return
                server.release.wait(timeout=2)
                return
            command = line.decode("ascii").strip().upper()
            if command.startswith("EHLO"):
                self.wfile.write(b"250-localhost\r\n250 HELP\r\n")
            elif command.startswith("MAIL FROM:"):
                self.wfile.write(b"250 sender ok\r\n")
            elif command.startswith("RCPT TO:"):
                self.wfile.write(b"250 recipient ok\r\n")
            elif command == "DATA":
                if server.mode is FailureMode.DROP_BEFORE_DATA:
                    return
                self.wfile.write(b"354 end with dot\r\n")
                data_mode = True
            elif command == "QUIT":
                return


class _AdversarialServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, mode: FailureMode) -> None:
        super().__init__(("127.0.0.1", 0), _AdversarialHandler)
        self.mode = mode
        self.body_seen = False
        self.data_complete = threading.Event()
        self.release = threading.Event()


@pytest.mark.parametrize("mode", list(FailureMode))
def test_loopback_ambiguity_is_unknown_and_never_retried(
    monkeypatch: pytest.MonkeyPatch, mode: FailureMode
) -> None:
    ids = _records()
    server = _AdversarialServer(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    external_dns: list[str] = []
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host != "127.0.0.1":
            external_dns.append(host)
            raise AssertionError("external DNS is forbidden")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    transport = SMTPClient(
        SMTPTransportConfig(
            host="127.0.0.1",
            port=int(server.server_address[1]),
            security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
            username=None,
            password=None,
            connection_timeout_seconds=0.1,
        )
    )
    sender = _sender(
        transport_name="stdlib-smtp",
        security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
    )
    try:
        with SessionLocal() as session, pytest.raises(EmailDeliveryUnknownOutcomeError):
            ConfirmedEmailSendService(
                session=session,
                repository=EmailDeliveryAttemptRepository(session),
                transport=transport,
                sender=sender,
                clock=lambda: NOW,
            ).send(_command(ids))
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert external_dns == []
    if mode is not FailureMode.DROP_BEFORE_DATA:
        assert server.body_seen is True
        assert server.data_complete.is_set()
    with SessionLocal() as fresh:
        attempt = fresh.scalar(select(EmailDeliveryAttempt))
        assert attempt is not None
        assert attempt.outcome == EmailDeliveryOutcome.UNKNOWN.value
        fresh.rollback()
        second = FakeSMTPTransport()
        with pytest.raises(EmailDeliveryAlreadyAttemptedError):
            _service(fresh, second).send(_command(ids))
        assert second.calls == []


def test_socket_guard_rejects_every_non_loopback_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_connections: list[object] = []
    real_create_connection = socket.create_connection

    def guarded_create_connection(address, *args: object, **kwargs: object):
        if address[0] != "127.0.0.1":
            external_connections.append(address)
            raise AssertionError("external socket is forbidden")
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    with pytest.raises(AssertionError, match="external socket"):
        socket.create_connection(("smtp.example.test", 25), timeout=0.01)
    assert external_connections == [("smtp.example.test", 25)]
