"""Hybrid retrieval = vector search + BM25 + reciprocal rank fusion + rerank."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.evidence import DocumentChunk
from backend.app.rag.bm25 import Bm25Index
from backend.app.rag.vector_store import VectorStore
from backend.app.repositories import store


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    case_id: str
    doc_type: str
    page: int
    section: Optional[str]
    supplier_id: Optional[str]
    content: str
    score: float
    rank: int


def _rrf_score(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank + 1)


# Document-type cues inferred from a natural-language query. Conservative: only
# triggers on unambiguous intent so retrieval never excludes a relevant document.
_DOC_TYPE_CUES = [
    (("quoted", "quotation", "who quotes", "quotes for"), {"quote"}),
    (("delivery commitment",), {"quote", "rfq"}),
]

_ALL_DOC_TYPES = {"rfq", "quote", "spec", "policy", "certificate", "history", "other"}


def infer_doc_types(query: str, *, doc_type: str | None = None) -> list[str]:
    """Return doc types to filter on from the query, or [] for no preference.

    An explicit ``doc_type`` always wins. Otherwise conservative keyword cues
    are applied; unrelated queries return [] (target the whole case).
    """
    if doc_type:
        return [doc_type]
    q = query.lower()
    for cues, types in _DOC_TYPE_CUES:
        if any(c in q for c in cues):
            return sorted(types)
    return []


def rank_fusion(vec_ranked: list[tuple[dict[str, Any], float]],
                bm25_ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reciprocal rank fusion of vector hits and rank-ordered BM25 chunks.

    Both legs identify chunks by the same ``chunk_id`` (the DocumentChunk row
    id), so chunks found by both legs accumulate RRF score.
    """
    fused: dict[str, float] = {}

    def add_rank(chunk_id: str, rank: int) -> None:
        fused[chunk_id] = fused.get(chunk_id, 0.0) + _rrf_score(rank)

    for rank, (payload, _score) in enumerate(vec_ranked):
        add_rank(str(payload.get("chunk_id", rank)), rank)
    for rank, chunk in enumerate(bm25_ranked):
        add_rank(str(chunk["chunk_id"]), rank)

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    return [{"chunk_id": cid, "fusion_score": s} for cid, s in ranked]


class HybridRetriever:
    """Hybrid retrieval across the vector store, BM25 in-memory, and chunk rows.

    The chunk table (document_chunks) is the source of truth for metadata; the
    vector store only holds embeddings + ids.
    """

    def __init__(self, vector_store: VectorStore, db: Session | None = None):
        self.vector_store = vector_store
        self.db = db
        self._bm25_cache: dict[tuple[Optional[str], Optional[str], Optional[tuple], Optional[str]], Bm25Index] = {}
        self._bm25_rows: dict[tuple[Optional[str], Optional[str], Optional[tuple], Optional[str]], list[DocumentChunk]] = {}
        self.last_stats: dict[str, Any] = {}

    # ------------------------------------------------------------- bm25 side
    def _get_bm25(self, case_id: str | None, doc_type: str | None,
                  doc_types: list[str] | None = None,
                  supplier_id: str | None = None) -> tuple[Bm25Index, list[DocumentChunk]]:
        key = (case_id, doc_type, tuple(sorted(doc_types)) if doc_types else None, supplier_id)
        if key in self._bm25_cache:
            return self._bm25_cache[key], self._bm25_rows[key]
        index = Bm25Index()
        if self.db is None:
            raise RuntimeError("HybridRetriever needs a db session for BM25 fallback.")
        stmt = select(DocumentChunk)
        if case_id:
            stmt = stmt.where(DocumentChunk.case_id == case_id)
        if doc_type:
            stmt = stmt.where(DocumentChunk.doc_type == doc_type)
        if doc_types:
            stmt = stmt.where(DocumentChunk.doc_type.in_(doc_types))
        if supplier_id:
            stmt = stmt.where(DocumentChunk.supplier_id == supplier_id)
        stmt = stmt.order_by(DocumentChunk.created_at)
        rows = list(self.db.execute(stmt).scalars())
        for chunk in rows:
            index.add_document(
                {
                    "chunk_id": chunk.id,
                    "content": chunk.content,
                    "document_id": chunk.document_id,
                    "case_id": chunk.case_id,
                    "doc_type": chunk.doc_type,
                    "page": chunk.page,
                    "section": chunk.section,
                    "supplier_id": chunk.supplier_id,
                }
            )
        self._bm25_cache[key] = index
        self._bm25_rows[key] = rows
        return index, rows

    # ------------------------------------------------------------- public API
    def retrieve(
        self,
        query: str,
        *,
        case_id: str | None = None,
        doc_type: str | None = None,
        supplier_id: str | None = None,
        top_k: int = 8,
        verbose: bool = False,
    ) -> list[RetrievedChunk] | tuple[list[RetrievedChunk], dict[str, Any]]:
        details: dict[str, Any] = {}
        inferred = infer_doc_types(query, doc_type=doc_type)
        details["doc_types"] = inferred
        vec_ranked = self.vector_store.search(
            query, top_k=top_k * 2, case_id=case_id,
            doc_type=doc_type, doc_types=inferred or None,
            supplier_id=supplier_id,
        )
        details["vector_hits"] = len(vec_ranked)

        bm25_hits: list[int] = []
        bm25_chunks: list[dict[str, Any]] = []
        try:
            bm25, bm25_rows = self._get_bm25(case_id, doc_type, doc_types=inferred or None,
                                             supplier_id=supplier_id)
            top_n = min(top_k * 2, max(len(bm25._doc_len), 1))
            bm25_hits = bm25.search(query, top_k=top_n)
            for idx in bm25_hits:
                if idx < len(bm25_rows):
                    row = bm25_rows[idx]
                    bm25_chunks.append(
                        {
                            "chunk_id": row.id,
                            "content": row.content,
                            "document_id": row.document_id,
                            "case_id": row.case_id,
                            "doc_type": row.doc_type,
                            "page": row.page,
                            "section": row.section,
                            "supplier_id": row.supplier_id,
                        }
                    )
        except Exception:
            bm25_hits = []
        details["bm25_hits"] = len(bm25_hits)

        # rerank vector hits via RRF over both legs
        fused = rank_fusion(vec_ranked, bm25_chunks)
        ranked_ids = [item["chunk_id"] for item in fused]
        fusion_scores = {item["chunk_id"]: float(item.get("fusion_score") or 0.0) for item in fused}
        detail_map: dict[str, Any] = {}
        for item in vec_ranked:
            cid = str(item[0].get("chunk_id"))
            detail_map[cid] = item[0]
        for chunk in bm25_chunks:
            detail_map[chunk["chunk_id"]] = chunk

        results: list[RetrievedChunk] = []
        seen: set[str] = set()
        for cid in ranked_ids:
            if cid in seen:
                continue
            seen.add(cid)
            chunk: dict[str, Any] | None = detail_map.get(cid)
            if chunk is None:
                continue
            results.append(
                RetrievedChunk(
                    chunk_id=str(chunk.get("chunk_id") or cid),
                    document_id=str(chunk.get("document_id") or ""),
                    case_id=str(chunk.get("case_id") or case_id or ""),
                    doc_type=str(chunk.get("doc_type") or doc_type or ""),
                    page=int(chunk.get("page") or 1),
                    section=str(chunk.get("section") or "") or None,
                    supplier_id=str(chunk.get("supplier_id") or "") or None,
                    content=str(chunk.get("content") or ""),
                    score=fusion_scores.get(cid, 0.0),
                    rank=0,
                )
            )
            if len(results) >= top_k:
                break
        for rank, r in enumerate(results):
            r.rank = rank + 1
        details["hybrid_hits"] = len(results)
        self.last_stats = details
        if verbose:
            return results, details
        return results

    def log(self, case_id: str | None, request_id: str, query: str, filters: dict,
            latency_ms: int, results: list[RetrievedChunk],
            metadata_json: dict | None = None) -> None:
        if self.db is None or case_id is None:
            return
        store.add_retrieval_log(
            self.db,
            case_id=case_id,
            request_id=request_id or uuid.uuid4().hex,
            query=query,
            filters=filters,
            hits=[{"chunk_id": r.chunk_id, "document_id": r.document_id, "page": r.page} for r in results[:10]],
            scores=[r.score for r in results[:10]],
            latency_ms=latency_ms,
            metadata_json=metadata_json or {},
        )