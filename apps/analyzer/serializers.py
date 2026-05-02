from rest_framework import serializers

from .models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    Recommendation,
    UserAction,
    UserGamification,
    BlogAutomationConfig,
    BlogAutomationJob,
    PromptTrack,
    PromptResult,
    PromptCitation,
    ScheduledAnalysis,
    SitemapAudit,
    SitemapAuditPage,
    AgentLogEntry,
    SchemaWatch,
    SchemaWatchPage,
    RankAudit,
    RankQuery,
    RankResult,
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
)


class RecommendationSerializer(serializers.ModelSerializer):
    can_auto_fix = serializers.SerializerMethodField()

    class Meta:
        model = Recommendation
        fields = [
            "id", "pillar", "priority", "title", "description",
            "action", "impact_estimate", "category", "can_auto_fix", "why",
            "steps", "xp_reward", "difficulty", "estimated_minutes", "finding_code", "finding_key",
        ]

    # Title keywords that indicate manual-only recommendations
    MANUAL_TITLE_KEYWORDS = [
        "sitemap", "enable https", "page load speed", "improve page load",
        "crawler blocked", "blocks automated", "too slow to crawl",
        "wikipedia", "reddit", "google ai overview",
        "brand into ai", "social profile", "brand website signal",
    ]

    # Fix types that can actually be auto-applied on any URL
    AUTO_FIX_TITLE_KEYWORDS = [
        "llms.txt", "robots.txt", "ai meta", "ai-meta", "ai crawler",
        "ai bot", "gptbot", "claudebot", "noindex",
    ]

    # Fix types that need a product/page URL — cannot auto-fix on homepage
    HOMEPAGE_MANUAL_TITLE_KEYWORDS = [
        "meta description", "seo title", "title tag", "meta title",
        "json-ld", "structured data", "schema",
        "faq", "expert quote", "author attribution", "first-hand",
        "about page", "contact page", "content", "keyword stuff",
        "review", "comparison", "shipping", "product description",
    ]

    def get_can_auto_fix(self, obj):
        title_lower = (obj.title or "").lower()
        cat_lower = (obj.category or "").lower()

        # Always manual
        for kw in self.MANUAL_TITLE_KEYWORDS:
            if kw in title_lower:
                return False

        # Check if this is a homepage analysis
        run = obj.analysis_run
        run_url = (run.url or "") if run else ""
        is_homepage = False
        if run_url:
            try:
                from urllib.parse import urlparse
                path = urlparse(run_url).path.rstrip("/")
                is_homepage = not path or path == ""
            except Exception:
                pass

        # On homepage: only specific fix types can auto-apply
        if is_homepage:
            for kw in self.AUTO_FIX_TITLE_KEYWORDS:
                if kw in title_lower:
                    return True
            # Schema category on homepage = theme extension (auto)
            # but schema issues like "missing schema" on homepage = manual
            return False

        # On product/page URLs: most things can be auto-fixed
        return True


class AIVisibilityProbeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIVisibilityProbe
        fields = [
            "id", "prompt_used", "llm_response", "brand_mentioned", "confidence",
        ]


class PageScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = PageScore
        fields = [
            "id", "url", "content_score", "content_details",
            "schema_score", "schema_details", "eeat_score", "eeat_details",
            "technical_score", "technical_details", "entity_score", "entity_details",
            "ai_visibility_score", "ai_visibility_details", "composite_score",
        ]


class BrandVisibilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandVisibility
        fields = [
            "google_score", "google_details",
            "reddit_score", "reddit_details",
            "web_mentions_score", "web_mentions_details",
            "social_presence_details",
            "ai_brand_facts",
            "overall_score",
        ]


class CompetitorSerializer(serializers.ModelSerializer):
    page_score = PageScoreSerializer(read_only=True)

    class Meta:
        model = Competitor
        fields = [
            "id", "name", "url", "industry",
            "tier", "target_market", "geography", "pricing_model",
            "estimated_revenue_band", "positioning", "relevance_score",
            "composite_score", "scored", "page_score",
        ]


class AnalysisRunListSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisRun
        fields = [
            "id", "slug", "url", "country", "run_type", "status", "progress",
            "composite_score", "created_at",
        ]


class AnalysisRunDetailSerializer(serializers.ModelSerializer):
    page_scores = PageScoreSerializer(many=True, read_only=True)
    competitors = CompetitorSerializer(many=True, read_only=True)
    recommendations = RecommendationSerializer(many=True, read_only=True)
    ai_probes = AIVisibilityProbeSerializer(many=True, read_only=True)
    brand_visibility = BrandVisibilitySerializer(read_only=True)
    display_brand_name = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisRun
        fields = [
            "id", "slug", "url", "brand_name", "display_brand_name", "country", "email", "run_type", "status", "progress",
            "composite_score", "error_message", "created_at", "updated_at",
            "page_scores", "competitors", "recommendations", "ai_probes",
            "brand_visibility",
        ]

    def get_display_brand_name(self, obj):
        from apps.analyzer.pipeline.brand_naming import visibility_brand_label

        return visibility_brand_label(getattr(obj, "url", "") or "", getattr(obj, "brand_name", "") or "")


class StartAnalysisSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2048)
    run_type = serializers.ChoiceField(
        choices=AnalysisRun.RunType.choices,
        default=AnalysisRun.RunType.SINGLE_PAGE,
    )
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    brand_name = serializers.CharField(
        max_length=255, required=False, allow_blank=True, default=""
    )
    country = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    org_id = serializers.IntegerField(required=False, allow_null=True)
    # When true (onboarding / post-checkout launch), require org ownership, URL match, brand, and prompts.
    verify_org_workspace = serializers.BooleanField(required=False, default=False)
    prompts = serializers.ListField(
        child=serializers.CharField(max_length=500),
        required=False,
        allow_empty=True,
    )

    def validate_url(self, value):
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value

    def validate_email(self, value):
        return value.lower().strip() if value else ""

    def validate_country(self, value):
        return value.strip() if value else ""

    def validate(self, attrs):
        from apps.organizations.models import Organization

        from .workspace_urls import normalize_workspace_url

        verify = attrs.get("verify_org_workspace") is True
        raw_prompts = attrs.get("prompts")
        if not raw_prompts:
            raw_prompts = []
        cleaned = [
            p.strip()
            for p in raw_prompts
            if isinstance(p, str) and p.strip()
        ]
        if len(cleaned) > 15:
            raise serializers.ValidationError(
                {"prompts": "You can track at most 15 prompts."}
            )

        if verify:
            org_id = attrs.get("org_id")
            if not org_id:
                raise serializers.ValidationError(
                    {
                        "org_id": "Create your workspace first, then launch analysis from onboarding."
                    }
                )
            email = (attrs.get("email") or "").strip().lower()
            if not email:
                raise serializers.ValidationError(
                    {
                        "email": "Sign in to continue — we need your account email to verify your workspace."
                    }
                )
            brand = (attrs.get("brand_name") or "").strip()
            if not brand:
                raise serializers.ValidationError(
                    {
                        "brand_name": "Brand name is required. Go back to the first step and enter your company name."
                    }
                )
            if len(cleaned) < 1:
                raise serializers.ValidationError(
                    {
                        "prompts": "Add at least one tracking prompt before launching."
                    }
                )

            org = Organization.objects.filter(pk=org_id).first()
            if not org:
                raise serializers.ValidationError(
                    {
                        "org_id": "Workspace not found. Complete company setup, then try again."
                    }
                )
            if org.owner_email.strip().lower() != email:
                raise serializers.ValidationError(
                    {"org_id": "This workspace does not belong to your account."}
                )

            org_url = (org.url or "").strip()
            if org_url:
                req_norm = normalize_workspace_url(attrs["url"])
                org_norm = normalize_workspace_url(org_url)
                if req_norm != org_norm:
                    raise serializers.ValidationError(
                        {
                            "url": "Website URL must match your workspace URL. Go back and correct it, or update your workspace."
                        }
                    )

        attrs["_cleaned_prompts"] = cleaned
        return attrs


# ============ Gamification Serializers ============

class AchievementSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    icon = serializers.CharField()
    points = serializers.IntegerField()


class UserGamificationSerializer(serializers.ModelSerializer):
    achievements_detail = serializers.SerializerMethodField()
    level_name = serializers.CharField(source="get_level_display")
    level_progress = serializers.FloatField()

    class Meta:
        model = UserGamification
        fields = [
            "user_email",
            "total_points",
            "points_this_week",
            "points_this_month",
            "level",
            "level_name",
            "current_level_points",
            "points_to_next_level",
            "level_progress",
            "current_streak",
            "longest_streak",
            "total_actions_completed",
            "total_actions_verified",
            "total_score_improvement",
            "achievements",
            "achievements_detail",
            "created_at",
            "updated_at",
        ]

    def get_achievements_detail(self, obj):
        return [
            {**ACHIEVEMENTS_INFO.get(code, {}), "code": code}
            for code in obj.achievements
            if code in ACHIEVEMENTS_INFO
        ]


class UserActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAction
        fields = [
            "id",
            "action_type",
            "title",
            "description",
            "points_value",
            "status",
            "started_at",
            "completed_at",
            "verified_at",
            "score_before",
            "score_after",
            "score_improvement",
            "notes",
            "created_at",
            "analysis_run",
            "recommendation",
        ]
        read_only_fields = [
            "points_value", "started_at", "completed_at", "verified_at",
            "score_before", "score_after", "score_improvement", "created_at"
        ]


class CreateUserActionSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=UserAction.ActionType.choices)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    recommendation_id = serializers.IntegerField(required=False)
    analysis_run_id = serializers.IntegerField(required=False)
    score_before = serializers.FloatField(required=False)


class UpdateUserActionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=UserAction.ActionStatus.choices,
        required=False
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    score_after = serializers.FloatField(required=False)


class ActionTemplateSerializer(serializers.Serializer):
    action_type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    points = serializers.IntegerField()
    category = serializers.CharField()


# ============ Helper Serializers ============

class ActionStatsSerializer(serializers.Serializer):
    total_actions = serializers.IntegerField()
    pending_actions = serializers.IntegerField()
    in_progress_actions = serializers.IntegerField()
    completed_actions = serializers.IntegerField()
    verified_actions = serializers.IntegerField()
    total_points = serializers.IntegerField()
    points_this_week = serializers.IntegerField()
    current_streak = serializers.IntegerField()
    level = serializers.IntegerField()
    level_name = serializers.CharField()
    level_progress = serializers.FloatField()
    recent_achievements = AchievementSerializer(many=True)


class PromptCitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptCitation
        fields = [
            "id", "url", "domain", "title", "snippet",
            "position", "is_brand", "is_competitor",
        ]


class PromptResultSerializer(serializers.ModelSerializer):
    response_text = serializers.SerializerMethodField()
    citations = PromptCitationSerializer(many=True, read_only=True)

    def get_response_text(self, obj):
        text = obj.response_text or ""
        return text[:500] if len(text) > 500 else text

    class Meta:
        model = PromptResult
        fields = [
            "id", "engine", "response_text", "brand_mentioned",
            "sentiment", "confidence", "rank_position", "checked_at",
            "citations",
        ]


class PromptResultFullSerializer(serializers.ModelSerializer):
    citations = PromptCitationSerializer(many=True, read_only=True)

    class Meta:
        model = PromptResult
        fields = [
            "id", "engine", "response_text", "brand_mentioned",
            "sentiment", "confidence", "rank_position", "checked_at",
            "citations",
        ]


class PromptTrackSerializer(serializers.ModelSerializer):
    results = PromptResultSerializer(many=True, read_only=True)
    intent = serializers.SerializerMethodField()
    prompt_type = serializers.SerializerMethodField()
    visibility_pct = serializers.SerializerMethodField()
    avg_position = serializers.SerializerMethodField()
    sentiment_label = serializers.SerializerMethodField()
    ranking_label = serializers.SerializerMethodField()
    total_runs = serializers.SerializerMethodField()
    mentions = serializers.SerializerMethodField()
    # 5-factor breakdown (computed live so they reflect the latest results)
    factor_authority = serializers.SerializerMethodField()
    factor_content_quality = serializers.SerializerMethodField()
    factor_structural = serializers.SerializerMethodField()
    factor_semantic = serializers.SerializerMethodField()
    factor_third_party = serializers.SerializerMethodField()

    class Meta:
        model = PromptTrack
        fields = [
            "id", "prompt_text", "is_custom", "intent", "prompt_type", "score",
            "created_at", "results",
            "visibility_pct", "avg_position", "sentiment_label", "ranking_label",
            "total_runs", "mentions",
            # 5-factor scores
            "factor_authority", "factor_content_quality", "factor_structural",
            "factor_semantic", "factor_third_party",
        ]

    def _taxonomy(self, obj):
        """Recompute from prompt + run so labels stay accurate when rules improve."""
        if not hasattr(obj, "_taxonomy_cache"):
            from .pipeline.prompt_tracker import classify_prompt_intent_and_type

            run = getattr(obj, "analysis_run", None)
            if run is None:
                obj._taxonomy_cache = ("informational", "organic")
            else:
                brand = (getattr(run, "brand_name", None) or "").strip()
                url = (getattr(run, "url", None) or "").strip()
                obj._taxonomy_cache = classify_prompt_intent_and_type(
                    obj.prompt_text,
                    brand,
                    url,
                )
        return obj._taxonomy_cache

    def get_intent(self, obj):
        return self._taxonomy(obj)[0]

    def get_prompt_type(self, obj):
        return self._taxonomy(obj)[1]

    def _score_data(self, obj):
        if not hasattr(obj, "_score_cache"):
            from .pipeline.prompt_tracker import compute_prompt_score
            # Read from the prefetched .results manager rather than .values()
            # — .values() re-queries the DB even when results are prefetched.
            results = [
                {
                    "brand_mentioned": r.brand_mentioned,
                    "sentiment": r.sentiment,
                    "rank_position": r.rank_position,
                    "confidence": r.confidence,
                    "engine": r.engine,
                }
                for r in obj.results.all()
            ]
            obj._score_cache = compute_prompt_score(results)
        return obj._score_cache

    def get_visibility_pct(self, obj):
        return self._score_data(obj)["visibility_pct"]

    def get_avg_position(self, obj):
        return self._score_data(obj)["avg_position"]

    def get_sentiment_label(self, obj):
        return self._score_data(obj)["sentiment"]

    def get_factor_authority(self, obj):
        return self._score_data(obj)["authority_score"]

    def get_factor_content_quality(self, obj):
        return self._score_data(obj)["content_quality_score"]

    def get_factor_structural(self, obj):
        return self._score_data(obj)["structural_score"]

    def get_factor_semantic(self, obj):
        return self._score_data(obj)["semantic_score"]

    def get_factor_third_party(self, obj):
        return self._score_data(obj)["third_party_score"]

    def get_ranking_label(self, obj):
        return self._score_data(obj)["label"]

    def get_total_runs(self, obj):
        return self._score_data(obj)["total_runs"]

    def get_mentions(self, obj):
        return self._score_data(obj)["mentions"]


class AddPromptSerializer(serializers.Serializer):
    prompt_text = serializers.CharField(max_length=2000)


class ShareOfVoiceSerializer(serializers.Serializer):
    engine = serializers.CharField()
    total = serializers.IntegerField()
    mentioned = serializers.IntegerField()
    sov_pct = serializers.FloatField()


class CitationTrendPointSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    engine = serializers.CharField()
    rate_pct = serializers.FloatField()


class BlogAutomationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogAutomationConfig
        fields = [
            "id",
            "user_email",
            "site_url",
            "topic",
            "keywords",
            "frequency_per_day",
            "publish_time",
            "mode",
            "publish_provider",
            "is_active",
            "last_queued_for",
            "created_at",
            "updated_at",
        ]


class BlogAutomationJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogAutomationJob
        fields = [
            "id",
            "status",
            "scheduled_for",
            "provider",
            "mode",
            "topic",
            "keywords",
            "title",
            "slug",
            "meta_description",
            "excerpt",
            "content_markdown",
            "tags",
            "external_post_id",
            "external_post_url",
            "published_at",
            "error_message",
            "created_at",
            "updated_at",
        ]


class ScheduledAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledAnalysis
        fields = [
            "id", "email", "url", "brand_name", "frequency",
            "next_run_at", "last_run_at", "last_run_slug",
            "is_active", "created_at",
        ]


class SitemapAuditPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SitemapAuditPage
        fields = [
            "id", "url", "path", "final_url", "state",
            "status_code", "redirect_count",
            "title", "meta_description", "h1_count",
            "word_count", "text_ratio", "content_length",
            "lcp_ms", "fcp_ms", "ttfb_ms", "server_ms",
            "resource_count", "resource_bytes",
            "link_count_total", "link_count_internal", "link_count_external",
            "jsonld_count", "has_canonical", "has_og", "is_noindex",
            "robots_allows_gptbot", "robots_allows_claudebot",
            "robots_allows_perplexitybot",
            "ai_score", "severity", "findings",
            "error_message", "checked_at",
        ]


class SitemapAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = SitemapAudit
        fields = [
            "id", "status", "progress",
            "sitemap_url", "crawl_limit",
            "total_urls", "indexed_count", "redirect_count",
            "queued_count", "failed_count",
            "avg_lcp_ms", "avg_fcp_ms", "avg_ttfb_ms", "avg_ai_score",
            "truncated", "discovered_url_count",
            "started_at", "finished_at", "created_at",
            "error_message",
        ]


class AgentLogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentLogEntry
        fields = ["id", "bot_name", "path", "status_code", "ts", "source"]


class SchemaWatchPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaWatchPage
        fields = [
            "id", "url", "path", "page_kind",
            "status_code",
            "schema_types", "jsonld_count", "raw_jsonld",
            "severity", "issues", "fix_targets",
            "error_message", "checked_at",
        ]


class SchemaWatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchemaWatch
        fields = [
            "id", "status", "progress",
            "total_urls", "healthy_count", "warn_count", "broken_count",
            "discovered_from_sitemap",
            "started_at", "finished_at", "created_at",
            "error_message",
        ]


class RankResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankResult
        fields = [
            "id", "surface", "position",
            "url", "domain", "title", "snippet",
            "engine", "response_text",
            "sentiment",
            "is_brand_mentioned", "competitors_mentioned",
            "upvotes", "subreddit",
            "checked_at",
        ]


class RankQuerySerializer(serializers.ModelSerializer):
    results = RankResultSerializer(many=True, read_only=True)

    class Meta:
        model = RankQuery
        fields = [
            "id", "prompt_text", "rank",
            "brand_mention_count", "status", "error_message",
            "results",
        ]


class RankAuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = RankAudit
        fields = [
            "id", "status", "progress",
            "total_queries", "queries_done",
            "avg_brand_mentions", "avg_top3_brand_rate",
            "started_at", "finished_at", "created_at",
            "error_message",
        ]
