from django.contrib import admin

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    Competitor,
    PageScore,
    PromptEvalLog,
    Recommendation,
)


class PageScoreInline(admin.TabularInline):
    model = PageScore
    extra = 0
    readonly_fields = [
        "url",
        "content_score",
        "schema_score",
        "eeat_score",
        "technical_score",
        "entity_score",
        "ai_visibility_score",
        "composite_score",
    ]


class CompetitorInline(admin.TabularInline):
    model = Competitor
    extra = 0


class RecommendationInline(admin.TabularInline):
    model = Recommendation
    extra = 0
    readonly_fields = ["pillar", "priority", "title"]


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ["id", "url", "status", "composite_score", "created_at"]
    list_filter = ["status", "run_type"]
    search_fields = ["url", "email"]
    inlines = [PageScoreInline, CompetitorInline, RecommendationInline]


@admin.register(PageScore)
class PageScoreAdmin(admin.ModelAdmin):
    list_display = ["url", "composite_score", "content_score", "schema_score"]


@admin.register(Competitor)
class CompetitorAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "composite_score", "scored"]


@admin.register(AIVisibilityProbe)
class AIVisibilityProbeAdmin(admin.ModelAdmin):
    list_display = ["prompt_used", "brand_mentioned", "confidence"]


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ["title", "pillar", "priority", "category"]
    list_filter = ["priority", "pillar"]


@admin.register(PromptEvalLog)
class PromptEvalLogAdmin(admin.ModelAdmin):
    list_display = [
        "prompt_name",
        "prompt_version",
        "case_id",
        "mode",
        "passed",
        "faithfulness",
        "relevance",
        "format_score",
        "total_tokens",
        "created_at",
    ]
    list_filter = ["passed", "mode", "prompt_name", "prompt_version"]
    search_fields = ["prompt_name", "case_id"]
    readonly_fields = [f.name for f in PromptEvalLog._meta.fields]
