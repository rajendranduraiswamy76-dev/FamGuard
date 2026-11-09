from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from app.repositories import (
    AlertLogRepo,
    CategoryAlreadyExistsError,
    CategoryRepo,
    ConflictError,
    DocumentRepo,
    DuplicateDocumentError,
    Family,
    FamilyMember,
    FamilyRepo,
    MemberRepo,
    NotFoundError,
    PendingReviewRepo,
)

pytestmark = pytest.mark.integration


async def _family_with_admin(pool: asyncpg.Pool, name: str) -> tuple[Family, FamilyMember]:
    family = await FamilyRepo(pool).create(name)
    admin = await MemberRepo(pool).create(
        family.id, display_name=f"{name} Admin", role="admin", email=f"{name}@example.com",
        clerk_user_id=f"user_{name}",
    )
    return family, admin


async def test_family_and_member_roundtrip(db_pool: asyncpg.Pool) -> None:
    family, admin = await _family_with_admin(db_pool, "Smith")
    members = MemberRepo(db_pool)
    kid = await members.create(family.id, display_name="John", role="dependent")

    assert await FamilyRepo(db_pool).get(family.id) == family
    assert await members.get(family.id, kid.id) == kid
    assert await members.get_by_clerk_user_id("user_Smith") == admin
    assert {m.display_name for m in await members.list_all(family.id)} == {"Smith Admin", "John"}
    assert [a.id for a in await members.list_admins(family.id)] == [admin.id]

    with pytest.raises(ConflictError):
        await members.create(family.id, display_name="John", role="member")


async def test_member_lookup_is_family_scoped(db_pool: asyncpg.Pool) -> None:
    fam_a, admin_a = await _family_with_admin(db_pool, "A")
    fam_b, _ = await _family_with_admin(db_pool, "B")
    assert await MemberRepo(db_pool).get(fam_b.id, admin_a.id) is None
    assert await MemberRepo(db_pool).get(fam_a.id, admin_a.id) is not None


async def test_custom_categories(db_pool: asyncpg.Pool) -> None:
    fam_a, _ = await _family_with_admin(db_pool, "A")
    fam_b, _ = await _family_with_admin(db_pool, "B")
    repo = CategoryRepo(db_pool)

    created = await repo.create_custom(fam_a.id, slug="vehicle", display_name="Vehicle")
    assert created.is_baseline is False

    assert "vehicle" in await repo.list_slugs(fam_a.id)
    assert "vehicle" not in await repo.list_slugs(fam_b.id)
    assert len(await repo.list_slugs(fam_b.id)) == 7
    assert await repo.exists(fam_a.id, "medical")

    with pytest.raises(CategoryAlreadyExistsError):
        await repo.create_custom(fam_a.id, slug="vehicle", display_name="Again")
    with pytest.raises(CategoryAlreadyExistsError):
        await repo.create_custom(fam_a.id, slug="medical", display_name="Shadow")
    with pytest.raises(ValueError):
        await repo.create_custom(fam_a.id, slug="Bad Slug", display_name="x")
    # Another family may reuse the same custom slug.
    await repo.create_custom(fam_b.id, slug="vehicle", display_name="Car")


async def test_documents_isolation_and_lifecycle(db_pool: asyncpg.Pool) -> None:
    fam_a, admin_a = await _family_with_admin(db_pool, "A")
    fam_b, admin_b = await _family_with_admin(db_pool, "B")
    docs = DocumentRepo(db_pool)

    doc = await docs.create(
        fam_a.id, family_member_id=admin_a.id, category_slug="miscellaneous",
        document_name="pending", source="upload", thread_id="th-1", sha256="a" * 64,
    )
    assert doc.status == "processing"
    assert await docs.get(fam_b.id, doc.id) is None
    assert await docs.get_by_thread(fam_a.id, "th-1") == doc

    with pytest.raises(DuplicateDocumentError) as dup:
        await docs.create(fam_a.id, family_member_id=admin_a.id, category_slug="x",
                          document_name="y", source="upload", sha256="a" * 64)
    assert dup.value.existing_document_id == doc.id

    # A member from another family cannot own a document here (composite FK).
    with pytest.raises(NotFoundError):
        await docs.create(fam_a.id, family_member_id=admin_b.id, category_slug="x",
                          document_name="y", source="upload")

    done = await docs.mark_completed(
        fam_a.id, doc.id, category_slug="identitycards", document_name="Passport",
        expiration_date=date(2027, 1, 7), requires_alert=True, financial_value=12.5,
        chunk_count=2,
    )
    assert done.status == "completed"
    assert done.financial_value == Decimal("12.50")

    with pytest.raises(NotFoundError):
        await docs.update_status(fam_b.id, doc.id, "failed")

    expiring = await docs.list_alertable_expiring_on_all_families(date(2027, 1, 7))
    assert [d.id for d in expiring] == [doc.id]

    assert await docs.delete(fam_b.id, doc.id) is False
    assert await docs.delete(fam_a.id, doc.id) is True


async def test_pending_review_flow(db_pool: asyncpg.Pool) -> None:
    fam_a, admin_a = await _family_with_admin(db_pool, "A")
    fam_b, admin_b = await _family_with_admin(db_pool, "B")
    reviews = PendingReviewRepo(db_pool)
    meta = {"category": "identitycards", "expiration_date": None, "requires_alert": True}

    created = await reviews.create(
        fam_a.id, thread_id="th-r", family_member_id=admin_a.id,
        original_filename="id.jpg", raw_text="ID CARD", suggested_metadata=meta,
        confidence_score=0.4, review_reasons=["expiration_unparsed"],
    )
    assert created.suggested_metadata == meta
    assert created.review_reasons == ["expiration_unparsed"]

    assert await reviews.count_open(fam_a.id) == 1
    assert await reviews.list_open(fam_b.id) == []
    assert await reviews.list_open(fam_a.id, family_member_id=uuid4()) == []
    assert len(await reviews.list_open(fam_a.id, family_member_id=admin_a.id)) == 1

    with pytest.raises(NotFoundError):
        await reviews.resolve(fam_b.id, "th-r", decision="approved", resolved_by=admin_b.id)

    resolved = await reviews.resolve(fam_a.id, "th-r", decision="approved",
                                     resolved_by=admin_a.id)
    assert resolved.status == "approved"
    with pytest.raises(ConflictError):
        await reviews.resolve(fam_a.id, "th-r", decision="rejected", resolved_by=admin_a.id)


async def test_alert_log_idempotent_and_scoped(db_pool: asyncpg.Pool) -> None:
    fam_a, admin_a = await _family_with_admin(db_pool, "A")
    fam_b, _ = await _family_with_admin(db_pool, "B")
    doc = await DocumentRepo(db_pool).create(
        fam_a.id, family_member_id=admin_a.id, category_slug="identitycards",
        document_name="Passport", source="bulk", status="completed",
    )
    log = AlertLogRepo(db_pool)

    assert await log.record(fam_a.id, doc.id, 7, "A@Example.com") is True
    assert await log.record(fam_a.id, doc.id, 7, "a@example.com") is False
    assert await log.was_sent(fam_a.id, doc.id, 7, "a@example.com")
    assert not await log.was_sent(fam_b.id, doc.id, 7, "a@example.com")
    assert await log.record(fam_b.id, doc.id, 1, "x@example.com") is False
