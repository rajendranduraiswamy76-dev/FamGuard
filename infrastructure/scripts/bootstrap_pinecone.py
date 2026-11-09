"""Create or validate the FamGuard Pinecone serverless index."""

from __future__ import annotations

import os
import sys
from typing import Any

DEFAULT_INDEX = "famguard-vault"
DIMENSION = 1536
METRIC = "cosine"
REGION = "us-east-1"


class ProvisionError(Exception):
    """The configured Pinecone index does not match the FamGuard contract."""


def ensure_index(client: Any, index_name: str, serverless_spec: Any) -> bool:
    """Ensure the index exists and has the required dimension and metric.

    Returns True when the index was created and False when it already existed.
    """
    existing = index_name in client.list_indexes().names()
    if not existing:
        client.create_index(
            name=index_name,
            dimension=DIMENSION,
            metric=METRIC,
            spec=serverless_spec,
        )

    description = client.describe_index(index_name)
    dimension = getattr(description, "dimension", None)
    metric = getattr(description, "metric", None)
    if dimension != DIMENSION or metric != METRIC:
        raise ProvisionError(
            f"Index {index_name!r} has dimension={dimension!r}, metric={metric!r}; "
            f"expected dimension={DIMENSION}, metric={METRIC}"
        )
    return not existing


def main() -> int:
    api_key = os.environ.get("PINECONE_API_KEY", "")
    index_name = os.environ.get("PINECONE_INDEX", DEFAULT_INDEX).strip()
    if not api_key:
        print("PINECONE_API_KEY must be set", file=sys.stderr)
        return 2
    if not index_name:
        print("PINECONE_INDEX must not be empty", file=sys.stderr)
        return 2

    from pinecone import Pinecone, ServerlessSpec

    client = Pinecone(api_key=api_key)
    created = ensure_index(
        client,
        index_name,
        ServerlessSpec(cloud="aws", region=REGION),
    )
    action = "Created" if created else "Index already exists"
    print(f"{action}: {index_name} (dimension={DIMENSION}, metric={METRIC})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
