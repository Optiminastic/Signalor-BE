"""Brand-level artefacts: kit, overview insights, domain analytics."""

from django.db import models

from .run import AnalysisRun


class BrandKit(models.Model):
    """Per-run cached brand submission kit (LLM-generated)."""

    analysis_run = models.OneToOneField(AnalysisRun, on_delete=models.CASCADE, related_name="brand_kit")
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"BrandKit<run={self.analysis_run_id}>"


class OverviewInsightReport(models.Model):
    """Per-run cached AI insight report blending analyzer + GA4 + GSC signals.

    ``payload`` holds {summary, insights[], tasks[], has_ga, has_gsc}. The tasks
    are also persisted as tagged Recommendation rows (source="ai_insight").
    """

    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="overview_insights"
    )
    payload = models.JSONField(default=dict)
    has_ga = models.BooleanField(default=False)
    has_gsc = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"OverviewInsightReport<run={self.analysis_run_id}>"


class DomainAnalyticsSnapshot(models.Model):
    """
    Per-run cached DataForSEO Domain Analytics snapshot — estimated organic
    traffic, top keywords, top pages, and per-country geographic breakdown.
    Refreshed on demand; default TTL is 7 days.
    """

    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="domain_analytics"
    )
    overview = models.JSONField(default=dict)
    top_keywords = models.JSONField(default=list)
    top_pages = models.JSONField(default=list)
    geo_distribution = models.JSONField(default=dict)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DomainAnalyticsSnapshot<run={self.analysis_run_id}>"



class EntityResolutionReport(models.Model):
    """Per-run cached "do engines know this brand?" probe.

    Cached because the probe is billable: it asks every engine live, one call
    each. Without a cache the endpoint respends on every click, and a run slug
    is not a credential, so anyone holding one could bill the account
    repeatedly. ``probed_at`` also backs the per-run cooldown.
    """

    analysis_run = models.OneToOneField(
        AnalysisRun, on_delete=models.CASCADE, related_name="entity_resolution"
    )
    payload = models.JSONField(default=dict)
    probed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"EntityResolutionReport<run={self.analysis_run_id}>"
