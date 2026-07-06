"""Text embeddings behind one interface — the vector half of the server brain.

Local-first: the default backend is fastembed (ONNX — no torch, no server; the
model downloads once and runs on CPU). OpenAI embeddings are the opt-in cloud
alternative. All vectors are float32 and L2-normalized so cosine similarity is
a plain dot product. Add a backend by subclassing BaseEmbedder.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"


def _normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class BaseEmbedder(ABC):
    name = "base"
    model = ""

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, d) float32, L2-normalized matrix for the given texts."""


class LocalEmbedder(BaseEmbedder):
    """fastembed (ONNX) — private, free, CPU-friendly. Loads lazily on first use."""
    name = "local"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL):
        self.model = model or DEFAULT_LOCAL_MODEL
        self._engine = None

    def _ensure(self):
        if self._engine is None:
            from fastembed import TextEmbedding  # lazy; pip install fastembed
            self._engine = TextEmbedding(model_name=self.model)
        return self._engine

    def embed(self, texts):
        vecs = list(self._ensure().embed(list(texts)))
        return _normalize(np.vstack(vecs))


class OpenAIEmbedder(BaseEmbedder):
    name = "openai"

    def __init__(self, api_key, model: str = DEFAULT_OPENAI_MODEL):
        self.api_key = api_key
        self.model = model or DEFAULT_OPENAI_MODEL
        self._client = None

    def _client_(self):
        if self._client is None:
            from openai import OpenAI  # lazy
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def embed(self, texts):
        r = self._client_().embeddings.create(model=self.model, input=list(texts))
        return _normalize(np.array([d.embedding for d in r.data], dtype=np.float32))


def get_embedder(config) -> BaseEmbedder:
    backend = (getattr(config, "embeddings_backend", "local") or "local").lower()
    model = getattr(config, "embed_model", "") or ""
    if backend == "openai" and getattr(config, "openai_api_key", None):
        return OpenAIEmbedder(config.openai_api_key, model or DEFAULT_OPENAI_MODEL)
    return LocalEmbedder(model or DEFAULT_LOCAL_MODEL)
