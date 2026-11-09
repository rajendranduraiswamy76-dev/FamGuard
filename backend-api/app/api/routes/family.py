from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.schemas import FamilyOut, MemberCreate, MemberOut, MeOut
from app.database import transaction
from app.repositories import FamilyRepo, MemberRepo
from app.security import AdminPrincipal, CurrentPrincipal, Principal, get_db

router = APIRouter(prefix="/api", tags=["family"])

Db = Annotated[asyncpg.Pool, Depends(get_db)]


@asynccontextmanager
async def _admin_members(pool: asyncpg.Pool, principal: Principal) -> AsyncIterator[MemberRepo]:
    async with transaction(pool) as conn:
        # Serialize family management and recheck the actor after acquiring the lock.
        family = await conn.fetchrow(
            "SELECT id FROM families WHERE id = $1 FOR UPDATE", principal.family_id
        )
        if family is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Family not found")
        repo = MemberRepo(conn)
        actor = await repo.get(principal.family_id, principal.member_id)
        if actor is None or actor.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
        yield repo


def _validate_login_email(body: MemberCreate) -> None:
    if body.role in ("admin", "member") and body.email is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Members who can sign in need an email so their login can be linked",
        )


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentPrincipal, pool: Db) -> MeOut:
    family = await FamilyRepo(pool).get(principal.family_id)
    if family is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Family not found")
    members = await MemberRepo(pool).list_all(principal.family_id)
    me_row = next((m for m in members if m.id == principal.member_id), None)
    if me_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return MeOut(
        member=MemberOut.from_member(me_row),
        family=FamilyOut.from_family(family),
        members=[MemberOut.from_member(m) for m in members],
    )


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def create_member(body: MemberCreate, principal: AdminPrincipal, pool: Db) -> MemberOut:
    _validate_login_email(body)
    async with _admin_members(pool, principal) as repo:
        member = await repo.create(
            principal.family_id,
            display_name=body.display_name,
            role=body.role,
            email=str(body.email) if body.email else None,
            relationship=body.relationship,
        )
    return MemberOut.from_member(member)


@router.put("/members/{member_id}", response_model=MemberOut)
async def update_member(
    member_id: UUID, body: MemberCreate, principal: AdminPrincipal, pool: Db,
) -> MemberOut:
    _validate_login_email(body)
    if member_id == principal.member_id and body.role != "admin":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot change your own admin role"
        )
    async with _admin_members(pool, principal) as repo:
        member = await repo.update(
            principal.family_id, member_id, display_name=body.display_name,
            role=body.role, email=str(body.email) if body.email else None,
            relationship=body.relationship,
        )
    return MemberOut.from_member(member)


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(member_id: UUID, principal: AdminPrincipal, pool: Db) -> Response:
    if member_id == principal.member_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot delete your own admin account"
        )
    async with _admin_members(pool, principal) as repo:
        await repo.delete(principal.family_id, member_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
