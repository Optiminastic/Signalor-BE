"""Tracked GEO prompts, the answers engines gave, and their citations."""

from django.db import models

from .run import AnalysisRun


class PromptTrack(models.Model):
    class SearchIntent(models.TextChoices):
        """Why the user is asking (GEO / prompt strategy)."""

        BRAND = "brand", "Brand"
        INFORMATIONAL = "informational", "Information"
        TRANSACTIONAL = "transactional", "Transactional"

    class PromptSurfaceType(models.TextChoices):
        """Shape of the query vs brand & competition (classic AI-search buckets)."""

        ORGANIC = "organic", "Organic"
        BRANDED = "branded", "Brand"
        COMPETITIVE = "competitive", "Competition"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="prompt_tracks")
    prompt_text = models.TextField()
    is_custom = models.BooleanField(default=False)
    intent = models.CharField(
        max_length=20,
        choices=SearchIntent.choices,
        default=SearchIntent.INFORMATIONAL,
    )
    prompt_type = models.CharField(
        max_length=20,
        choices=PromptSurfaceType.choices,
        default=PromptSurfaceType.ORGANIC,
    )
    score = models.FloatField(default=0.0)

    # 5-Factor AI Visibility Ranking Scores (all 0.0–1.0)
    authority_score = models.FloatField(default=0.0)  # Factor 1 — 40% weight
    content_quality_score = models.FloatField(default=0.0)  # Factor 2 — 35% weight
    structural_score = models.FloatField(default=0.0)  # Factor 3 — 25% weight
    semantic_score = models.FloatField(default=0.0)  # Factor 4 — supplementary
    third_party_score = models.FloatField(default=0.0)  # Factor 5 — supplementary

    created_at = models.DateTimeField(auto_now_add=True)
    # Soft-delete so that deleting a prompt does NOT free a plan-limit slot.
    # Active (visible) prompts are those with deleted_at IS NULL; all rows
    # still count toward `max_prompts` usage.
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PromptTrack #{self.pk} — {self.prompt_text[:60]}"

    class Meta:
        indexes = [
            models.Index(fields=["analysis_run", "score", "created_at"]),
        ]


class PromptResult(models.Model):
    class Engine(models.TextChoices):
        CHATGPT = "chatgpt", "ChatGPT"
        CLAUDE = "claude", "Claude"
        GEMINI = "gemini", "Gemini"
        PERPLEXITY = "perplexity", "Perplexity"
        GOOGLE = "google", "Google"
        BING = "bing", "Bing"
        DEEPSEEK = "deepseek", "DeepSeek"
        GROK = "grok", "Grok"
        LLAMA = "llama", "Meta Llama"

    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEUTRAL = "neutral", "Neutral"
        NEGATIVE = "negative", "Negative"

    prompt_track = models.ForeignKey(PromptTrack, on_delete=models.CASCADE, related_name="results")
    engine = models.CharField(max_length=20, choices=Engine.choices)
    response_text = models.TextField(blank=True)
    brand_mentioned = models.BooleanField(default=False)
    sentiment = models.CharField(max_length=10, choices=Sentiment.choices, default=Sentiment.NEUTRAL)
    confidence = models.FloatField(default=0.0)
    rank_position = models.IntegerField(default=0)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["checked_at"]
        indexes = [
            models.Index(fields=["prompt_track", "engine"]),
            models.Index(fields=["prompt_track", "brand_mentioned"]),
        ]

    def __str__(self):
        return f"PromptResult [{self.engine}] {'✓' if self.brand_mentioned else '✗'} {self.sentiment}"


class PromptCitation(models.Model):
    """
    URLs cited by an AI engine (or search engine) when responding to a tracked prompt.
    Captures source attribution so "pages AI loves" roll-ups and competitor gap analysis
    can be derived per-run without re-parsing response text.
    """

    prompt_result = models.ForeignKey(PromptResult, on_delete=models.CASCADE, related_name="citations")
    url = models.URLField(max_length=2048)
    domain = models.CharField(max_length=255, blank=True, default="", db_index=True)
    title = models.CharField(max_length=512, blank=True, default="")
    snippet = models.TextField(blank=True, default="")
    position = models.IntegerField(default=0)
    is_brand = models.BooleanField(default=False, db_index=True)
    is_competitor = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prompt_result_id", "position", "id"]
        indexes = [
            models.Index(fields=["domain", "is_brand"]),
            models.Index(fields=["domain", "is_competitor"]),
        ]

    def __str__(self):
        return f"Citation {self.domain} (brand={self.is_brand}, rival={self.is_competitor})"


class PromptWikipediaDraft(models.Model):
    """Per-prompt cached Wikipedia draft kit (LLM-generated)."""

    prompt_track = models.OneToOneField(PromptTrack, on_delete=models.CASCADE, related_name="wikipedia_draft")
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PromptWikipediaDraft<track={self.prompt_track_id}>"


class PromptSchemaArtifact(models.Model):
    """A saved artifact (answer paragraph or JSON-LD) generated for a prompt."""

    class SchemaType(models.TextChoices):
        FAQ = "faq", "FAQ"
        ARTICLE = "article", "Article"
        PERSON = "person", "Person"
        ORGANIZATION = "organization", "Organization"
        ANSWER = "answer", "Direct answer"

    prompt_track = models.ForeignKey(PromptTrack, on_delete=models.CASCADE, related_name="schema_artifacts")
    schema_type = models.CharField(max_length=24, choices=SchemaType.choices)
    output = models.TextField()
    explanation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("prompt_track", "schema_type")]
        indexes = [models.Index(fields=["prompt_track", "schema_type"])]

    def __str__(self):
        return f"PromptSchemaArtifact<track={self.prompt_track_id} {self.schema_type}>"


class PromptEvalLog(models.Model):
    """A single prompt-evaluation result (Epic 6).

    One row per golden case (or live generation) judged by the LLM-as-judge. Persisting
    prompt name + version alongside the faithfulness/relevance/format scores and token
    usage is what makes prompt changes measurable over time and lets an eval run gate CI.
    """

    class Mode(models.TextChoices):
        RECORDED = "recorded", "Recorded known-good"
        LIVE = "live", "Live generation"

    prompt_name = models.CharField(max_length=64, db_index=True)
    prompt_version = models.CharField(max_length=16)
    case_id = models.CharField(max_length=128, blank=True, default="")
    mode = models.CharField(max_length=12, choices=Mode.choices, default=Mode.RECORDED)

    faithfulness = models.FloatField(default=0.0)
    relevance = models.FloatField(default=0.0)
    format_score = models.FloatField(default=0.0)
    passed = models.BooleanField(default=False, db_index=True)
    rationale = models.TextField(blank=True, default="")

    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    # [{"source_url": ..., "score": ...}] when the evaluated prompt used retrieval.
    retrieved_chunks = models.JSONField(default=list, blank=True)

    # Provenance only; a deleted run must not delete its eval history.
    source_run = models.ForeignKey(
        "analyzer.AnalysisRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prompt_eval_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["prompt_name", "prompt_version"]),
            models.Index(fields=["prompt_name", "passed"]),
        ]

    def __str__(self):
        return f"PromptEvalLog<{self.prompt_name}/{self.prompt_version}:{'pass' if self.passed else 'fail'}>"

