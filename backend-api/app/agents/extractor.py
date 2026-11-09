"""GPT-4o Vision metadata extraction (Node 1 of the ingestion graph)."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.agents.prompts import (
    EXTRACTION_TEXT_WRAPPER,
    EXTRACTION_USER_PROMPT,
    render_extraction_system_prompt,
)
from app.agents.state import UnifiedVaultExtraction
from app.config import Settings
from app.parsers import PreparedInput

EXTRACTION_MODEL = "gpt-4o"


class ExtractionError(Exception):
    """The model response could not be turned into a valid extraction."""


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    extraction: UnifiedVaultExtraction
    expiration_raw: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class MetadataExtractor(Protocol):
    async def extract(
        self, prepared: PreparedInput, *, categories: Sequence[str], filename: str
    ) -> ExtractionResult: ...


def build_extraction_messages(
    prepared: PreparedInput, *, categories: Sequence[str], filename: str
) -> list[BaseMessage]:
    content: list[str | dict[str, Any]] = [
        {"type": "text", "text": EXTRACTION_USER_PROMPT.format(filename=filename)}
    ]
    if prepared.text:
        content.append(
            {"type": "text", "text": EXTRACTION_TEXT_WRAPPER.format(text=prepared.text)}
        )
    for image in prepared.images:
        encoded = base64.b64encode(image).decode("ascii")
        content.append(
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"}}
        )
    if len(content) == 1:
        raise ExtractionError("Nothing to extract: no text and no images")
    return [
        SystemMessage(content=render_extraction_system_prompt(categories)),
        HumanMessage(content=content),
    ]


class OpenAIVisionExtractor:
    def __init__(self, llm: Any) -> None:
        # Raw message is kept for token metrics; function_calling avoids strict-schema limits.
        self._runnable = llm.with_structured_output(
            UnifiedVaultExtraction, method="function_calling", include_raw=True
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIVisionExtractor:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=EXTRACTION_MODEL,
            temperature=0,
            api_key=settings.openai_api_key,
            max_retries=2,
            timeout=90,
        )
        return cls(llm)

    async def extract(
        self, prepared: PreparedInput, *, categories: Sequence[str], filename: str
    ) -> ExtractionResult:
        messages = build_extraction_messages(prepared, categories=categories, filename=filename)
        result: dict[str, Any] = await self._runnable.ainvoke(messages)
        parsed = result.get("parsed")
        if result.get("parsing_error") is not None or not isinstance(
            parsed, UnifiedVaultExtraction
        ):
            raise ExtractionError("Model returned an invalid extraction")
        usage = getattr(result.get("raw"), "usage_metadata", None) or {}
        return ExtractionResult(
            extraction=parsed,
            expiration_raw=parsed.expiration_raw,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
        )


class FakeExtractor:
    """Deterministic extractor for FAKE_EXTERNALS mode and tests."""

    def __init__(self, responses: Sequence[dict[str, Any] | Exception] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self, prepared: PreparedInput, *, categories: Sequence[str], filename: str
    ) -> ExtractionResult:
        self.calls.append(
            {"categories": list(categories), "filename": filename,
             "images": len(prepared.images), "text": prepared.text}
        )
        if self._responses:
            response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        else:
            response = _default_fake_response(filename, prepared)
        if isinstance(response, Exception):
            raise response
        extraction = UnifiedVaultExtraction.model_validate(response)
        return ExtractionResult(extraction=extraction, expiration_raw=extraction.expiration_raw)


def _default_fake_response(filename: str, prepared: PreparedInput) -> dict[str, Any]:
    lowered = filename.lower()
    if "passport" in lowered or "license" in lowered:
        return {"category": "identitycards", "document_name": filename,
                "expiration_date": "2030-01-01", "requires_alert": True,
                "extracted_text": prepared.text or f"Identity document {filename}",
                "financial_value": None, "confidence_score": 0.95}
    return {"category": "miscellaneous", "document_name": filename, "expiration_date": None,
            "requires_alert": False, "extracted_text": prepared.text or f"Document {filename}",
            "financial_value": None, "confidence_score": 0.9}


def get_extractor(settings: Settings) -> MetadataExtractor:
    if settings.fake_externals:
        return FakeExtractor()
    return OpenAIVisionExtractor.from_settings(settings)
