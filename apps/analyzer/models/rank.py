"""Keyword rank auditing."""

from django.db import models

from .run import AnalysisRun


class RankAudit(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="rank_audits")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    progress = models.IntegerField(default=0)
    total_queries = models.IntegerField(default=0)
    queries_done = models.IntegerField(default=0)

    avg_brand_mentions = models.FloatField(default=0.0)
    avg_top3_brand_rate = models.FloatField(default=0.0)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RankAudit<{self.analysis_run_id} {self.status} {self.progress}%>"


class RankQuery(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        DONE = "done"
        FAILED = "failed"

    audit = models.ForeignKey(RankAudit, on_delete=models.CASCADE, related_name="queries")
    prompt_text = models.TextField()
    rank = models.IntegerField(default=0)
    brand_mention_count = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["audit_id", "rank", "id"]

    def __str__(self):
        return f"RankQuery<{self.audit_id} #{self.rank} {self.status}>"


class RankResult(models.Model):
    class Surface(models.TextChoices):
        GOOGLE = "google"
        REDDIT = "reddit"
        QUORA = "quora"
        AI = "ai"

    query = models.ForeignKey(RankQuery, on_delete=models.CASCADE, related_name="results")
    surface = models.CharField(max_length=16, choices=Surface.choices, db_index=True)
    position = models.IntegerField()
    url = models.URLField(max_length=2048, blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=300, blank=True, default="")
    snippet = models.TextField(blank=True, default="")

    # AI-surface specific — blank for SERP rows
    engine = models.CharField(max_length=64, blank=True, default="")
    response_text = models.TextField(blank=True, default="")

    class Sentiment(models.TextChoices):
        POSITIVE = "positive"
        NEUTRAL = "neutral"
        NEGATIVE = "negative"

    sentiment = models.CharField(max_length=10, choices=Sentiment.choices, default=Sentiment.NEUTRAL)

    is_brand_mentioned = models.BooleanField(default=False)
    competitors_mentioned = models.JSONField(default=list, blank=True)

    upvotes = models.IntegerField(null=True, blank=True)
    subreddit = models.CharField(max_length=120, blank=True, default="")

    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["query_id", "surface", "position"]
        indexes = [
            models.Index(fields=["query", "surface", "position"]),
        ]

    def __str__(self):
        return f"RankResult<{self.query_id} {self.surface}#{self.position}>"

