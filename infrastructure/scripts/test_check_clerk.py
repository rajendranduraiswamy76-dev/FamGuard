from __future__ import annotations

import check_clerk
from app.security import VerifiedToken


def test_main_requires_clerk_configuration(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    assert check_clerk.main() == 2
    assert "must be set" in capsys.readouterr().err


def test_main_verifies_token_without_printing_it(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(check_clerk.getpass, "getpass", lambda _: "secret-token")
    monkeypatch.setattr(
        check_clerk,
        "verify_token",
        lambda *args, **kwargs: VerifiedToken(
            subject="user_123", email="user@example.com", name="Fam Guard"
        ),
    )

    assert check_clerk.main() == 0
    output = capsys.readouterr()
    assert "user_123" in output.out
    assert "secret-token" not in output.out
    assert "secret-token" not in output.err


def test_main_reports_missing_required_claim(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(check_clerk.getpass, "getpass", lambda _: "secret-token")
    monkeypatch.setattr(
        check_clerk,
        "verify_token",
        lambda *args, **kwargs: VerifiedToken(subject="user_123", email=None, name="Fam Guard"),
    )

    assert check_clerk.main() == 1
    assert "email" in capsys.readouterr().err
