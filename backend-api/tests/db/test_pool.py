from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app import database
from app.database import (
    DatabaseUnavailableError,
    PoolNotInitializedError,
    create_pool_with_retry,
    get_pool,
)


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


async def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel: Any = object()
    create = AsyncMock(side_effect=[OSError("refused"), TimeoutError(), sentinel])
    monkeypatch.setattr(asyncpg, "create_pool", create)
    sleeps = _Sleeps()

    pool = await create_pool_with_retry("postgresql://x", max_attempts=5, base_delay_s=0.5,
                                        sleep=sleeps)

    assert pool is sentinel
    assert create.await_count == 3
    assert sleeps.calls == [0.5, 1.0]
    assert create.await_args is not None
    assert create.await_args.kwargs["statement_cache_size"] == 0


async def test_backoff_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(side_effect=OSError("down")))
    sleeps = _Sleeps()
    with pytest.raises(DatabaseUnavailableError):
        await create_pool_with_retry("postgresql://x", max_attempts=6, base_delay_s=4,
                                     max_delay_s=10, sleep=sleeps)
    assert sleeps.calls == [4, 8, 10, 10, 10]


async def test_exhausted_retries_raise_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    create = AsyncMock(side_effect=asyncpg.CannotConnectNowError("starting"))
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(DatabaseUnavailableError) as info:
        await create_pool_with_retry("postgresql://x", max_attempts=3, sleep=_Sleeps())
    assert create.await_count == 3
    assert isinstance(info.value.__cause__, asyncpg.CannotConnectNowError)


async def test_non_transient_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    create = AsyncMock(side_effect=asyncpg.InvalidPasswordError("bad password"))
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(asyncpg.InvalidPasswordError):
        await create_pool_with_retry("postgresql://x", max_attempts=5, sleep=_Sleeps())
    assert create.await_count == 1


async def test_init_and_close_pool_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=fake_pool))
    monkeypatch.setattr(database, "_pool", None)

    with pytest.raises(PoolNotInitializedError):
        get_pool()
    first = await database.init_pool("postgresql://x")
    second = await database.init_pool("postgresql://x")
    assert first is second is get_pool()

    await database.close_pool()
    fake_pool.close.assert_awaited_once()
    with pytest.raises(PoolNotInitializedError):
        get_pool()


def test_migrate_cli_without_flag_prints_help() -> None:
    assert database.main([]) == 1
