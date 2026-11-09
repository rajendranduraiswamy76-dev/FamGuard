from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import bootstrap_pinecone as bootstrap
import pytest


class FakePinecone:
    def __init__(
        self,
        names: list[str] | None = None,
        *,
        dimension: int = bootstrap.DIMENSION,
        metric: str = bootstrap.METRIC,
    ) -> None:
        self.names = names or []
        self.create_arguments: dict[str, Any] | None = None
        self.dimension = dimension
        self.metric = metric

    def list_indexes(self) -> SimpleNamespace:
        return SimpleNamespace(names=lambda: self.names)

    def create_index(self, **kwargs: Any) -> None:
        self.create_arguments = kwargs
        self.names.append(kwargs["name"])

    def describe_index(self, name: str) -> SimpleNamespace:
        assert name in self.names
        return SimpleNamespace(dimension=self.dimension, metric=self.metric)


def test_creates_serverless_index_when_absent() -> None:
    client = FakePinecone()
    spec = object()

    created = bootstrap.ensure_index(client, "famguard-vault", spec)

    assert created
    assert client.create_arguments == {
        "name": "famguard-vault",
        "dimension": 1536,
        "metric": "cosine",
        "spec": spec,
    }


def test_existing_index_is_not_recreated() -> None:
    client = FakePinecone(["famguard-vault"])

    created = bootstrap.ensure_index(client, "famguard-vault", object())

    assert not created
    assert client.create_arguments is None


def test_rejects_existing_index_with_wrong_contract() -> None:
    client = FakePinecone(["famguard-vault"], dimension=1024)

    with pytest.raises(bootstrap.ProvisionError, match="expected dimension=1536"):
        bootstrap.ensure_index(client, "famguard-vault", object())
