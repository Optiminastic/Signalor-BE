import secrets

from django.db import models
from pgvector.django import VectorField

from .utils import normalize_url

# Gemini text-embedding-004 output width. Fixed at the column level, so changing
# the embedding model means a migration + re-embed (tracked via embedding_model).
EMBEDDING_DIMENSIONS = 768


def generate_org_slug() -> str:
    """High-entropy, URL-safe brand slug (~22 chars / 128 bits).

    Random rather than name-derived so brand dashboard URLs can't be guessed or
    enumerated. Data is still email-scoped server-side, so a leaked slug alone
    exposes nothing.
    """
    return secrets.token_urlsafe(16)


class Organization(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True, default="")
    # Unguessable public identifier used in the dashboard URL (/dashboard/<slug>).
    slug = models.CharField(max_length=32, unique=True, blank=True, default="", db_index=True)
    # Canonicalized host derived from ``url`` (no scheme, no www, no path).
    # Used to dedupe org creation per (owner_email, normalized_url) without
    # being fooled by trivial URL variants. Maintained by .save() — never
    # set this field directly.
    normalized_url = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # Site platform (e.g. "nextjs", "wordpress", "shopify", "webflow"). Optional —
    # populated by platform detection / integrations; empty until known. The
    # column is NOT NULL in the DB, so the "" default is what keeps onboarding
    # inserts valid.
    platform = models.CharField(max_length=20, blank=True, default="")
    owner_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_email"]),
            models.Index(fields=["owner_email", "normalized_url"]),
        ]

    def save(self, *args, **kwargs):
        # Keep normalized_url in sync with url on every write. Doing it here
        # (rather than the serializer) means admin edits and shell tweaks
        # also stay consistent.
        self.normalized_url = normalize_url(self.url or "")
        if not self.slug:
            candidate = generate_org_slug()
            while Organization.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = generate_org_slug()
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.owner_email})"


class BrandProfile(models.Model):
    """Persistent, org-scoped brand knowledge — the AI's durable memory of a brand.

    Bootstrapped (PENDING) from existing analyzer signals, human-approved, and only
    then fed into LLM prompts as a system-prompt "brand card". Interpretive sections
    (identity/positioning/audience/voice) are LLM-synthesized; factual sections
    (canonical_facts/competitors/sources) are mapped deterministically so reviewers
    can trust them and prompts never inherit a hallucinated fact.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="brand_profile")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    # 0..1 heuristic — how much hard signal backed the bootstrap.
    confidence = models.FloatField(default=0.0)
    # Stamped when a human approves/rejects (nullable-event-timestamp style).
    last_verified_at = models.DateTimeField(null=True, blank=True)
    # String ref avoids a top-level analyzer import (keeps analyzer -> organizations
    # the only hard dependency direction).
    source_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brand_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Content sections — one JSONField each. See apps/organizations/services.
    identity = models.JSONField(default=dict, blank=True)  # LLM-synthesized
    positioning = models.JSONField(default=dict, blank=True)  # LLM-synthesized
    audience = models.JSONField(default=dict, blank=True)  # LLM-synthesized
    voice = models.JSONField(default=dict, blank=True)  # LLM-synthesized
    canonical_facts = models.JSONField(default=dict, blank=True)  # deterministic anchors
    competitors = models.JSONField(default=list, blank=True)  # deterministic
    sources = models.JSONField(default=dict, blank=True)  # provenance

    class Meta:
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"BrandProfile<{self.organization_id}:{self.status}>"


class BrandCorpusChunk(models.Model):
    """A single embedded slice of a brand's crawled content — the org's searchable
    knowledge base (Epic 3, Knowledge Ingestion).

    Every analysis run extracts, cleans, chunks and embeds the pages it already
    crawled into these rows (org-scoped; anonymous runs are skipped). Storage only —
    retrieval/similarity search is Epic 4. Chunks are content-addressed by
    ``content_hash`` so unchanged pages are never re-embedded; when a page's content
    changes the old rows are soft-superseded (``is_current=False``) and a new
    ``version`` is inserted, so history is retained (never hard-deleted).
    """

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="corpus_chunks")
    # String ref avoids a top-level analyzer import (keeps analyzer -> organizations
    # the only hard dependency direction). Provenance only — SET_NULL on run delete.
    source_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corpus_chunks",
    )
    source_url = models.URLField(max_length=2048, db_index=True)
    # Heading breadcrumb for the chunk, e.g. ["Pricing", "Enterprise plan"].
    heading_path = models.JSONField(default=list, blank=True)
    text = models.TextField()
    # Free-form provenance/context: {page_title, position, lang, char_count, ...}.
    metadata = models.JSONField(default=dict, blank=True)
    # sha256 of the normalized chunk text — the dedup / skip-unchanged key.
    content_hash = models.CharField(max_length=64, db_index=True)
    # Null until embedded; a failed embedding leaves it null to retry next run.
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True)
    # Which model produced ``embedding`` — lets a future re-embed target stale rows.
    embedding_model = models.CharField(max_length=64, blank=True, default="")
    version = models.PositiveIntegerField(default=1)
    # Soft-supersede flag: only is_current rows feed retrieval (Epic 4).
    is_current = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "source_url", "content_hash"],
                name="uniq_corpus_chunk",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "is_current"]),
            models.Index(fields=["organization", "source_url", "is_current"]),
        ]

    def __str__(self):
        return f"BrandCorpusChunk<{self.organization_id}:{self.source_url}#{self.pk}>"
