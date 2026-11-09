"""Conversational RAG: resolve member/category -> filtered Pinecone retrieval -> streamed answer.

Events are emitted through LangGraph's custom stream:
  {"event": "filter", ...} -> {"event": "token", ...}* -> {"event": "sources", ...}
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, TypedDict
from uuid import UUID

import asyncpg
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents.prompts import (
    CONTEXT_RESOLVER_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT,
    render_context_block,
)
from app.agents.state import ChatFilter, SourceRef
from app.config import Settings
from app.embeddings import Embedder
from app.repositories import CategoryRepo, DocumentRepo, FamilyMember, MemberRepo
from app.security import Principal
from app.vectorstore import VectorMatch, VectorStore, build_filter

logger = logging.getLogger(__name__)

RESOLVER_MODEL: Final = "gpt-4o-mini"
ANSWER_MODEL: Final = "gpt-4o"
DEFAULT_TOP_K: Final = 8
NOT_FOUND_MESSAGE: Final = (
    "I couldn't find that in your vault. Try naming the family member or document type, "
    "or upload the document first."
)

ChatEvent = dict[str, Any]


class ContextResolution(BaseModel):
    """Which family member and category the question is about (null when not stated)."""

    member_name: str | None = Field(
        default=None, description="Family member the question is about, exactly as listed."
    )
    category: str | None = Field(
        default=None, description="Most relevant category slug from the list, or null."
    )


class ContextResolver(Protocol):
    async def resolve(
        self, question: str, *, member_names: Sequence[str], categories: Sequence[str]
    ) -> ContextResolution: ...


class AnswerGenerator(Protocol):
    def stream(self, question: str, contexts: Sequence[VectorMatch]) -> AsyncIterator[str]: ...


# ---------------------------------------------------------------------------
# OpenAI implementations
# ---------------------------------------------------------------------------


class OpenAIContextResolver:
    def __init__(self, llm: Any) -> None:
        self._runnable = llm.with_structured_output(ContextResolution, method="function_calling")

    async def resolve(
        self, question: str, *, member_names: Sequence[str], categories: Sequence[str]
    ) -> ContextResolution:
        system = CONTEXT_RESOLVER_SYSTEM_PROMPT.format(
            members="\n".join(f"- {n}" for n in member_names) or "- (none)",
            categories="\n".join(f"- {c}" for c in categories),
        )
        result = await self._runnable.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=question)]
        )
        if not isinstance(result, ContextResolution):
            return ContextResolution()
        return result


class OpenAIAnswerGenerator:
    def __init__(self, llm: Any) -> None:
        self._llm = llm

    async def stream(self, question: str, contexts: Sequence[VectorMatch]) -> AsyncIterator[str]:
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(content=f"{render_context_block(contexts)}\n\nQuestion: {question}"),
        ]
        async for chunk in self._llm.astream(messages):
            content = chunk.content
            if isinstance(content, str) and content:
                yield content


# ---------------------------------------------------------------------------
# Offline implementations (tests and FAKE_EXTERNALS mode)
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "identitycards": ("passport", "license", "licence", "id card", "identity", "national id"),
    "certificate": ("certificate", "birth", "marriage", "degree", "diploma"),
    "income": ("paystub", "payslip", "salary", "tax", "w-2", "w2", "income", "wage"),
    "expense": ("receipt", "bill", "utility", "invoice", "expense", "spent"),
    "medical": ("prescription", "insurance", "medical", "doctor", "scan", "health", "medicine"),
    "photo": ("photo", "picture", "memory", "memories", "snapshot", "vacation"),
}


class KeywordContextResolver:
    async def resolve(
        self, question: str, *, member_names: Sequence[str], categories: Sequence[str]
    ) -> ContextResolution:
        text = question.lower()
        member = next(
            (n for n in member_names if re.search(rf"\b{re.escape(n.lower())}\b", text)), None
        )
        category = next(
            (c for c in categories
             if c not in _CATEGORY_KEYWORDS and re.search(rf"\b{re.escape(c)}\b", text)),
            None,
        )
        if category is None:
            category = next(
                (c for c, words in _CATEGORY_KEYWORDS.items()
                 if c in categories and any(w in text for w in words)),
                None,
            )
        return ContextResolution(member_name=member, category=category)


class TemplateAnswerGenerator:
    async def stream(self, question: str, contexts: Sequence[VectorMatch]) -> AsyncIterator[str]:
        names = []
        for match in contexts:
            name = str(match.metadata.get("document_name", "document"))
            expiry = match.metadata.get("expiration_date")
            names.append(f"{name} (expires {expiry})" if expiry else name)
        answer = "From your vault: " + "; ".join(dict.fromkeys(names)) + "."
        for word in answer.split(" "):
            yield word + " "


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class RAGGraphState(TypedDict, total=False):
    question: str
    principal: Principal
    chat_filter: ChatFilter
    pinecone_filter: dict[str, Any]
    contexts: list[VectorMatch]
    sources: list[SourceRef]
    answer: str


@dataclass
class RAGDeps:
    pool: asyncpg.Pool
    embedder: Embedder
    vectors: VectorStore
    resolver: ContextResolver
    generator: AnswerGenerator
    top_k: int = DEFAULT_TOP_K


def match_member(name: str | None, roster: Sequence[FamilyMember]) -> FamilyMember | None:
    """Only names from the caller's own family roster can ever become a filter."""
    if not name:
        return None
    wanted = name.strip().lower()
    for member in roster:
        if member.display_name.lower() == wanted:
            return member
    first_name_hits = [m for m in roster if m.display_name.lower().split(" ")[0] == wanted]
    return first_name_hits[0] if len(first_name_hits) == 1 else None


def filter_label(chat_filter: ChatFilter) -> str:
    return (
        f"Context Filtered: {chat_filter.member or 'All members'} | "
        f"{chat_filter.category or 'All categories'}"
    )


def build_sources(matches: Sequence[VectorMatch]) -> list[SourceRef]:
    best: dict[str, SourceRef] = {}
    for m in matches:
        doc_id = str(m.metadata.get("document_id", m.id.split("#")[0]))
        if doc_id not in best or m.score > best[doc_id].score:
            best[doc_id] = SourceRef(
                document_id=doc_id,
                document_name=str(m.metadata.get("document_name", "")),
                category=str(m.metadata.get("category", "")),
                score=round(m.score, 4),
            )
    return sorted(best.values(), key=lambda s: s.score, reverse=True)


class RAGEngine:
    def __init__(self, deps: RAGDeps) -> None:
        self._deps = deps
        self._graph = self._build()

    def _build(self) -> Any:
        deps = self._deps

        async def resolve_context(state: RAGGraphState) -> dict[str, Any]:
            principal = state["principal"]
            roster = await MemberRepo(deps.pool).list_all(principal.family_id)
            categories = await CategoryRepo(deps.pool).list_slugs(principal.family_id)
            try:
                resolution = await deps.resolver.resolve(
                    state["question"],
                    member_names=[m.display_name for m in roster],
                    categories=categories,
                )
            except Exception:  # resolver failure degrades to an unfiltered family search
                logger.warning("context resolution failed", exc_info=True)
                resolution = ContextResolution()

            member = match_member(resolution.member_name, roster)
            if not principal.is_admin:
                member = next((m for m in roster if m.id == principal.member_id), None)
            category = resolution.category if resolution.category in categories else None

            chat_filter = ChatFilter(
                member_id=member.id if member else None,
                member=member.display_name if member else None,
                category=category,
            )
            pinecone_filter = build_filter(
                principal, member_id=chat_filter.member_id, category=category
            )
            get_stream_writer()(
                {"event": "filter",
                 "data": {**chat_filter.model_dump(mode="json"),
                          "label": filter_label(chat_filter)}}
            )
            return {"chat_filter": chat_filter, "pinecone_filter": pinecone_filter}

        async def retrieve(state: RAGGraphState) -> dict[str, Any]:
            vector = (
                None if deps.vectors.integrated_embeddings
                else (await deps.embedder.embed([state["question"]]))[0]
            )
            matches = await deps.vectors.query(
                vector, filter=state["pinecone_filter"], top_k=deps.top_k,
                query_text=state["question"],
            )
            ids: dict[str, UUID] = {}
            for match in matches:
                document_id = str(match.metadata.get("document_id", ""))
                try:
                    ids[document_id] = UUID(document_id)
                except ValueError:
                    logger.warning("ignoring search match with invalid document ID")
            documents = await DocumentRepo(deps.pool).searchable_documents(
                state["principal"].family_id, list(ids.values()),
                family_member_id=state["chat_filter"].member_id,
                category=state["chat_filter"].category,
            )
            allowed = {str(document.id): document.index_version for document in documents}
            # Pinecone deletes are eventually consistent. Never answer from a held,
            # deleted or out-of-scope document even if its old vectors are returned.
            matches = [
                match for match in matches
                if (document_id := str(match.metadata.get("document_id", ""))) in allowed
                and match.metadata.get("index_version") == allowed[document_id]
            ]
            return {"contexts": matches, "sources": build_sources(matches)}

        async def generate(state: RAGGraphState) -> dict[str, Any]:
            writer = get_stream_writer()
            contexts = state.get("contexts", [])
            parts: list[str] = []
            if not contexts:
                parts.append(NOT_FOUND_MESSAGE)
                writer({"event": "token", "data": {"text": NOT_FOUND_MESSAGE}})
            else:
                async for token in deps.generator.stream(state["question"], contexts):
                    parts.append(token)
                    writer({"event": "token", "data": {"text": token}})
            writer(
                {"event": "sources",
                 "data": [s.model_dump(mode="json") for s in state.get("sources", [])]}
            )
            return {"answer": "".join(parts)}

        graph: StateGraph[Any, Any, Any, Any] = StateGraph(RAGGraphState)
        graph.add_node("resolve_context", resolve_context)
        graph.add_node("retrieve", retrieve)
        graph.add_node("generate", generate)
        graph.add_edge(START, "resolve_context")
        graph.add_edge("resolve_context", "retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", END)
        return graph.compile()

    async def stream(self, question: str, principal: Principal) -> AsyncIterator[ChatEvent]:
        async for event in self._graph.astream(
            {"question": question, "principal": principal}, stream_mode="custom"
        ):
            yield event


def get_rag_engine_deps(
    settings: Settings, *, pool: asyncpg.Pool, embedder: Embedder, vectors: VectorStore
) -> RAGDeps:
    if settings.fake_externals:
        return RAGDeps(pool=pool, embedder=embedder, vectors=vectors,
                       resolver=KeywordContextResolver(), generator=TemplateAnswerGenerator())
    from langchain_openai import ChatOpenAI

    key = settings.openai_api_key
    return RAGDeps(
        pool=pool,
        embedder=embedder,
        vectors=vectors,
        resolver=OpenAIContextResolver(
            ChatOpenAI(model=RESOLVER_MODEL, temperature=0, api_key=key, timeout=20)
        ),
        generator=OpenAIAnswerGenerator(
            ChatOpenAI(model=ANSWER_MODEL, temperature=0.2, api_key=key, streaming=True,
                       timeout=60)
        ),
    )
