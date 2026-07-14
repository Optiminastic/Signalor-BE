"""Gemini text-embedding client for the knowledge base (Epic 3).

Thin, swappable wrapper over the direct Gemini embeddings API (``google-generativeai``),
mirroring the key/config pattern in ``llm.py``. Kept separate from ``llm.py`` so the
embedding backend can be swapped without touching chat-completion routing.

Fail-soft by contract: ``embed_documents`` always returns a list aligned 1:1 with its
input, with ``None`` in any slot that could not be embedded (missing key, API error).
Callers store those chunks un-embedded and retry them on the next run.
"""

import logging
import os

logger = logging.getLogger("apps")

# text-embedding-004 → 768 dims. Must match models.EMBEDDING_DIMENSIONS / the
# BrandCorpusChunk.embedding column width. Override via env only if you also
# migrate the column and re-embed.
DEFAULT_EMBED_MODEL = os.getenv("CORPUS_EMBED_MODEL", "models/text-embedding-004")

# Gemini caps batch embedding requests; stay well under it.
_MAX_BATCH = 100
# task_type tunes the vector for its role; documents and queries use different
# types so a query vector lands near the docs that answer it (Epic 4 retrieval).
_TASK_DOCUMENT = "retrieval_document"
_TASK_QUERY = "retrieval_query"


def _google_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY", "").strip() or None


def _configure() -> object | None:
    """Return a configured ``genai`` module, or ``None`` if unavailable."""
    key = _google_key()
    if not key:
        logger.warning("Embeddings skipped: GOOGLE_API_KEY not set")
        return None
    try:
        import google.generativeai as genai
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("Embeddings skipped: google-generativeai not installed")
        return None
    genai.configure(api_key=key)
    return genai


def _embed_one(genai: object, text: str, model: str) -> list[float] | None:
    try:
        resp = genai.embed_content(model=model, content=text, task_type=_TASK_DOCUMENT)
        return list(resp["embedding"])
    except Exception as exc:  # noqa: BLE001 - fail-soft per item
        logger.warning("Embedding failed for one chunk: %s", exc)
        return None


def _embed_batch(genai: object, texts: list[str], model: str) -> list[list[float] | None]:
    """Embed a batch in one call; fall back to per-item on batch failure."""
    try:
        resp = genai.embed_content(model=model, content=texts, task_type=_TASK_DOCUMENT)
        vectors = resp["embedding"]
        # The batch API returns a list aligned with ``texts``.
        if isinstance(vectors, list) and len(vectors) == len(texts):
            return [list(v) for v in vectors]
        logger.warning("Unexpected batch embedding shape; retrying per item")
    except Exception as exc:  # noqa: BLE001 - fall back to per-item
        logger.warning("Batch embedding failed (%s); retrying per item", exc)
    return [_embed_one(genai, t, model) for t in texts]


def embed_documents(texts: list[str], *, model: str | None = None) -> list[list[float] | None]:
    """Embed ``texts`` for storage in the knowledge base.

    Returns a list the same length as ``texts``; each element is a 768-float vector
    or ``None`` if that item could not be embedded. Never raises.
    """
    if not texts:
        return []
    genai = _configure()
    if genai is None:
        return [None] * len(texts)

    model = model or DEFAULT_EMBED_MODEL
    out: list[list[float] | None] = []
    for start in range(0, len(texts), _MAX_BATCH):
        out.extend(_embed_batch(genai, texts[start : start + _MAX_BATCH], model))
    return out


def embed_query(text: str, *, model: str | None = None) -> list[float] | None:
    """Embed a search query for retrieval (Epic 4).

    Uses ``retrieval_query`` task type so the vector lands near the documents that
    answer it (documents are embedded with ``retrieval_document``). Returns the
    768-float vector, or ``None`` if it could not be embedded. Never raises.
    """
    text = (text or "").strip()
    if not text:
        return None
    genai = _configure()
    if genai is None:
        return None
    model = model or DEFAULT_EMBED_MODEL
    try:
        resp = genai.embed_content(model=model, content=text, task_type=_TASK_QUERY)
        return list(resp["embedding"])
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("Query embedding failed: %s", exc)
        return None
