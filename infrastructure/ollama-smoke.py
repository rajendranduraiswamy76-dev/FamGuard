"""Synthetic-only inference check, run using the deployed API's interpreter."""

from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from app.agents.extractor import get_extractor
from app.agents.rag_engine import OllamaAnswerGenerator, OllamaContextResolver
from app.config import get_settings
from app.embeddings import embed_query, get_embedder
from app.ollama import OllamaClient
from app.parsers import PreparedInput
from app.security import Principal
from app.vectorstore import SQLiteVectorIndex, VectorMatch, VectorStore, build_filter, build_records


async def main() -> None:
    settings = get_settings()
    assert settings.ai_provider == "ollama" and not settings.fake_externals
    failures: list[str] = []
    image = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    draw.multiline_text(
        (20, 20),
        "SYNTHETIC IDENTITY CARD\nHolder: Demo\n"
        "Expiry date: 2030-12-31\nNo real personal data",
        fill="black", font=font, spacing=18,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    result = await get_extractor(settings).extract(
        PreparedInput(images=[buffer.getvalue()]),
        categories=["identitycards", "miscellaneous"], filename="synthetic-identity-card.jpg",
    )
    if (str(result.extraction.expiration_date) == "2030-12-31"
            and result.extraction.requires_alert):
        print("FAMGUARD_VISION_PASS expiry=2030-12-31", flush=True)
    else:
        failures.append("vision expiry extraction")
        print(f"FAMGUARD_VISION_FAIL extraction={result.extraction}", flush=True)

    embedder = get_embedder(settings)
    text = result.extraction.extracted_text
    vector = (await embedder.embed([text]))[0]
    question = "When does Demo's identity card expire?"
    query_vector = await embed_query(embedder, question)
    assert len(vector) == 768
    assert len(query_vector) == 768
    with tempfile.TemporaryDirectory() as temp:
        store = VectorStore(
            SQLiteVectorIndex(Path(temp) / "smoke.sqlite3",
                              embedding_model=settings.ollama_embedding_model, dimensions=768),
            "smoke", dimensions=768,
        )
        principal = Principal("synthetic", uuid4(), uuid4(), "admin", "Demo", None)
        records = build_records(
            family_id=principal.family_id, family_member_id=principal.member_id,
            document_id=uuid4(), extraction=result.extraction, chunks=[text], vectors=[vector],
            source="upload", dimensions=768,
        )
        await store.upsert(records)
        assert len(await store.query(query_vector, filter=build_filter(principal))) == 1
        other = Principal("other", uuid4(), uuid4(), "admin", "Other", None)
        assert not await store.query(query_vector, filter=build_filter(other))
    print("FAMGUARD_EMBEDDING_PASS dimensions=768 tenant_isolation=pass", flush=True)

    client = OllamaClient(settings.ollama_url)
    resolution = await OllamaContextResolver(client, settings.ollama_chat_model).resolve(
        question, member_names=["Demo"], categories=["identitycards", "miscellaneous"]
    )
    if resolution.member_name != "Demo":
        failures.append("chat context resolution")
        print(f"FAMGUARD_RESOLVER_FAIL resolution={resolution}", flush=True)
    contexts = [VectorMatch(
        id="synthetic", score=1.0,
        metadata={"document_name": "Demo Identity Card", "text": text,
                  "category": "identitycards", "expiration_date": "2030-12-31"},
    )]
    answer = "".join([part async for part in OllamaAnswerGenerator(
        client, settings.ollama_chat_model
    ).stream(question, contexts)])
    assert "2030-12-31" in answer, answer
    print(f"FAMGUARD_CHAT_PASS answer={answer}", flush=True)
    if failures:
        raise RuntimeError("Inference checks failed: " + ", ".join(failures))


asyncio.run(main())
