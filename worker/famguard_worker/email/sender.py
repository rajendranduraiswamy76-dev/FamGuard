"""Email delivery through the Resend SDK, plus an in-memory sender for tests/fake mode."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import resend
from resend import exceptions as resend_errors

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS: Final = 3
_QUOTA_ERROR_TYPES: Final = frozenset({"daily_quota_exceeded", "monthly_quota_exceeded"})


class EmailDeliveryError(Exception):
    """The message was rejected and should not be retried (bad address, validation...)."""


class QuotaExceededError(EmailDeliveryError):
    """The provider quota is exhausted; stop sending for this run."""


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    html: str
    text: str
    idempotency_key: str


class EmailSender(Protocol):
    async def send(self, message: OutgoingEmail) -> str: ...


class ResendSender:
    def __init__(
        self,
        api_key: str,
        from_address: str,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_delay_s: float = 2.0,
    ) -> None:
        resend.api_key = api_key
        self._from = from_address
        self._sleep = sleep
        self._base_delay_s = base_delay_s

    def _send_sync(self, message: OutgoingEmail) -> Any:
        params: Any = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
            "tags": [{"name": "category", "value": "expiry_alert"}],
        }
        return resend.Emails.send(params, {"idempotency_key": message.idempotency_key})

    async def send(self, message: OutgoingEmail) -> str:
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            try:
                response = await asyncio.to_thread(self._send_sync, message)
                return str(response["id"])
            except resend_errors.RateLimitError as exc:
                if getattr(exc, "error_type", "") in _QUOTA_ERROR_TYPES:
                    raise QuotaExceededError(str(exc)) from exc
                retryable: BaseException = exc
            except resend_errors.ApplicationError as exc:
                retryable = exc
            except resend_errors.ResendError as exc:
                raise EmailDeliveryError(f"Resend rejected the message: {exc}") from exc
            except (OSError, TimeoutError) as exc:
                retryable = exc
            if attempt == MAX_SEND_ATTEMPTS:
                raise EmailDeliveryError(
                    f"Resend unavailable after {MAX_SEND_ATTEMPTS} attempts"
                ) from retryable
            logger.warning("resend send failed, retrying",
                           extra={"attempt": attempt, "error_type": type(retryable).__name__})
            await self._sleep(self._base_delay_s * 2 ** (attempt - 1))
        raise AssertionError("unreachable")


@dataclass
class InMemorySender:
    sent: list[OutgoingEmail] = field(default_factory=list)
    fail_for: set[str] = field(default_factory=set)

    async def send(self, message: OutgoingEmail) -> str:
        if message.to in self.fail_for:
            raise EmailDeliveryError(f"rejected {message.to}")
        self.sent.append(message)
        return f"mem-{len(self.sent)}"


class SmtpSender:
    """Dev-only delivery to an SMTP catcher (e.g. Mailpit); no TLS or auth."""

    def __init__(self, url: str, from_address: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "smtp" or not parsed.hostname:
            raise ValueError("SMTP_URL must look like smtp://host:port")
        self._host = parsed.hostname
        self._port = parsed.port or 25
        self._from = from_address or "FamGuard <alerts@famguard.local>"

    def _send_sync(self, message: OutgoingEmail) -> str:
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg["Message-ID"] = make_msgid(idstring=message.idempotency_key[-16:])
        msg.set_content(message.text)
        msg.add_alternative(message.html, subtype="html")
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            client.send_message(msg)
        return str(msg["Message-ID"])

    async def send(self, message: OutgoingEmail) -> str:
        try:
            return await asyncio.to_thread(self._send_sync, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError(f"SMTP delivery failed: {type(exc).__name__}") from exc


def get_sender(settings: Settings) -> EmailSender:
    if settings.env != "prod" and settings.smtp_url:
        return SmtpSender(settings.smtp_url, settings.alert_from_email)
    if settings.fake_externals or not settings.resend_api_key.get_secret_value():
        logger.warning("RESEND_API_KEY not set: alert emails are captured in memory only")
        return InMemorySender()
    return ResendSender(settings.resend_api_key.get_secret_value(), settings.alert_from_email)
