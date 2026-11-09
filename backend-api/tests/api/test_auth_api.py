from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.main import create_app
from app.repositories import FamilyRepo, MemberRepo
from app.security import assert_member_access, get_db, get_verifier
from tests.helpers import auth, make_principal, make_token, make_verifier


@pytest.fixture
def app_no_db() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(db_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_pool
    app.dependency_overrides[get_verifier] = lambda: make_verifier()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_auth_not_configured_returns_503(app_no_db: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app_no_db)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/me", headers=auth(make_token()))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"


async def test_missing_token_returns_401_envelope(app_no_db: FastAPI) -> None:
    app_no_db.dependency_overrides[get_verifier] = lambda: make_verifier()
    app_no_db.dependency_overrides[get_db] = lambda: object()
    transport = httpx.ASGITransport(app=app_no_db)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/me")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_database_not_ready_returns_503(app_no_db: FastAPI) -> None:
    app_no_db.dependency_overrides[get_verifier] = lambda: make_verifier()
    transport = httpx.ASGITransport(app=app_no_db)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/me", headers=auth(make_token()))
    assert resp.status_code == 503
    assert resp.json()["error"]["message"] == "Database unavailable"


@pytest.mark.integration
async def test_invalid_token_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/me", headers=auth(make_token(expires_in=-60)))
    assert resp.status_code == 401


@pytest.mark.integration
async def test_first_login_bootstraps_family_once(
    client: httpx.AsyncClient, db_pool: asyncpg.Pool
) -> None:
    token = make_token("user_ann", email="ann@example.com", name="Ann")
    first, second = await asyncio.gather(
        client.get("/api/me", headers=auth(token)), client.get("/api/me", headers=auth(token))
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["family"]["id"] == second.json()["family"]["id"]
    body = first.json()
    assert body["member"]["role"] == "admin"
    assert body["member"]["display_name"] == "Ann"
    assert body["family"]["name"] == "Ann's Family"
    assert await db_pool.fetchval("SELECT count(*) FROM families") == 1


@pytest.mark.integration
async def test_admin_adds_members_and_member_cannot(client: httpx.AsyncClient) -> None:
    admin = auth(make_token("user_admin", email="admin@example.com", name="Admin"))
    resp = await client.post("/api/members", headers=admin,
                             json={"display_name": "John", "role": "dependent"})
    assert resp.status_code == 201
    assert resp.json()["has_login"] is False

    resp = await client.post("/api/members", headers=admin,
                             json={"display_name": "John", "role": "dependent"})
    assert resp.status_code == 409

    resp = await client.post("/api/members", headers=admin,
                             json={"display_name": "Bob", "role": "member"})
    assert resp.status_code == 422

    resp = await client.post("/api/members", headers=admin,
                             json={"display_name": "Bob", "role": "member",
                                   "email": "Bob@Example.com"})
    assert resp.status_code == 201

    # Bob signs in for the first time and claims the invite.
    bob = auth(make_token("user_bob", email="bob@example.com", name="Robert"))
    me = (await client.get("/api/me", headers=bob)).json()
    admin_me = (await client.get("/api/me", headers=admin)).json()
    assert me["family"]["id"] == admin_me["family"]["id"]
    assert me["member"]["role"] == "member"
    assert me["member"]["display_name"] == "Bob"
    assert {m["display_name"] for m in me["members"]} == {"Admin", "John", "Bob"}

    resp = await client.post("/api/members", headers=bob,
                             json={"display_name": "Eve", "role": "dependent"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.integration
async def test_invalid_member_payload(client: httpx.AsyncClient) -> None:
    admin = auth(make_token("user_admin2", name="A"))
    resp = await client.post("/api/members", headers=admin,
                             json={"display_name": "", "role": "owner"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["errors"]


@pytest.mark.integration
async def test_assert_member_access(db_pool: asyncpg.Pool) -> None:
    fam_a = await FamilyRepo(db_pool).create("A")
    fam_b = await FamilyRepo(db_pool).create("B")
    members = MemberRepo(db_pool)
    admin_a = await members.create(fam_a.id, display_name="AdminA", role="admin")
    kid_a = await members.create(fam_a.id, display_name="Kid", role="dependent")
    member_a = await members.create(fam_a.id, display_name="MemberA", role="member")
    admin_b = await members.create(fam_b.id, display_name="AdminB", role="admin")

    admin = make_principal("admin", family_id=fam_a.id, member_id=admin_a.id)
    member = make_principal("member", family_id=fam_a.id, member_id=member_a.id)
    outsider = make_principal("admin", family_id=fam_b.id, member_id=admin_b.id)

    assert (await assert_member_access(db_pool, admin, kid_a.id)).id == kid_a.id
    assert (await assert_member_access(db_pool, member, member_a.id)).id == member_a.id
    with pytest.raises(HTTPException) as forbidden:
        await assert_member_access(db_pool, member, kid_a.id)
    assert forbidden.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await assert_member_access(db_pool, outsider, kid_a.id)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException):
        await assert_member_access(db_pool, admin, uuid4())
