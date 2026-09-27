"""Pluggable embedding providers.

Abstractions:

- ``HashEmbeddingProvider`` — deterministic, dependency-free fallback (used by
  tests and offline mode). Creates a fixed dimension vector from token hashes.
- ``OllamaEmbeddingProvider`` — uses a local Ollama embeddings endpoint
  (``/api/embeddings``). Default remote-free path for local development.
- ``TransformersEmbeddingProvider`` — sentence-transformers (needs the optional
  ``sentence-transformers`` package).

The rest of the application only talks to the ``EmbeddingProvider`` protocol, so
the model can be swapped through configuration.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from backend.app.config import settings

DEFAULT_DIMENSION = 384


class EmbeddingProvider(ABC):
    dimension: int = DEFAULT_DIMENSION

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a list of vectors, one per input text."""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed bag-of-ngrams embedding (no external deps).

    Good enough for offline/testing; meant to be replaced by a semantic provider
    for production quality retrieval.
    """

    def __init__(self, dimension: int = DEFAULT_DIMENSION):
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            tokens = _tokenize(text)
            for n in (1, 2):
                for gram in zip(tokens, tokens[1:]) if n == 2 else [(t,) for t in tokens]:
                    key = " ".join(gram)
                    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
                    idx = int.from_bytes(digest[:4], "little") % self.dimension
                    sign = 1.0 if digest[4] % 2 == 0 else -1.0
                    vector[idx] += sign
            norm = sum(v * v for v in vector) ** 0.5 or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


class OllamaEmbeddingProvider(EmbeddingProvider):
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 dimension: int = DEFAULT_DIMENSION, timeout: float = 60.0):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.dimension = dimension
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=self.timeout) as client:
            vectors = []
            for text in texts:
                resp = client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text[:8192]},
                )
                resp.raise_for_status()
                vectors.append(resp.json()["embedding"])
            return vectors


class TransformersEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install with: pip install -r requirements-optional.txt"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.dimension = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


def _tokenize(text: str) -> list[str]:
    tokens = []
    current: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                tokens.append("".join(current))
                current = []
    if current:
        tokens.append("".join(current))
    return tokens


_PROVIDER_CACHE: dict[str, EmbeddingProvider] = {}


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    name = name or settings.embedding_provider
    if name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[name]
    if name == "hash":
        provider: EmbeddingProvider = HashEmbeddingProvider(settings.embedding_dim)
    elif name == "ollama":
        provider = OllamaEmbeddingProvider(dimension=settings.embedding_dim)
    elif name == "transformers":
        provider = TransformersEmbeddingProvider()
    else:
        raise ValueError(f"Unknown embedding provider: {name}")
    _PROVIDER_CACHE[name] = provider
    return provider