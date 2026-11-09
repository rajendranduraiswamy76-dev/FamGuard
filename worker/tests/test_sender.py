from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
import resend
from resend import exceptions as resend_errors

from famguard_worker.email.sender import (
    EmailDeliveryError,
    OutgoingEmail,
    QuotaExceededError,
    ResendSender,
    SmtpSender,
)

MESSAGE = OutgoingEmail(to="ann@example.com", subject="s", html="<p>h</p>", text="t",
                        idempotency_key="famguard-expiry-abc")


class _FakeEmails:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    def send(self, params: dict[str, Any], options: dict[str, Any] | None = None) -> Any:
        self.calls.append((params, options))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def _no_sleep(_: float) -> None:
    return None


def _sender(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> tuple[ResendSender,
                                                                           _FakeEmails]:
    fake = _FakeEmails(outcomes)
    monkeypatch.setattr(resend, "Emails", fake)
    return ResendSender("re_test", "FamGuard <alerts@famguard.test>", sleep=_no_sleep), fake


def _rate_limited(error_type: str = "rate_limit_exceeded") -> Exception:
    return resend_errors.RateLimitError("slow down", error_type, 429)


async def test_send_passes_idempotency_key(monkeypatch: pytest.MonkeyPatch) -> None:
    sender, fake = _sender(monkeypatch, [{"id": "re_123"}])
    assert await sender.send(MESSAGE) == "re_123"
    params, options = fake.calls[0]
    assert params["to"] == ["ann@example.com"]
    assert params["from"] == "FamGuard <alerts@famguard.test>"
    assert params["html"] == "<p>h</p>" and params["text"] == "t"
    assert options == {"idempotency_key": "famguard-expiry-abc"}


async def test_rate_limit_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sender, fake = _sender(monkeypatch, [_rate_limited(), {"id": "re_ok"}])
    assert await sender.send(MESSAGE) == "re_ok"
    assert len(fake.calls) == 2


async def test_server_errors_exhaust_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    err = resend_errors.ApplicationError("boom", "application_error", 500)
    sender, fake = _sender(monkeypatch, [err, err, err])
    with pytest.raises(EmailDeliveryError, match="after 3 attempts"):
        await sender.send(MESSAGE)
    assert len(fake.calls) == 3


async def test_validation_error_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    err = resend_errors.ValidationError("bad to", "validation_error", 422)
    sender, fake = _sender(monkeypatch, [err])
    with pytest.raises(EmailDeliveryError) as info:
        await sender.send(MESSAGE)
    assert not isinstance(info.value, QuotaExceededError)
    assert len(fake.calls) == 1


async def test_daily_quota_stops_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    sender, fake = _sender(monkeypatch, [_rate_limited("daily_quota_exceeded")])
    with pytest.raises(QuotaExceededError):
        await sender.send(MESSAGE)
    assert len(fake.calls) == 1


async def test_network_error_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sender, fake = _sender(monkeypatch, [ConnectionError("reset"), {"id": "re_net"}])
    assert await sender.send(MESSAGE) == "re_net"
    assert len(fake.calls) == 2


class _FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []
    fail = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.address = (host, port)

    def __enter__(self) -> _FakeSMTP:
        if _FakeSMTP.fail:
            raise ConnectionRefusedError
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def send_message(self, msg: EmailMessage) -> None:
        assert self.address == ("mailpit", 1025)
        _FakeSMTP.sent.append(msg)


async def test_smtp_sender_builds_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.sent.clear()
    _FakeSMTP.fail = False
    sender = SmtpSender("smtp://mailpit:1025", "")
    message_id = await sender.send(MESSAGE)
    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "ann@example.com" and msg["Message-ID"] == message_id
    assert msg["From"] == "FamGuard <alerts@famguard.local>"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]

    _FakeSMTP.fail = True
    with pytest.raises(EmailDeliveryError):
        await sender.send(MESSAGE)


def test_smtp_sender_rejects_bad_url() -> None:
    with pytest.raises(ValueError):
        SmtpSender("http://mailpit:1025", "")
