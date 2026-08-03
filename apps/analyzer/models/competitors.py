"""Competitors discovered for a run."""

from django.db import models

from .run import AnalysisRun, PageScore


class Competitor(models.Model):
    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="competitors")
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=2048)
    industry = models.CharField(max_length=255, blank=True, default="")
    tier = models.CharField(max_length=20, blank=True, default="")
    target_market = models.CharField(max_length=80, blank=True, default="")
    geography = models.CharField(max_length=80, blank=True, default="")
    pricing_model = models.CharField(max_length=80, blank=True, default="")
    estimated_revenue_band = models.CharField(max_length=40, blank=True, default="")
    positioning = models.CharField(max_length=240, blank=True, default="")
    relevance_score = models.IntegerField(null=True, blank=True)
    composite_score = models.FloatField(null=True, blank=True)
    scored = models.BooleanField(default=False)
    page_score = models.OneToOneField(
        PageScore, on_delete=models.SET_NULL, null=True, blank=True, related_name="competitor"
    )

    def __str__(self):
        return f"{self.name} ({self.url})"

