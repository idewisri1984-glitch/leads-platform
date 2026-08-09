import socketserver
import threading
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser

from app.providers.smtp import (
    SMTPClient,
    SMTPMessageEnvelope,
    SMTPSecurityMode,
    SMTPSenderIdentity,
    SMTPTransportConfig,
)


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _SMTPServer)
        self.wfile.write(b"220 localhost test SMTP\r\n")
        data_mode = False
        content: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if data_mode:
                if line == b".\r\n":
                    server.message = b"".join(content)
                    self.wfile.write(b"250 accepted\r\n")
                    data_mode = False
                    continue
                content.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.decode("ascii").strip()
            server.commands.append(command)
            upper = command.upper()
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-localhost\r\n250 HELP\r\n")
            elif upper.startswith("MAIL FROM:"):
                self.wfile.write(b"250 sender ok\r\n")
            elif upper.startswith("RCPT TO:"):
                self.wfile.write(b"250 recipient ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 end with dot\r\n")
                data_mode = True
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"500 unsupported\r\n")


class _SMTPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SMTPHandler)
        self.commands: list[str] = []
        self.message: bytes | None = None


def test_real_adapter_sends_one_plain_text_message_to_loopback() -> None:
    server = _SMTPServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        transport = SMTPClient(
            SMTPTransportConfig(
                host="127.0.0.1",
                port=port,
                security_mode=SMTPSecurityMode.PLAINTEXT_LOCAL_TEST_ONLY,
                username=None,
                password=None,
                connection_timeout_seconds=5.0,
            )
        )
        receipt = transport.send(
            SMTPMessageEnvelope(
                envelope_from="sender@example.test",
                envelope_to="recipient@example.test",
                sender=SMTPSenderIdentity(
                    email="sender@example.test", display_name="Команда", reply_to=None
                ),
                subject="Stage 5B test",
                text_body="Plain-text integration message.",
                message_id="<stage5b-loopback@example.test>",
                date=datetime(2026, 8, 9, 6, tzinfo=UTC),
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert receipt.accepted is True
    assert any(command.upper() == "MAIL FROM:<SENDER@EXAMPLE.TEST>" for command in server.commands)
    assert any(command.upper() == "RCPT TO:<RECIPIENT@EXAMPLE.TEST>" for command in server.commands)
    assert server.message is not None
    parsed = BytesParser(policy=policy.default).parsebytes(server.message)
    assert parsed["From"].addresses[0].addr_spec == "sender@example.test"
    assert parsed["To"] == "recipient@example.test"
    assert parsed["Subject"] == "Stage 5B test"
    assert parsed["Message-ID"] == "<stage5b-loopback@example.test>"
    assert parsed["Date"] is not None
    assert parsed.get_content().strip() == "Plain-text integration message."
