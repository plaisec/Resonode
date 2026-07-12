"""LLM (summarize / translate / moderate / answer) and Memory (recall) services.

Pluggable, bring-your-own-key. Phase 1 ships a NoLLM stub plus OpenAI/Anthropic
implementations, and memory as keyword recall over stored utterances (works
today). Vector RAG (pgvector/Qdrant + embeddings) is the documented upgrade.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


# --------------------------------------------------------------------------- LLM
class BaseLLM(ABC):
    name = "base"
    available = False

    @abstractmethod
    def summarize(self, transcript: str) -> str: ...
    @abstractmethod
    def translate(self, text: str, target_lang: str) -> str: ...
    @abstractmethod
    def moderate(self, text: str) -> dict: ...
    @abstractmethod
    def answer(self, question: str, context: str) -> str: ...


class NoLLM(BaseLLM):
    name = "none"
    available = False
    _MSG = ("No LLM configured. Set RESONODE_LLM=openai|anthropic and the matching API "
            "key to enable summaries, translation, moderation, and answered questions.")

    def summarize(self, transcript): return self._MSG
    def translate(self, text, target_lang): return self._MSG
    def moderate(self, text): return {"flagged": False, "reason": self._MSG}
    def answer(self, question, context): return self._MSG


class _ChatLLM(BaseLLM):
    """Shared prompts; subclasses implement _chat(system, user) -> str."""
    available = True

    def _chat(self, system: str, user: str) -> str:
        raise NotImplementedError

    def summarize(self, transcript):
        return self._chat(
            "You are a meeting assistant. Summarize the transcript: a 2-3 sentence overview, "
            "a 'Decisions' list, and an 'Action items' list (owner - task). Be concise.",
            transcript,
        )

    def translate(self, text, target_lang):
        return self._chat(
            f"Translate the user's text into {target_lang}. The text is a single line of "
            "noisy speech-to-text output from live voice chat: expect missing punctuation, "
            "slang, and occasional mis-transcribed words. Translate the intended meaning "
            "naturally; keep names and untranslatable terms as-is. Output only the "
            "translation, no commentary.",
            text,
        )

    def moderate(self, text):
        out = self._chat(
            "You are a content moderator. Reply exactly 'SAFE' or 'FLAG: <short reason>'.", text
        ).strip()
        return {"flagged": out.upper().startswith("FLAG"), "reason": out}

    def answer(self, question, context):
        return self._chat(
            "You answer questions about a Discord community's voice history. Be concise and "
            "cite [timestamp] speaker. Use only the provided lines; say so if they don't cover it.",
            f"Question: {question}\n\nLines:\n{context}",
        )


class OpenAILLM(_ChatLLM):
    name = "openai"

    def __init__(self, api_key, model="gpt-4o-mini"):
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"
        self._client = None

    def _client_(self):
        if self._client is None:
            from openai import OpenAI  # lazy
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _chat(self, system, user):
        r = self._client_().chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
        )
        return (r.choices[0].message.content or "").strip()


class AnthropicLLM(_ChatLLM):
    name = "anthropic"

    def __init__(self, api_key, model="claude-3-5-haiku-latest"):
        self.api_key = api_key
        self.model = model or "claude-3-5-haiku-latest"
        self._client = None

    def _client_(self):
        if self._client is None:
            import anthropic  # lazy
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _chat(self, system, user):
        r = self._client_().messages.create(
            model=self.model, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()


def get_llm(config) -> BaseLLM:
    provider = (getattr(config, "llm_provider", "none") or "none").lower()
    if provider == "openai" and getattr(config, "openai_api_key", None):
        return OpenAILLM(config.openai_api_key, getattr(config, "llm_model", ""))
    if provider == "anthropic" and getattr(config, "anthropic_api_key", None):
        return AnthropicLLM(config.anthropic_api_key, getattr(config, "llm_model", ""))
    return NoLLM()


# ------------------------------------------------------------------------ Memory
class BaseMemory(ABC):
    name = "base"

    @abstractmethod
    def recall(self, guild_id: int, query: str, k: int = 8) -> list[dict]: ...


class KeywordMemory(BaseMemory):
    """Works today: keyword recall over stored utterances (no embeddings)."""
    name = "keyword"

    def __init__(self, storage):
        self.storage = storage

    def recall(self, guild_id, query, k=8):
        terms = set(w for w in query.split() if len(w) > 2) or {query}
        seen: dict = {}
        for term in terms:
            for row in self.storage.search(guild_id, term, limit=k):
                seen[row["id"]] = row
        return list(seen.values())[:k]


class VectorMemory(BaseMemory):
    """Semantic recall: embeddings stored in SQLite, cosine top-k via numpy.

    Indexing is lazy — each recall first embeds any not-yet-indexed utterances
    for the guild, so write paths stay untouched and old history backfills
    itself on first use. Falls back to keyword recall if the embedder fails
    (e.g. fastembed not installed).
    """
    name = "vector"

    def __init__(self, storage, embedder, fallback: BaseMemory | None = None):
        self.storage = storage
        self.embedder = embedder
        self.fallback = fallback or KeywordMemory(storage)

    def _backfill(self, guild_id, batch=256):
        while True:
            rows = self.storage.unembedded_utterances(guild_id, self.embedder.model, limit=batch)
            if not rows:
                return
            mat = self.embedder.embed([r["text"] for r in rows])
            self.storage.add_embeddings(
                guild_id, self.embedder.model, int(mat.shape[1]),
                [(r["id"], mat[i].tobytes()) for i, r in enumerate(rows)],
            )
            if len(rows) < batch:
                return

    def recall(self, guild_id, query, k=8):
        try:
            self._backfill(guild_id)
            stored = self.storage.guild_embeddings(guild_id, self.embedder.model)
            if not stored:
                return []
            ids = [uid for uid, _ in stored]
            mat = np.vstack([np.frombuffer(blob, dtype=np.float32) for _, blob in stored])
            q = self.embedder.embed([query])[0]
            top = np.argsort(mat @ q)[::-1][:k]
            return self.storage.utterances_by_ids([ids[i] for i in top])
        except Exception as exc:
            print(f"[resonode] vector recall failed ({exc}); falling back to keyword search.")
            return self.fallback.recall(guild_id, query, k)


def get_memory(config, storage) -> BaseMemory:
    backend = (getattr(config, "memory_backend", "keyword") or "keyword").lower()
    if backend == "vector":
        from .embeddings import get_embedder  # lazy — keeps the default path light
        return VectorMemory(storage, get_embedder(config))
    return KeywordMemory(storage)
