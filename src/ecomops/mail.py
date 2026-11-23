"""IMAP inbox reader for the task pipeline.

Only the standard library is used: ``imaplib`` for the wire protocol and
``email`` for MIME parsing. Credentials live in session state for the browser
session and are never written to disk or logged — ``MailConfig`` hides the
password in its repr.

The reader is deliberately source-agnostic: :func:`fetch_from` takes any client
with imaplib's ``select/search/fetch/store/logout`` shape, so tests use a small
fake and never touch the network.
"""

from __future__ import annotations

import contextlib
import html
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_FOLDER = "INBOX"
DEFAULT_PORT = 993
DEFAULT_LIMIT = 20

_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")


class MailError(RuntimeError):
    """The inbox could not be read (bad settings, auth failure, no network)."""


@dataclass(frozen=True)
class MailConfig:
    """Connection settings for one mailbox."""

    host: str = ""
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = field(default="", repr=False)
    folder: str = DEFAULT_FOLDER
    limit: int = DEFAULT_LIMIT
    use_ssl: bool = True
    mark_seen: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise MailError("port must be between 1 and 65535")
        if not 1 <= self.limit <= 200:
            raise MailError("message limit must be between 1 and 200")

    @property
    def configured(self) -> bool:
        return bool(self.host.strip() and self.username.strip() and self.password)

    def redacted(self) -> dict[str, Any]:
        """A safe copy for display — the password is never echoed."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": "••••" if self.password else "",
            "folder": self.folder,
            "limit": self.limit,
            "use_ssl": self.use_ssl,
            "mark_seen": self.mark_seen,
        }


def config_from_env() -> MailConfig:
    """Build settings from ``ECOMOPS_MAIL_*`` environment variables."""

    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, "").strip())
        except (TypeError, ValueError):
            return default

    def _bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        if raw in {"1", "true", "yes", "on"}:
            return True
        return default

    return MailConfig(
        host=os.environ.get("ECOMOPS_MAIL_HOST", "").strip(),
        port=_int("ECOMOPS_MAIL_PORT", DEFAULT_PORT),
        username=os.environ.get("ECOMOPS_MAIL_USER", "").strip(),
        password=os.environ.get("ECOMOPS_MAIL_PASSWORD", ""),
        folder=os.environ.get("ECOMOPS_MAIL_FOLDER", DEFAULT_FOLDER).strip() or DEFAULT_FOLDER,
        limit=_int("ECOMOPS_MAIL_LIMIT", DEFAULT_LIMIT),
        use_ssl=_bool("ECOMOPS_MAIL_SSL", True),
        mark_seen=_bool("ECOMOPS_MAIL_MARK_SEEN", False),
    )


@dataclass(frozen=True)
class MailMessage:
    """One parsed inbox message, reduced to what task extraction needs."""

    message_id: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()
    subject: str = ""
    date: str = ""
    body: str = ""


class ImapClient(Protocol):  # pragma: no cover - structural typing
    def login(self, user: str, password: str) -> Any: ...

    def select(self, mailbox: str, readonly: bool = True) -> Any: ...

    def search(self, charset: str | None, *criteria: str) -> Any: ...

    def fetch(self, message_set: str, message_parts: str) -> Any: ...

    def store(self, message_set: str, command: str, flags: str) -> Any: ...

    def logout(self) -> Any: ...


def _header(message: Any, name: str) -> str:
    return str(message.get(name) or "").strip()


def _part_text(part: Any) -> str:
    """Decode one MIME part without ever raising on odd charsets."""
    try:
        value = part.get_content()
        if isinstance(value, str):
            return value
    except (KeyError, LookupError, UnicodeDecodeError, ValueError):
        pass
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = _TAG.sub(" ", value)
    return re.sub(r"[ \t]{2,}", " ", html.unescape(value))


def _clean(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def parse_message(raw: bytes) -> MailMessage:
    """Parse a raw RFC822 message into plain text (HTML is a fallback)."""
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain: list[str] = []
    rich: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            kind = part.get_content_type()
            if kind == "text/plain":
                plain.append(_part_text(part))
            elif kind == "text/html":
                rich.append(_strip_html(_part_text(part)))
    else:
        kind = message.get_content_type()
        text = _part_text(message)
        (plain if kind == "text/plain" else rich).append(
            text if kind == "text/plain" else _strip_html(text)
        )

    body = _clean("\n\n".join(plain) if plain else "\n\n".join(rich))
    to = _header(message, "To")
    recipients = tuple(part.strip() for part in to.split(",") if part.strip())
    return MailMessage(
        message_id=_header(message, "Message-ID"),
        sender=_header(message, "From"),
        recipients=recipients,
        subject=_header(message, "Subject"),
        date=_header(message, "Date"),
        body=body,
    )


def _first_body(data: Any) -> bytes | None:
    """Pull the RFC822 payload out of imaplib's fetch response."""
    for item in data or ():
        if isinstance(item, tuple) and len(item) == 2:
            payload = item[1]
            if isinstance(payload, (bytes, bytearray)):
                return bytes(payload)
    return None


def fetch_from(client: ImapClient, config: MailConfig) -> list[MailMessage]:
    """Read the newest messages using an existing (already logged-in) client.

    Newest first; ``mark_seen`` controls whether the read-only flag is dropped
    and a ``\\Seen`` flag is stored. Both are opt-in so a scan is non-destructive
    by default.
    """
    try:
        status, _ = client.select(config.folder, readonly=not config.mark_seen)
    except Exception as exc:
        raise MailError(f"cannot open mailbox {config.folder!r}: {exc}") from exc
    if str(status).upper() != "OK":
        raise MailError(f"cannot open mailbox {config.folder!r}: {status}")

    try:
        status, data = client.search(None, "ALL")
    except Exception as exc:
        raise MailError(f"inbox search failed: {exc}") from exc
    if str(status).upper() != "OK":
        raise MailError(f"inbox search failed: {status}")

    raw_ids = (data or [None])[0] or b""
    ids = raw_ids.split() if isinstance(raw_ids, (bytes, bytearray)) else []
    messages: list[MailMessage] = []
    for mid in reversed(ids[-config.limit :]):
        token = mid.decode() if isinstance(mid, (bytes, bytearray)) else str(mid)
        try:
            status, payload = client.fetch(token, "(RFC822)")
        except Exception as exc:
            raise MailError(f"cannot fetch message {token}: {exc}") from exc
        if str(status).upper() != "OK":
            continue
        body = _first_body(payload)
        if body is None:
            continue
        messages.append(parse_message(body))
        if config.mark_seen:
            with contextlib.suppress(Exception):
                client.store(token, "+FLAGS", "(\\Seen)")
    return messages


def _connect(config: MailConfig) -> ImapClient:
    import imaplib

    try:
        cls = imaplib.IMAP4_SSL if config.use_ssl else imaplib.IMAP4
        # Ports other than the implicit-TLS default need an explicit port.
        client = cls(config.host) if config.use_ssl and config.port == DEFAULT_PORT else cls(config.host, config.port)
        client.login(config.username, config.password)
    except Exception as exc:
        raise MailError(f"cannot connect to {config.host}:{config.port} as {config.username!r}: {exc}") from exc
    return client


def fetch(config: MailConfig, *, client: ImapClient | None = None) -> list[MailMessage]:
    """Read a mailbox. ``client`` is injectable for offline tests."""
    if not config.configured:
        raise MailError("inbox is not configured (host, user and password are required)")
    owner = client if client is not None else _connect(config)
    try:
        return fetch_from(owner, config)
    finally:
        if client is None:
            with contextlib.suppress(Exception):
                owner.logout()


__all__ = [
    "DEFAULT_FOLDER",
    "DEFAULT_LIMIT",
    "DEFAULT_PORT",
    "ImapClient",
    "MailConfig",
    "MailError",
    "MailMessage",
    "config_from_env",
    "fetch",
    "fetch_from",
    "parse_message",
]
