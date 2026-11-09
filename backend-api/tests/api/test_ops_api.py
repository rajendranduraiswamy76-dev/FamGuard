from __future__ import annotations

import hashlib
import hmac
import io
import time
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from PIL import Image

from app.bulk import EXPECTED_HEADERS
from app.config import get_settings
from app.dependencies import provide_readiness_checks
from app.repositories import DocumentRepo, PendingReviewRepo
from app.tasks import EXPIRY_SWEEP_TASK
from tests.api_harness import ApiHarness
from tests.test_bulk import csv_bytes

pytestmark = pytest.mark.integration


async def _admin(api: ApiHarness) -> tuple[dict[str, str], dict[str, Any]]:
    return await api.login("user_ann", email="ann@example.com", name="Ann")


# ---------------------------------------------------------------------------
# Bulk upload endpoint
# ---------------------------------------------------------------------------


async def test_bulk_upload_endpoint(api: ApiHarness) -> None:
    headers, me = await _admin(api)
    member_id = me["member"]["id"]
    data = csv_bytes([[member_id, "identitycards", "Ann Passport", "P", "2031-01-01", ""],
                      [member_id, "nope", "Bad", "", "", ""]])
    resp = await api.client.post("/api/vault/bulk-upload", headers=headers,
                                 files={"file": ("family.csv", data, "text/csv")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] == 1 and body["rejected"] == 1
    assert body["errors"] == [{"row": 3, "reason": "Unknown category 'nope'"}]
    assert body["errors_truncated"] is False


async def test_bulk_upload_rejections(api: ApiHarness) -> None:
    headers, _ = await _admin(api)
    wrong_type = await api.client.post("/api/vault/bulk-upload", headers=headers,
                                       files={"file": ("data.xlsx", b"x", "application/zip")})
    assert wrong_type.status_code == 415

    big = b",".join(EXPECTED_HEADERS[i].encode() for i in range(6)) + b"\n" + b"a" * (6 << 20)
    too_big = await api.client.post("/api/vault/bulk-upload", headers=headers,
                                    files={"file": ("big.csv", big, "text/csv")})
    assert too_big.status_code == 413

    bad_headers = await api.client.post(
        "/api/vault/bulk-upload", headers=headers,
        files={"file": ("x.csv", b"Name,Date\nA,B\n", "text/csv")},
    )
    assert bad_headers.status_code == 422
    assert "FamilyMemberID" in bad_headers.json()["error"]["message"]

    latin1 = csv_bytes([]) + "caf\xe9".encode("latin-1")
    not_utf8 = await api.client.post("/api/vault/bulk-upload", headers=headers,
                                     files={"file": ("x.csv", latin1, "text/csv")})
    assert not_utf8.status_code == 200
    assert "Malformed CSV" in not_utf8.json()["errors"][0]["reason"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def test_metrics_scoped_by_role(api: ApiHarness) -> None:
    admin_h, me = await _admin(api)
    family_id, admin_id = UUID(me["family"]["id"]), UUID(me["member"]["id"])
    await api.client.post("/api/members", headers=admin_h,
                          json={"display_name": "Bob", "role": "member",
                                "email": "bob@example.com"})
    bob_h, bob = await api.login("user_bob", email="bob@example.com", name="Bob")
    bob_id = UUID(bob["member"]["id"])

    today = date.today()
    docs = DocumentRepo(api.pool)
    await docs.create(family_id, family_member_id=admin_id, category_slug="identitycards",
                      document_name="Ann Passport", source="bulk", status="completed",
                      expiration_date=today + timedelta(days=5), requires_alert=True,
                      chunk_count=2)
    await docs.create(family_id, family_member_id=bob_id, category_slug="medical",
                      document_name="Bob Insurance", source="bulk", status="completed",
                      expiration_date=today + timedelta(days=40), requires_alert=True,
                      chunk_count=3)
    await docs.create(family_id, family_member_id=bob_id, category_slug="expense",
                      document_name="Processing", source="upload", thread_id="t-1")
    review_doc = await docs.create(family_id, family_member_id=bob_id,
                                   category_slug="identitycards", document_name="ID",
                                   source="upload", thread_id="t-2", status="review_required")
    await PendingReviewRepo(api.pool).create(
        family_id, thread_id="t-2", family_member_id=bob_id, document_id=review_doc.id,
        original_filename="id.jpg", raw_text="x", suggested_metadata={}, confidence_score=0.4,
        review_reasons=["low_confidence"],
    )

    admin_metrics = (await api.client.get("/api/vault/metrics", headers=admin_h)).json()
    assert admin_metrics["documents_total"] == 2
    assert admin_metrics["vectors_total"] == 5
    assert {c["category"] for c in admin_metrics["by_category"]} == {"identitycards", "medical"}
    assert [u["document_name"] for u in admin_metrics["upcoming_expirations"]] == ["Ann Passport"]
    assert admin_metrics["upcoming_expirations"][0]["days_remaining"] == 5
    assert admin_metrics["upcoming_expirations"][0]["member_name"] == "Ann"
    assert admin_metrics["open_reviews"] == 1
    assert admin_metrics["processing"] == 2
    assert [item["document_name"] for item in admin_metrics["year_expirations"]] == ["Bob Insurance"]

    bob_metrics = (await api.client.get("/api/vault/metrics", headers=bob_h)).json()
    assert bob_metrics["documents_total"] == 1 and bob_metrics["vectors_total"] == 3
    assert bob_metrics["upcoming_expirations"] == []
    assert bob_metrics["open_reviews"] == 1
    assert [item["document_name"] for item in bob_metrics["year_expirations"]] == ["Bob Insurance"]


async def test_year_expiration_boundaries(api: ApiHarness) -> None:
    headers, me = await _admin(api)
    repo = DocumentRepo(api.pool)
    for days in (0, 29, 30, 31, 364, 365):
        await repo.create(
            UUID(me["family"]["id"]), family_member_id=UUID(me["member"]["id"]),
            category_slug="medical", document_name=f"day-{days}", source="bulk",
            status="completed", expiration_date=date.today() + timedelta(days=days),
            requires_alert=True,
        )
    response = await api.client.get("/api/vault/metrics", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [item["days_remaining"] for item in body["upcoming_expirations"]] == [0, 29, 30]
    assert [item["days_remaining"] for item in body["year_expirations"]] == [30, 31, 364]


# ---------------------------------------------------------------------------
# Document URL + delete
# ---------------------------------------------------------------------------


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return buf.getvalue()


async def _uploaded(api: ApiHarness, headers: dict[str, str], member_id: str) -> dict[str, Any]:
    resp = await api.client.post("/api/vault/upload", headers=headers,
                                 data={"family_member_id": member_id},
                                 files=[("files", ("a.png", _png(), "image/png"))])
    assert resp.status_code == 202
    return dict(resp.json()["uploads"][0])


async def test_signed_url_and_delete(api: ApiHarness) -> None:
    admin_h, me = await _admin(api)
    item = await _uploaded(api, admin_h, me["member"]["id"])
    doc_id = item["document_id"]
    key = next(iter(api.storage.objects))

    url = await api.client.get(f"/api/vault/documents/{doc_id}/url", headers=admin_h)
    assert url.status_code == 200
    assert url.json() == {"url": f"memory://{key}?expires=300", "expires_in": 300}

    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    assert (await api.client.get(f"/api/vault/documents/{doc_id}/url",
                                 headers=other_h)).status_code == 404
    assert (await api.client.delete(f"/api/vault/documents/{doc_id}",
                                    headers=other_h)).status_code == 404

    api.index.upsert(vectors=[{"id": f"{doc_id}#0", "values": [1.0] + [0.0] * 1535,
                               "metadata": {"document_id": doc_id}}], namespace="family-vault")
    await DocumentRepo(api.pool).update_status(UUID(me["family"]["id"]), UUID(doc_id), "completed")
    deleted = await api.client.delete(f"/api/vault/documents/{doc_id}", headers=admin_h)
    assert deleted.status_code == 204
    assert api.storage.objects == {}
    assert api.index.namespaces["family-vault"] == {}
    assert await DocumentRepo(api.pool).get(UUID(me["family"]["id"]), UUID(doc_id)) is None


async def test_delete_keeps_everything_when_vector_store_down(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_h, me = await _admin(api)
    item = await _uploaded(api, admin_h, me["member"]["id"])

    await DocumentRepo(api.pool).update_status(
        UUID(me["family"]["id"]), UUID(item["document_id"]), "failed",
    )
    async def down(_: Any) -> int:
        raise ConnectionError("pinecone down")

    monkeypatch.setattr(api.vectors, "delete_document", down)
    resp = await api.client.delete(f"/api/vault/documents/{item['document_id']}",
                                   headers=admin_h)
    assert resp.status_code == 503
    assert len(api.storage.objects) == 1
    assert await DocumentRepo(api.pool).get(UUID(me["family"]["id"]),
                                            UUID(item["document_id"])) is not None


async def test_member_cannot_delete_others_documents(api: ApiHarness) -> None:
    admin_h, me = await _admin(api)
    item = await _uploaded(api, admin_h, me["member"]["id"])
    await api.client.post("/api/members", headers=admin_h,
                          json={"display_name": "Bob", "role": "member",
                                "email": "bob@example.com"})
    bob_h, _ = await api.login("user_bob", email="bob@example.com", name="Bob")
    resp = await api.client.delete(f"/api/vault/documents/{item['document_id']}", headers=bob_h)
    assert resp.status_code == 403


@pytest.mark.parametrize("document_status", ["processing", "review_required"])
async def test_delete_blocks_unsettled_documents(api: ApiHarness, document_status: Any) -> None:
    headers, me = await _admin(api)
    item = await _uploaded(api, headers, me["member"]["id"])
    repo = DocumentRepo(api.pool)
    await repo.update_status(
        UUID(me["family"]["id"]), UUID(item["document_id"]), document_status,
    )
    response = await api.client.delete(
        f"/api/vault/documents/{item['document_id']}", headers=headers,
    )
    assert response.status_code == 409
    assert len(api.storage.objects) == 1
    assert await repo.get(UUID(me["family"]["id"]), UUID(item["document_id"])) is not None


# ---------------------------------------------------------------------------
# Cron trigger
# ---------------------------------------------------------------------------


def _sign(secret: str, ts: str) -> str:
    return hmac.new(secret.encode(), f"{ts}:expiry-sweep".encode(), hashlib.sha256).hexdigest()


async def test_cron_trigger(api: ApiHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRON_SECRET", "s3cret-value")
    get_settings.cache_clear()
    ts = str(int(time.time()))

    ok = await api.client.post("/internal/cron/expiry-sweep",
                               headers={"X-Cron-Timestamp": ts,
                                        "X-Cron-Signature": _sign("s3cret-value", ts)})
    assert ok.status_code == 202
    assert api.queue.sent[-1] == (EXPIRY_SWEEP_TASK, [])

    stale = str(int(time.time()) - 600)
    cases = [
        {},
        {"X-Cron-Timestamp": ts, "X-Cron-Signature": _sign("wrong", ts)},
        {"X-Cron-Timestamp": stale, "X-Cron-Signature": _sign("s3cret-value", stale)},
        {"X-Cron-Timestamp": "abc", "X-Cron-Signature": "00"},
    ]
    for headers in cases:
        resp = await api.client.post("/internal/cron/expiry-sweep", headers=headers)
        assert resp.status_code == 401, headers
    assert len(api.queue.sent) == 1


async def test_cron_trigger_disabled_without_secret(api: ApiHarness,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRON_SECRET", raising=False)
    get_settings.cache_clear()
    resp = await api.client.post("/internal/cron/expiry-sweep",
                                 headers={"X-Cron-Timestamp": "1", "X-Cron-Signature": "x"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Readiness, CORS, security headers, rate limits
# ---------------------------------------------------------------------------


async def test_readyz(api: ApiHarness) -> None:
    async def ok() -> bool:
        return True

    async def broken() -> bool:
        raise ConnectionError("secret-host:6379 refused")

    api.app.dependency_overrides[provide_readiness_checks] = lambda: {
        "postgres": ok, "redis": ok, "pinecone": ok}
    resp = await api.client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok",
                                                      "pinecone": "ok"}}

    api.app.dependency_overrides[provide_readiness_checks] = lambda: {
        "postgres": ok, "redis": broken, "pinecone": ok}
    resp = await api.client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"] == "error"
    assert "secret-host" not in resp.text


async def test_cors_allowlist(api: ApiHarness) -> None:
    preflight = {"Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization"}
    allowed = await api.client.options("/api/vault/chat",
                                       headers={"Origin": "http://localhost:3000", **preflight})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = await api.client.options("/api/vault/chat",
                                      headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in denied.headers


async def test_security_headers(api: ApiHarness) -> None:
    resp = await api.client.get("/healthz")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert "Strict-Transport-Security" not in resp.headers  # prod only


async def test_chat_rate_limit(api: ApiHarness) -> None:
    headers, _ = await _admin(api)
    for _ in range(30):
        resp = await api.client.post("/api/vault/chat", headers=headers, json={"message": "hi"})
        assert resp.status_code == 200
    limited = await api.client.post("/api/vault/chat", headers=headers, json={"message": "hi"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["Retry-After"] == "60"
    # Limits are per user.
    other_h, _ = await api.login("user_eve", email="eve@example.com", name="Eve")
    assert (await api.client.post("/api/vault/chat", headers=other_h,
                                  json={"message": "hi"})).status_code == 200
