from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = {
    "DATABASE_URL": "postgresql://u:p@localhost:5432/db",
    "OPENAI_API_KEY": "sk-test",
    "PINECONE_API_KEY": "pc-test",
}

PROD_REQUIRED = {
    "ENV": "prod",
    "CLERK_JWKS_URL": "https://clerk.example.com/.well-known/jwks.json",
    "CLERK_ISSUER": "https://clerk.example.com",
    "R2_ACCOUNT_ID": "acct",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
    "CRON_SECRET": "cron",
    "ALLOWED_ORIGINS": "https://famguard.vercel.app",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in ("ENV", "DATABASE_URL", "OPENAI_API_KEY", "PINECONE_API_KEY", "FAKE_EXTERNALS",
                "ALLOWED_ORIGINS", "RUN_MODE", "PORT", "SMTP_URL", "LOCAL_STORAGE_DIR"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_defaults(env: pytest.MonkeyPatch) -> None:
    _set(env, BASE)
    s = _settings()
    assert s.port == 8000
    assert s.pinecone_namespace == "family-vault"
    assert s.pinecone_index == "famguard-vault"
    assert s.env == "dev"
    assert s.allowed_origins == ["http://localhost:3000"]


@pytest.mark.parametrize("missing", ["DATABASE_URL", "OPENAI_API_KEY", "PINECONE_API_KEY"])
def test_missing_required_var_raises(env: pytest.MonkeyPatch, missing: str) -> None:
    _set(env, {k: v for k, v in BASE.items() if k != missing})
    with pytest.raises(ValidationError):
        _settings()


def test_rejects_non_postgres_url(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, "DATABASE_URL": "mysql://u:p@localhost/db"})
    with pytest.raises(ValidationError, match="postgresql"):
        _settings()


def test_allowed_origins_comma_separated(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, "ALLOWED_ORIGINS": "https://a.app/, https://b.app"})
    assert _settings().allowed_origins == ["https://a.app", "https://b.app"]


def test_secrets_are_masked(env: pytest.MonkeyPatch) -> None:
    _set(env, BASE)
    assert "sk-test" not in repr(_settings())


def test_prod_requires_operational_settings(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, "ENV": "prod"})
    with pytest.raises(ValidationError, match="CLERK_JWKS_URL"):
        _settings()


def test_prod_valid(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, **PROD_REQUIRED})
    assert _settings().env == "prod"


def test_prod_worker_requires_resend(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, **PROD_REQUIRED, "RUN_MODE": "combined"})
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        _settings()


def test_prod_refuses_fake_externals(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, **PROD_REQUIRED, "FAKE_EXTERNALS": "1"})
    with pytest.raises(ValidationError, match="FAKE_EXTERNALS"):
        _settings()


def test_prod_refuses_wildcard_origin(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, **PROD_REQUIRED, "ALLOWED_ORIGINS": "*"})
    with pytest.raises(ValidationError, match="ALLOWED_ORIGINS"):
        _settings()


@pytest.mark.parametrize("dev_only", ["SMTP_URL", "LOCAL_STORAGE_DIR"])
def test_prod_refuses_dev_only_settings(env: pytest.MonkeyPatch, dev_only: str) -> None:
    _set(env, {**BASE, **PROD_REQUIRED, dev_only: "x"})
    with pytest.raises(ValidationError, match="dev-only"):
        _settings()


def test_invalid_port(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, "PORT": "70000"})
    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.parametrize(
    ("key", "value"),
    [("OPENAI_API_KEY", ""), ("PINECONE_API_KEY", ""),
     ("OPENAI_API_KEY", "sk-fake"), ("PINECONE_API_KEY", "pc-fake")],
)
def test_real_ai_rejects_missing_or_placeholder_keys(
    env: pytest.MonkeyPatch, key: str, value: str,
) -> None:
    _set(env, {**BASE, "FAKE_EXTERNALS": "0", key: value})
    with pytest.raises(ValidationError, match=key):
        _settings()


def test_offline_mode_allows_placeholder_keys(env: pytest.MonkeyPatch) -> None:
    _set(env, {**BASE, "FAKE_EXTERNALS": "1",
               "OPENAI_API_KEY": "sk-fake", "PINECONE_API_KEY": "pc-fake"})
    assert _settings().fake_externals
