from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from freezegun import freeze_time

from app.repositories import DocumentRepo
from app.vectorstore import VectorStore
from famguard_worker.runtime import runtime
from famguard_worker.tasks.expiry_sweep import (
    DUMMY_VECTOR,
    ExpirySweep,
    SweepReport,
    expiry_sweep,
    sweep_filter,
    target_dates,
)

if TYPE_CHECKING:
    from tests.conftest import World

TODAY = date(2026, 12, 31)


def test_target_dates_cross_year_boundary() -> None:
    assert target_dates(TODAY) == {
        30: date(2027, 1, 30), 7: date(2027, 1, 7), 1: date(2027, 1, 1)
    }


def test_dummy_vector_is_valid_for_cosine() -> None:
    assert len(DUMMY_VECTOR) == 1536
    assert any(DUMMY_VECTOR)


def test_sweep_filter_shape() -> None:
    assert sweep_filter(date(2027, 1, 7)) == {
        "expiration_date": {"$eq": "2027-01-07"},
        "is_primary": {"$eq": True},
        "requires_alert": {"$eq": True},
    }


def _sweep(world: World, **kwargs: Any) -> ExpirySweep:
    return ExpirySweep(world.pool, VectorStore(world.index, "family-vault"), world.sender,
                       **kwargs)


def _run(world: World, today: date = TODAY, **kwargs: Any) -> SweepReport:
    return runtime.run(_sweep(world, **kwargs).run(today))


@pytest.mark.integration
def test_queries_pinecone_with_dummy_vector_and_exact_filters(world: World) -> None:
    _run(world)
    calls = world.index.query_calls
    assert [c["filter"]["expiration_date"]["$eq"] for c in calls] == [
        "2027-01-30", "2027-01-07", "2027-01-01"]
    for call in calls:
        assert len(call["vector"]) == 1536
        assert call["namespace"] == "family-vault"
        assert call["top_k"] == 1000
        assert call["filter"]["is_primary"] == {"$eq": True}
        assert "family_id" not in call["filter"]


@pytest.mark.integration
def test_digest_per_recipient_with_banners(world: World) -> None:
    assert world.member is not None
    ann = world.member
    john = world.add_member("John", "dependent", None)
    bob = world.add_member("Bob", "member", "bob@example.com")
    world.seed_document(john, "John Passport", TODAY + timedelta(days=7))
    world.seed_document(ann, "Ann License", TODAY + timedelta(days=1))
    world.seed_document(bob, "Bob Insurance", TODAY + timedelta(days=30), category="medical")
    world.seed_document(bob, "Bob Photo", None, requires_alert=False, category="photo")
    world.seed_document(john, "John ID (not due)", TODAY + timedelta(days=8))

    report = _run(world)
    assert report.matched_documents == 3
    assert report.emails_sent == 2
    by_to = {m.to: m for m in world.sender.sent}
    assert set(by_to) == {"ann@example.com", "bob@example.com"}

    ann_mail = by_to["ann@example.com"]
    assert "background:#fee2e2" in ann_mail.html
    for name in ("John Passport", "Ann License", "Bob Insurance"):
        assert name in ann_mail.html
    assert "John ID" not in ann_mail.html

    bob_mail = by_to["bob@example.com"]
    assert "Bob Insurance" in bob_mail.html and "John Passport" not in bob_mail.html
    assert "background:#fee2e2" not in bob_mail.html
    assert "background:#fef3c7" in bob_mail.html


@pytest.mark.integration
def test_rerun_same_day_sends_nothing(world: World) -> None:
    assert world.member is not None
    world.seed_document(world.member, "Ann Passport", TODAY + timedelta(days=7))
    first = _run(world)
    second = _run(world)
    assert first.emails_sent == 1
    assert second.emails_sent == 0
    assert second.alerts_already_sent == 1
    assert len(world.sender.sent) == 1


@pytest.mark.integration
def test_multi_chunk_documents_counted_once(world: World) -> None:
    assert world.member is not None
    world.seed_document(world.member, "Long Policy", TODAY + timedelta(days=7), chunks=4)
    report = _run(world)
    assert report.matched_documents == 1
    assert world.sender.sent[0].html.count("<strong>Long Policy</strong>") == 1


@pytest.mark.integration
def test_stale_vectors_are_ignored(world: World) -> None:
    assert world.member is not None
    deleted = world.seed_document(world.member, "Deleted Doc", TODAY + timedelta(days=7))
    moved = world.seed_document(world.member, "Renewed Doc", TODAY + timedelta(days=7))
    runtime.run(DocumentRepo(world.pool).delete(deleted.family_id, deleted.id))
    runtime.run(world.pool.execute(
        "UPDATE documents SET expiration_date = $1 WHERE id = $2",
        TODAY + timedelta(days=400), moved.id,
    ))
    report = _run(world)
    assert report.matched_documents == 0
    assert report.stale_vectors_skipped == 1
    assert world.sender.sent == []


@pytest.mark.integration
def test_top_k_overflow_reconciles_with_postgres(world: World) -> None:
    assert world.member is not None
    for i in range(3):
        world.seed_document(world.member, f"Doc {i}", TODAY + timedelta(days=1))
    report = _run(world, top_k=2)
    assert report.reconciled_from_postgres == 1
    assert report.matched_documents == 3
    for i in range(3):
        assert f"Doc {i}" in world.sender.sent[0].html


@pytest.mark.integration
def test_daily_cap_sends_most_urgent_first(world: World) -> None:
    assert world.member is not None
    for days, name in ((30, "Cara"), (7, "Dan"), (1, "Eli")):
        person = world.add_member(name, "member", f"{name.lower()}@example.com")
        world.seed_document(person, f"{name} Passport", TODAY + timedelta(days=days))
    report = _run(world, daily_cap=2)
    assert report.emails_sent == 2
    assert report.emails_deferred == 2
    assert [m.to for m in world.sender.sent] == ["ann@example.com", "eli@example.com"]


@pytest.mark.integration
def test_failed_recipient_is_retried_next_run(world: World) -> None:
    assert world.member is not None
    bob = world.add_member("Bob", "member", "bob@example.com")
    world.seed_document(bob, "Bob Passport", TODAY + timedelta(days=7))
    world.sender.fail_for.add("bob@example.com")
    first = _run(world)
    assert first.emails_failed == 1 and first.emails_sent == 1

    world.sender.fail_for.clear()
    second = _run(world)
    assert second.emails_sent == 1
    assert [m.to for m in world.sender.sent] == ["ann@example.com", "bob@example.com"]


@pytest.mark.integration
def test_quota_exhaustion_defers_remaining(world: World) -> None:
    from famguard_worker.email.sender import OutgoingEmail, QuotaExceededError

    assert world.member is not None
    bob = world.add_member("Bob", "member", "bob@example.com")
    world.seed_document(bob, "Bob Passport", TODAY + timedelta(days=7))

    async def quota(_: OutgoingEmail) -> str:
        raise QuotaExceededError("daily quota")

    world.sender.send = quota  # type: ignore[method-assign]
    report = _run(world)
    assert report.quota_exhausted is True
    assert report.emails_sent == 0 and report.emails_deferred == 2


@pytest.mark.integration
def test_always_reconcile_uses_postgres_when_vectors_missing(world: World) -> None:
    assert world.member is not None
    world.seed_document(world.member, "Ann Passport", TODAY + timedelta(days=7))
    world.index.namespaces.clear()
    assert _run(world).emails_sent == 0
    report = runtime.run(_sweep(world, always_reconcile=True).run(TODAY))
    assert report.emails_sent == 1


@pytest.mark.integration
def test_alert_log_records_every_item(world: World) -> None:
    assert world.member is not None
    doc = world.seed_document(world.member, "Ann Passport", TODAY + timedelta(days=30))
    _run(world)
    rows = runtime.run(world.pool.fetch("SELECT * FROM alert_log"))
    assert [(UUID(str(r["document_id"])), r["days_before"], r["recipient"]) for r in rows] == [
        (doc.id, 30, "ann@example.com")]
    assert rows[0]["resend_id"] == "mem-1"


@pytest.mark.integration
def test_celery_task_uses_today_in_configured_timezone(world: World) -> None:
    assert world.member is not None
    world.seed_document(world.member, "Ann Passport", date(2027, 1, 7))
    with freeze_time("2026-12-31 23:30:00"):
        result = expiry_sweep.delay().get()
    assert result["run_date"] == "2026-12-31"
    assert result["emails_sent"] == 1

    explicit = expiry_sweep.delay("2026-12-31").get()
    assert explicit["emails_sent"] == 0 and explicit["alerts_already_sent"] == 1
