from __future__ import annotations

import os
import re

import asyncpg
import pytest

from app import database
from app.database import (
    BASELINE_CATEGORIES,
    CATEGORY_SLUG_PATTERN,
    DDL_PENDING_REVIEWS,
    DDL_VAULT_CATEGORIES,
    SCHEMA_STATEMENTS,
    apply_schema,
)

EXPECTED_TABLES = {
    "families", "family_members", "vault_categories", "documents", "pending_reviews", "alert_log",
}
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.parametrize(
    "column", ["id", "family_id", "category_slug", "category_display_name"]
)
def test_vault_categories_has_spec_columns(column: str) -> None:
    assert re.search(rf"^\s+{column}\s", DDL_VAULT_CATEGORIES, re.MULTILINE)


@pytest.mark.parametrize(
    "column",
    ["id", "thread_id", "family_member_id", "original_filename", "raw_text",
     "suggested_metadata", "confidence_score"],
)
def test_pending_reviews_has_spec_columns(column: str) -> None:
    assert re.search(rf"^\s+{column}\s", DDL_PENDING_REVIEWS, re.MULTILINE)


def test_baseline_categories() -> None:
    slugs = [slug for slug, _ in BASELINE_CATEGORIES]
    assert slugs == ["identitycards", "certificate", "income", "expense", "medical", "photo",
                     "miscellaneous"]
    assert all(re.fullmatch(CATEGORY_SLUG_PATTERN, s) for s in slugs)


def test_all_statements_idempotent() -> None:
    for stmt in SCHEMA_STATEMENTS:
        assert "IF NOT EXISTS" in stmt


@pytest.mark.integration
async def test_apply_schema_twice_is_idempotent(db_pool: asyncpg.Pool) -> None:
    await apply_schema(db_pool)  # second application (fixture already applied once)

    tables = {
        r["table_name"]
        for r in await db_pool.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    }
    assert EXPECTED_TABLES <= tables
    baseline = await db_pool.fetch(
        "SELECT category_slug FROM vault_categories WHERE is_baseline ORDER BY category_slug"
    )
    assert len(baseline) == 7


@pytest.mark.integration
async def test_slug_check_constraint(db_pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute(
            "INSERT INTO vault_categories (family_id, category_slug, category_display_name) "
            "VALUES (NULL, 'Bad Slug', 'x')"
        )


def test_migrate_cli_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as info:
        database.main(["--migrate"])
    assert info.value.code == 2


@pytest.mark.integration
def test_migrate_cli_applies_schema_twice() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    assert database.main(["--migrate", "--database-url", TEST_DATABASE_URL]) == 0
    assert database.main(["--migrate", "--database-url", TEST_DATABASE_URL]) == 0
