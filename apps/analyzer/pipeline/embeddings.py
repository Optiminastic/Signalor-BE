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

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("apps")

# ``text-embedding-004`` was retired: the Gemini API now answers it with
# "not found for API version v1beta". Only the gemini-embedding-* family remains,
# and those default to 3072 dims, so the dimension MUST be requested explicitly -
# BrandCorpusChunk.embedding is a fixed-width 768 pgvector column and a 3072-wide
# vector cannot be stored in it.
#
# Requesting 768 (a supported Matryoshka truncation) keeps the existing column and
# every already-stored vector valid, so this is a drop-in swap with no migration
# and no re-index. Override both together via env if you ever widen the column.
DEFAULT_EMBED_MODEL = os.getenv("CORPUS_EMBED_MODEL", "models/gemini-embedding-001")

def _embed_dimensions() -> int:
    """Vector width, authoritative from the column the vectors are stored in.

    ``BrandCorpusChunk.embedding`` is a fixed-width pgvector column, so the model
    output width is not a free choice: a mismatch is only discovered at INSERT,
    after a whole run's worth of embeddings has been paid for and computed.

    ``CORPUS_EMBED_DIMENSIONS`` stays available for the widen-the-column case but
    is validated here rather than trusted, so a stale env var fails loudly at
    import instead of silently producing vectors that cannot be stored.
    """
    from apps.organizations.models import EMBEDDING_DIMENSIONS

    raw = os.getenv("CORPUS_EMBED_DIMENSIONS", "").strip()
    if not raw:
        return EMBEDDING_DIMENSIONS
    try:
        override = int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"CORPUS_EMBED_DIMENSIONS={raw!r} is not an integer."
        ) from exc
    if override != EMBEDDING_DIMENSIONS:
        raise ImproperlyConfigured(
            f"CORPUS_EMBED_DIMENSIONS={override} does not match the "
            f"BrandCorpusChunk.embedding column width ({EMBEDDING_DIMENSIONS}). "
            "Migrate the column first, or unset the override."
        )
    return override


EMBED_DIMENSIONS = _embed_dimensions()

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


def _embed_content(genai: object, **kwargs):
    """``genai.embed_content`` with an explicit output width.

    Older SDK builds do not accept ``output_dimensionality``; on TypeError we
    retry without it and let ``_fit`` trim the result, so an SDK downgrade
    degrades to a slightly wasteful call rather than a hard failure.
    """
    try:
        return genai.embed_content(output_dimensionality=EMBED_DIMENSIONS, **kwargs)
    except TypeError:
        return genai.embed_content(**kwargs)


def _fit(vector) -> list[float] | None:
    """Coerce a returned vector to exactly ``EMBED_DIMENSIONS`` floats.

    Truncation is safe here because retrieval ranks by cosine similarity, which
    is scale-invariant, and these embeddings are Matryoshka-trained so a prefix
    is a valid lower-dimensional embedding. A vector that comes back *shorter*
    than the column is unusable, so it is dropped rather than zero-padded - a
    padded vector would silently score as a poor match forever.
    """
    if vector is None:
        return None
    values = list(vector)
    if len(values) < EMBED_DIMENSIONS:
        logger.warning(
            "Embedding too short (%d < %d); dropping rather than padding",
            len(values),
            EMBED_DIMENSIONS,
        )
        return None
    return values[:EMBED_DIMENSIONS]


def _embed_one(genai: object, text: str, model: str) -> list[float] | None:
    try:
        resp = _embed_content(genai, model=model, content=text, task_type=_TASK_DOCUMENT)
        return _fit(resp["embedding"])
    except Exception as exc:  # noqa: BLE001 - fail-soft per item
        logger.warning("Embedding failed for one chunk: %s", exc)
        return None


def _embed_batch(genai: object, texts: list[str], model: str) -> list[list[float] | None]:
    """Embed a batch in one call; fall back to per-item on batch failure."""
    try:
        resp = _embed_content(genai, model=model, content=texts, task_type=_TASK_DOCUMENT)
        vectors = resp["embedding"]
        # The batch API returns a list aligned with ``texts``.
        if isinstance(vectors, list) and len(vectors) == len(texts):
            return [_fit(v) for v in vectors]
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
        # Must go through the same width-controlling wrapper as the document path.
        # A 3072-wide query vector cannot be compared against 768-wide stored
        # vectors, so every search would fail or silently score nothing.
        resp = _embed_content(genai, model=model, content=text, task_type=_TASK_QUERY)
        return _fit(resp["embedding"])
    except Exception as exc:  # noqa: BLE001 - fail-soft
        logger.warning("Query embedding failed: %s", exc)
        return None
