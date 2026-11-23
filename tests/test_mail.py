"""IMAP inbox reader — MIME parsing and transport-free fetch tests."""

from __future__ import annotations

from email.message import EmailMessage
from email.policy import default

import pytest

from ecomops import mail


def _raw(message: EmailMessage) -> bytes:
    return message.as_bytes(policy=default)


def test_config_from_env_and_redaction(monkeypatch):
    monkeypatch.setenv("ECOMOPS_MAIL_HOST", "imap.example.com")
    monkeypatch.setenv("ECOMOPS_MAIL_USER", "ops@msm.local")
    monkeypatch.setenv("ECOMOPS_MAIL_PASSWORD", "topsecret")
    monkeypatch.setenv("ECOMOPS_MAIL_PORT", "1143")
    monkeypatch.setenv("ECOMOPS_MAIL_SSL", "false")
    monkeypatch.setenv("ECOMOPS_MAIL_LIMIT", "7")

    config = mail.config_from_env()
    assert config.configured
    assert config.port == 1143 and config.limit == 7 and not config.use_ssl
    assert "topsecret" not in repr(config)
    assert config.redacted()["password"] == "••••"
    assert config.redacted()["username"] == "ops@msm.local"


def test_config_defaults_and_validation(monkeypatch):
    for name in (
        "ECOMOPS_MAIL_HOST", "ECOMOPS_MAIL_USER", "ECOMOPS_MAIL_PASSWORD",
        "ECOMOPS_MAIL_PORT", "ECOMOPS_MAIL_SSL", "ECOMOPS_MAIL_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    config = mail.config_from_env()
    assert config.host == "" and not config.configured
    assert config.port == 993 and config.limit == 20 and config.use_ssl

    with pytest.raises(mail.MailError):
        mail.MailConfig(host="x", port=0)


def test_parse_plain_message():
    message = EmailMessage()
    message["From"] = "boss@msm.local"
    message["To"] = "ops@msm.local, staff@msm.local"
    message["Subject"] = "Actions for today"
    message["Message-ID"] = "<abc@example.com>"
    message.set_content("Please review the report\n- [ ] Call the carrier")

    parsed = mail.parse_message(_raw(message))
    assert parsed.sender == "boss@msm.local"
    assert parsed.recipients == ("ops@msm.local", "staff@msm.local")
    assert parsed.subject == "Actions for today"
    assert parsed.message_id == "<abc@example.com>"
    assert "Please review the report" in parsed.body


def test_parse_html_only_message_strips_markup():
    message = EmailMessage()
    message["Subject"] = "HTML"
    message.set_content("<html><body><p>Please <b>confirm</b> the order</p></body></html>", subtype="html")

    parsed = mail.parse_message(_raw(message))
    assert "<b>" not in parsed.body
    assert "Please confirm the order" in parsed.body


class FakeClient:
    """imaplib-shaped client; no network and no real credentials."""

    def __init__(self, raw: bytes, *, fail: str = ""):
        self.raw = raw
        self.fail = fail
        self.select_args = None
        self.stored: list[tuple] = []
        self.logged_out = False

    def login(self, user, password):
        if self.fail == "login":
            raise RuntimeError("bad password")
        return "OK", [b""]

    def select(self, mailbox, readonly=True):
        self.select_args = (mailbox, readonly)
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return "OK", [b"1 2 3"]

    def fetch(self, message_set, message_parts):
        return "OK", [(f"{message_set} (RFC822 {{{len(self.raw)}}}".encode(), self.raw), b")"]

    def store(self, message_set, command, flags):
        self.stored.append((message_set, command, flags))
        return "OK", [b""]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


def test_fetch_from_reads_newest_first_and_is_readonly_by_default():
    message = EmailMessage()
    message["Subject"] = "Do the thing"
    message.set_content("Please check the stock")
    client = FakeClient(_raw(message))
    config = mail.MailConfig(host="imap.example.com", username="ops@msm.local", password="msm", limit=2)

    result = mail.fetch(config, client=client)
    assert len(result) == 2
    assert all(item.subject == "Do the thing" for item in result)
    assert client.select_args == ("INBOX", True)
    assert client.stored == []
    assert not client.logged_out  # injected clients are owned by the caller


def test_fetch_from_can_mark_seen():
    message = EmailMessage()
    message.set_content("Please check the stock")
    client = FakeClient(_raw(message))
    config = mail.MailConfig(host="x", username="u", password="p", limit=1, mark_seen=True)

    mail.fetch(config, client=client)
    assert client.select_args == ("INBOX", False)
    assert client.stored == [("3", "+FLAGS", "(\\Seen)")]


def test_fetch_rejects_unconfigured_mailbox():
    with pytest.raises(mail.MailError, match="not configured"):
        mail.fetch(mail.MailConfig())
