"""Daily expiry sweep: find documents expiring in exactly 30/7/1 days and email digests."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from celery import Task

from app.config import get_settings
from app.embeddings import EMBEDDING_DIMENSIONS
from app.repositories import (
    AlertLogRepo,
    AlertOffset,
    Document,
    DocumentRepo,
    FamilyMember,
    MemberRepo,
)
from app.tasks import EXPIRY_SWEEP_TASK
from app.vectorstore import MAX_TOP_K_WITH_METADATA, VectorStore
from famguard_worker.email.sender import (
    EmailDeliveryError,
    EmailSender,
    OutgoingEmail,
    QuotaExceededError,
)
from famguard_worker.email.templates import AlertItem, render_alert_email
from famguard_worker.runtime import runtime
from famguard_worker.worker import app

logger = logging.getLogger(__name__)

ALERT_OFFSETS: Final[tuple[AlertOffset, ...]] = (30, 7, 1)
# Cosine similarity rejects all-zero vectors; ranking is irrelevant because only the filter matters.
DUMMY_VECTOR: Final = [0.0] * (EMBEDDING_DIMENSIONS - 1) + [1.0]
SWEEP_TOP_K: Final = MAX_TOP_K_WITH_METADATA
DEFAULT_DAILY_SEND_CAP: Final = 95


def target_dates(today: date) -> dict[AlertOffset, date]:
    return {offset: today + timedelta(days=offset) for offset in ALERT_OFFSETS}


def sweep_filter(target: date) -> dict[str, Any]:
    return {
        "expiration_date": {"$eq": target.isoformat()},
        "is_primary": {"$eq": True},
        "requires_alert": {"$eq": True},
    }


@dataclass
class SweepReport:
    run_date: str
    matched_documents: int = 0
    reconciled_from_postgres: int = 0
    stale_vectors_skipped: int = 0
    alerts_already_sent: int = 0
    emails_sent: int = 0
    emails_deferred: int = 0
    emails_failed: int = 0
    quota_exhausted: bool = False


@dataclass
class _Digest:
    email: str
    name: str
    entries: list[tuple[AlertItem, UUID, AlertOffset]] = field(default_factory=list)

    @property
    def most_urgent(self) -> int:
        return min(item.days_remaining for item, _, _ in self.entries)


def idempotency_key(today: date, email: str, entries: Sequence[tuple[AlertItem, UUID, int]]) -> str:
    parts = sorted(f"{item.document_id}:{offset}" for item, _, offset in entries)
    raw = f"{today.isoformat()}|{email}|{'|'.join(parts)}"
    return f"famguard-expiry-{hashlib.sha256(raw.encode()).hexdigest()[:40]}"


class ExpirySweep:
    def __init__(
        self,
        pool: asyncpg.Pool,
        vectors: VectorStore,
        sender: EmailSender,
        *,
        daily_cap: int = DEFAULT_DAILY_SEND_CAP,
        top_k: int = SWEEP_TOP_K,
        always_reconcile: bool = False,
    ) -> None:
        self._pool = pool
        self._vectors = vectors
        self._sender = sender
        self._daily_cap = daily_cap
        self._top_k = top_k
        self._always_reconcile = always_reconcile
        self._admins: dict[UUID, list[FamilyMember]] = {}

    async def find_document_ids(self, target: date, report: SweepReport) -> set[UUID]:
        matches = await self._vectors.query(
            DUMMY_VECTOR, filter=sweep_filter(target), top_k=self._top_k, tenant_scoped=False,
            query_text="Documents with expiration dates",
        )
        ids = {
            UUID(str(m.metadata["document_id"])) for m in matches if m.metadata.get("document_id")
        }
        if self._always_reconcile or len(matches) >= self._top_k:
            if not self._always_reconcile:
                logger.warning(
                    "expiry sweep hit top_k, reconciling with Postgres",
                    extra={"target_date": target.isoformat(), "top_k": self._top_k},
                )
            extra = {
                d.id
                for d in await DocumentRepo(self._pool).list_alertable_expiring_on_all_families(
                    target
                )
            }
            report.reconciled_from_postgres += len(extra - ids)
            ids |= extra
        return ids

    async def _recipients(self, document: Document) -> dict[str, str]:
        members = MemberRepo(self._pool)
        recipients: dict[str, str] = {}
        owner = await members.get(document.family_id, document.family_member_id)
        if owner is not None and owner.email:
            recipients[owner.email.lower()] = owner.display_name
        if document.family_id not in self._admins:
            self._admins[document.family_id] = await members.list_admins(document.family_id)
        for admin in self._admins[document.family_id]:
            if admin.email:
                recipients.setdefault(admin.email.lower(), admin.display_name)
        return recipients

    async def _collect(self, today: date, report: SweepReport) -> dict[str, _Digest]:
        digests: dict[str, _Digest] = {}
        members = MemberRepo(self._pool)
        alert_log = AlertLogRepo(self._pool)
        for offset, target in target_dates(today).items():
            ids = await self.find_document_ids(target, report)
            for document in await DocumentRepo(self._pool).get_many_system(sorted(ids)):
                # Vector metadata can lag behind edits/deletions; Postgres is authoritative.
                if (
                    document.status != "completed"
                    or not document.requires_alert
                    or document.expiration_date != target
                ):
                    report.stale_vectors_skipped += 1
                    continue
                report.matched_documents += 1
                owner = await members.get(document.family_id, document.family_member_id)
                item = AlertItem(
                    document_id=str(document.id),
                    document_name=document.document_name,
                    member_name=owner.display_name if owner else "Family member",
                    category=document.category_slug,
                    expiration_date=target,
                    days_remaining=offset,
                )
                for email, name in (await self._recipients(document)).items():
                    if await alert_log.was_sent(document.family_id, document.id, offset, email):
                        report.alerts_already_sent += 1
                        continue
                    digest = digests.setdefault(email, _Digest(email=email, name=name))
                    digest.entries.append((item, document.family_id, offset))
        return digests

    async def run(self, today: date) -> SweepReport:
        report = SweepReport(run_date=today.isoformat())
        digests = await self._collect(today, report)
        alert_log = AlertLogRepo(self._pool)

        for digest in sorted(digests.values(), key=lambda d: (d.most_urgent, d.email)):
            if report.quota_exhausted or report.emails_sent >= self._daily_cap:
                report.emails_deferred += 1
                continue
            rendered = render_alert_email(digest.name, [item for item, _, _ in digest.entries])
            message = OutgoingEmail(
                to=digest.email,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
                idempotency_key=idempotency_key(today, digest.email, digest.entries),
            )
            try:
                provider_id = await self._sender.send(message)
            except QuotaExceededError:
                logger.error("email quota exhausted; remaining alerts deferred")
                report.quota_exhausted = True
                report.emails_deferred += 1
                continue
            except EmailDeliveryError as exc:
                logger.error(
                    "alert email failed",
                    extra={"error_type": type(exc).__name__},
                )
                report.emails_failed += 1
                continue
            for item, family_id, offset in digest.entries:
                await alert_log.record(
                    family_id, UUID(item.document_id), offset, digest.email, resend_id=provider_id
                )
            report.emails_sent += 1

        if report.emails_deferred:
            logger.warning(
                "alert emails deferred by daily cap or quota",
                extra={"deferred": report.emails_deferred, "cap": self._daily_cap},
            )
        logger.info("expiry sweep finished", extra=asdict(report))
        return report


def sweep_today(tz_name: str) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


@app.task(bind=True, name=EXPIRY_SWEEP_TASK, max_retries=2)
def expiry_sweep(self: Task, today_iso: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    today = date.fromisoformat(today_iso) if today_iso else sweep_today(settings.celery_timezone)
    resources = runtime.resources()
    sweep = ExpirySweep(
        resources.pool,
        resources.vectors,
        resources.sender,
        daily_cap=settings.alert_daily_send_cap,
        # Fake vectors live per process, so Postgres is the source of truth.
        always_reconcile=settings.fake_externals,
    )
    try:
        report = runtime.run(sweep.run(today))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "expiry sweep failed",
                extra={"run_date": today.isoformat(), "error_type": type(exc).__name__},
            )
            raise
        # Safe to retry: alerts already sent are skipped via alert_log.
        raise self.retry(exc=exc, countdown=300) from exc
    return asdict(report)
