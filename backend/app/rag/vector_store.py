"""Qdrant-backed vector store with server and in-memory modes."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from backend.app.config import settings
from backend.app.rag.embeddings import EmbeddingProvider, get_embedding_provider


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, provider: EmbeddingProvider):
        self.client = client
        self.collection = collection
        self.provider = provider
        self._ensure_collection()

    @classmethod
    def from_settings(cls) -> "VectorStore":
        mode = settings.vector_store_mode
        if settings.qdrant_url:
            client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=30,
            )
        elif mode == "server":
            client = QdrantClient(
                host=settings.vector_host,
                port=settings.vector_port,
                api_key=settings.qdrant_api_key or None,
                timeout=30,
            )
        else:
            client = QdrantClient(":memory:")
        return cls(
            client=client,
            collection=settings.active_collection,
            provider=get_embedding_provider(),
        )

    def validate_connection(self) -> None:
        """Raise if the configured Qdrant endpoint is unreachable.

        Only meaningful in server modes; in-memory mode never raises.
        """
        if isinstance(self.client, QdrantClient) and (settings.qdrant_url or settings.vector_store_mode == "server"):
            try:
                self.client.get_collections()
            except Exception as exc:  # pragma: no cover - external server
                raise RuntimeError(
                    f"Qdrant is unreachable at {settings.qdrant_url or f'{settings.vector_host}:{settings.vector_port}'}: {exc}"
                ) from exc

    def _ensure_collection(self) -> None:
        try:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(
                    size=self.provider.dimension,
                    distance=qm.Distance.COSINE,
                ),
            )
        except Exception:
            # collection already exists
            pass

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ write
    def index_chunks(self, chunks: list[dict[str, Any]]) -> list[str]:
        """Index chunks. Each chunk has id, content, metadata. Returns vector ids."""
        texts = [c["content"] for c in chunks]
        vectors = self.provider.embed(texts)
        points = []
        ids: list[str] = []
        for chunk, vector in zip(chunks, vectors):
            point_id = chunk.get("id") or uuid.uuid4().hex
            ids.append(point_id)
            points.append(
                qm.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "chunk_id": point_id,
                        "content": chunk["content"][:2000],
                        **chunk.get("metadata", {}),
                    },
                )
            )
        self.client.upsert(collection_name=self.collection, points=points)
        return ids

    def delete_by_case(self, case_id: str) -> None:
        try:
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            self.client.delete(
                collection_name=self.collection,
                points_selector=qm.FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(key="case_id", match=MatchValue(value=case_id))]
                    )
                ),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        case_id: str | None = None,
        doc_type: str | None = None,
        doc_types: list[str] | None = None,
        supplier_id: str | None = None,
    ) -> list[tuple[dict[str, Any], float]]:
        must: list[Any] = []
        if case_id:
            must.append(qm.FieldCondition(key="case_id", match=qm.MatchValue(value=case_id)))
        if doc_type:
            must.append(qm.FieldCondition(key="doc_type", match=qm.MatchValue(value=doc_type)))
        if doc_types:
            must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=list(doc_types))))
        if supplier_id:
            must.append(qm.FieldCondition(key="supplier_id", match=qm.MatchValue(value=supplier_id)))
        query_vector = self.provider.embed_one(query)
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k,
            query_filter=qm.Filter(must=must) if must else None,
        )
        return [(hit.payload or {}, float(hit.score)) for hit in response.points]