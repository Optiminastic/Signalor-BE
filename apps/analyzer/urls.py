from django.urls import path

from .views import (
    AchievementsView,
    ActionStatsView,
    ActionTemplatesView,
    # Sitemap audit
    AgentLogView,
    AiChatView,
    AiRecommendationSummaryView,
    AnalysisRunBySlugView,
    AnalysisRunDetailView,
    AnalysisRunListView,
    AnalysisRunStatusView,
    ApplyGeoFixesAndReanalyzeView,
    AutoFixApproveView,
    AutoFixPreviewView,
    AutoFixVerifyView,
    # New features
    AutoFixView,
    BacklinkCatalogView,
    BacklinkOrderConfirmPaymentView,
    BacklinkOrderDetailView,
    BacklinkOrderListCreateView,
    BlogAutomationCalendarView,
    BlogAutomationConfigView,
    BlogAutomationGenerateView,
    BlogAutomationProcessDueView,
    BlogAutomationPublishView,
    BulkCreateUserActionView,
    CitationSourcesView,
    CitationTrendView,
    CompetitorDetailView,
    CompetitorListCreateView,
    ContentApplyElementView,
    ContentPageFieldsView,
    # Content optimisation (Cursor-style edit + save)
    ContentPagesView,
    ContentRawFilesListView,
    ContentRawFileUpsertView,
    ContentRewriteElementView,
    ContentSaveView,
    ContentSuggestionDismissView,
    ContentSuggestionsView,
    CrawlEssentialsStatusView,
    CreateUserActionView,
    DomainAnalyticsView,
    ExportPDFView,
    GeneratePromptsView,
    GeoImprovementsView,
    HealthCheckView,
    OnboardingStartView,
    PromptBacklinksView,
    PromptDeleteView,
    PromptListCreateView,
    PromptOpportunitiesView,
    PromptOpportunityDetailView,
    PromptRankView,
    PromptResultDetailView,
    PromptSchemaView,
    PromptWikipediaDraftView,
    QuickActionView,
    # Schema watchtower
    RankAuditDetailView,
    RankAuditRefreshQueryView,
    # Rank tracker
    RankAuditStartView,
    RecheckAllPromptsView,
    RecheckPromptView,
    RunBacklinkFreeView,
    ScheduledAnalysisView,
    SchemaWatchDetailView,
    # Schema watchtower
    SchemaWatchStartView,
    # New features
    ScoreHistoryView,
    ShareOfVoiceView,
    SitemapAuditDetailView,
    # Sitemap audit
    SitemapAuditStartView,
    StartAnalysisView,
    UpdateUserActionView,
    UserActionListView,
    # Gamification views
    UserGamificationView,
    WeeklyTestEmailView,
)

app_name = "analyzer"

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("onboarding-start/", OnboardingStartView.as_view(), name="onboarding-start"),
    path("analyze/", StartAnalysisView.as_view(), name="start-analysis"),
    path("generate-prompts/", GeneratePromptsView.as_view(), name="generate-prompts"),
    path("runs/history/", ScoreHistoryView.as_view(), name="run-history"),
    path("schedule/", ScheduledAnalysisView.as_view(), name="schedule"),
    path("runs/", AnalysisRunListView.as_view(), name="run-list"),
    path("runs/<int:run_id>/", AnalysisRunDetailView.as_view(), name="run-detail"),
    path("runs/s/<str:slug>/prompts/", PromptListCreateView.as_view(), name="prompt-list-create"),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/results/<int:result_id>/",
        PromptResultDetailView.as_view(),
        name="prompt-result-detail",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/recheck/",
        RecheckPromptView.as_view(),
        name="prompt-recheck",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/backlinks/",
        PromptBacklinksView.as_view(),
        name="prompt-backlinks",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/opportunities/",
        PromptOpportunitiesView.as_view(),
        name="prompt-opportunities",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/opportunities/<int:opp_id>/",
        PromptOpportunityDetailView.as_view(),
        name="prompt-opportunity-detail",
    ),
    path("runs/s/<str:slug>/prompts/<int:track_id>/", PromptDeleteView.as_view(), name="prompt-delete"),
    path("runs/s/<str:slug>/recheck-all/", RecheckAllPromptsView.as_view(), name="prompt-recheck-all"),
    path("runs/s/<str:slug>/share-of-voice/", ShareOfVoiceView.as_view(), name="share-of-voice"),
    path("runs/s/<str:slug>/citation-trend/", CitationTrendView.as_view(), name="citation-trend"),
    path("runs/s/<str:slug>/citations/", CitationSourcesView.as_view(), name="citation-sources"),
    path(
        "runs/s/<str:slug>/ai-recommendation-summary/",
        AiRecommendationSummaryView.as_view(),
        name="ai-recommendation-summary",
    ),
    path("runs/s/<str:slug>/geo-improvements/", GeoImprovementsView.as_view(), name="geo-improvements"),
    path(
        "runs/s/<str:slug>/apply-geo-fixes/", ApplyGeoFixesAndReanalyzeView.as_view(), name="apply-geo-fixes"
    ),
    path("runs/s/<str:slug>/competitors/", CompetitorListCreateView.as_view(), name="competitor-list-create"),
    path(
        "runs/s/<str:slug>/competitors/<int:competitor_id>/",
        CompetitorDetailView.as_view(),
        name="competitor-detail",
    ),
    path("runs/s/<str:slug>/auto-fix/", AutoFixView.as_view(), name="auto-fix"),
    path("runs/s/<str:slug>/auto-fix/preview/", AutoFixPreviewView.as_view(), name="auto-fix-preview"),
    path("runs/s/<str:slug>/auto-fix/approve/", AutoFixApproveView.as_view(), name="auto-fix-approve"),
    path("runs/s/<str:slug>/auto-fix/verify/", AutoFixVerifyView.as_view(), name="auto-fix-verify"),
    path("runs/s/<str:slug>/chat/", AiChatView.as_view(), name="ai-chat"),
    path(
        "runs/s/<str:slug>/domain-analytics/",
        DomainAnalyticsView.as_view(),
        name="domain-analytics",
    ),
    # Sitemap audit + AI agent log stub
    path("runs/s/<str:slug>/sitemap/", SitemapAuditDetailView.as_view(), name="sitemap-audit-detail"),
    path("runs/s/<str:slug>/sitemap/start/", SitemapAuditStartView.as_view(), name="sitemap-audit-start"),
    path("runs/s/<str:slug>/agent-log/", AgentLogView.as_view(), name="agent-log"),
    # Schema watchtower
    path("runs/s/<str:slug>/schema-watch/", SchemaWatchDetailView.as_view(), name="schema-watch-detail"),
    path("runs/s/<str:slug>/schema-watch/start/", SchemaWatchStartView.as_view(), name="schema-watch-start"),
    # Rank tracker
    path("runs/s/<str:slug>/rank/", RankAuditDetailView.as_view(), name="rank-audit-detail"),
    path("runs/s/<str:slug>/rank/start/", RankAuditStartView.as_view(), name="rank-audit-start"),
    path(
        "runs/s/<str:slug>/rank/query/<int:query_id>/refresh/",
        RankAuditRefreshQueryView.as_view(),
        name="rank-audit-refresh-query",
    ),
    path("runs/s/<str:slug>/prompts/<int:track_id>/rank/", PromptRankView.as_view(), name="prompt-rank"),
    # Backlink marketplace
    path("runs/s/<str:slug>/backlinks/free/", RunBacklinkFreeView.as_view(), name="backlink-free"),
    path("runs/s/<str:slug>/backlinks/catalog/", BacklinkCatalogView.as_view(), name="backlink-catalog"),
    path(
        "runs/s/<str:slug>/backlinks/orders/", BacklinkOrderListCreateView.as_view(), name="backlink-orders"
    ),
    path(
        "runs/s/<str:slug>/backlinks/orders/<int:order_id>/",
        BacklinkOrderDetailView.as_view(),
        name="backlink-order-detail",
    ),
    path(
        "runs/s/<str:slug>/backlinks/orders/<int:order_id>/confirm-payment/",
        BacklinkOrderConfirmPaymentView.as_view(),
        name="backlink-order-confirm-payment",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/wikipedia/draft/",
        PromptWikipediaDraftView.as_view(),
        name="prompt-wikipedia-draft",
    ),
    path(
        "runs/s/<str:slug>/prompts/<int:track_id>/schema/", PromptSchemaView.as_view(), name="prompt-schema"
    ),
    # Content optimisation
    path("runs/s/<str:slug>/content/pages/", ContentPagesView.as_view(), name="content-pages"),
    path("runs/s/<str:slug>/content/page/", ContentPageFieldsView.as_view(), name="content-page-fields"),
    path(
        "runs/s/<str:slug>/content/suggestions/", ContentSuggestionsView.as_view(), name="content-suggestions"
    ),
    path(
        "runs/s/<str:slug>/content/suggestions/<int:suggestion_id>/dismiss/",
        ContentSuggestionDismissView.as_view(),
        name="content-suggestion-dismiss",
    ),
    path("runs/s/<str:slug>/content/save/", ContentSaveView.as_view(), name="content-save"),
    path(
        "runs/s/<str:slug>/content/rewrite-element/",
        ContentRewriteElementView.as_view(),
        name="content-rewrite-element",
    ),
    path(
        "runs/s/<str:slug>/content/apply-element/",
        ContentApplyElementView.as_view(),
        name="content-apply-element",
    ),
    path(
        "runs/s/<str:slug>/content/raw-files/",
        ContentRawFilesListView.as_view(),
        name="content-raw-files-list",
    ),
    path(
        "runs/s/<str:slug>/content/raw-files/<str:name>/",
        ContentRawFileUpsertView.as_view(),
        name="content-raw-files-upsert",
    ),
    path("runs/s/<str:slug>/", AnalysisRunBySlugView.as_view(), name="run-by-slug"),
    path("runs/<int:run_id>/status/", AnalysisRunStatusView.as_view(), name="run-status"),
    path("runs/<int:run_id>/export-pdf/", ExportPDFView.as_view(), name="export-pdf"),
    # Gamification endpoints
    path("gamification/", UserGamificationView.as_view(), name="gamification"),
    path("gamification/stats/", ActionStatsView.as_view(), name="action-stats"),
    path("achievements/", AchievementsView.as_view(), name="achievements"),
    path("action-templates/", ActionTemplatesView.as_view(), name="action-templates"),
    # Action endpoints
    path("actions/", UserActionListView.as_view(), name="action-list"),
    path("actions/create/", CreateUserActionView.as_view(), name="action-create"),
    path("actions/<int:action_id>/", UpdateUserActionView.as_view(), name="action-update"),
    path("actions/crawl-essentials/", CrawlEssentialsStatusView.as_view(), name="crawl-essentials"),
    path(
        "actions/blog-automation/config/", BlogAutomationConfigView.as_view(), name="blog-automation-config"
    ),
    path(
        "actions/blog-automation/calendar/",
        BlogAutomationCalendarView.as_view(),
        name="blog-automation-calendar",
    ),
    path(
        "actions/blog-automation/process-due/",
        BlogAutomationProcessDueView.as_view(),
        name="blog-automation-process-due",
    ),
    path(
        "actions/blog-automation/generate/",
        BlogAutomationGenerateView.as_view(),
        name="blog-automation-generate",
    ),
    path(
        "actions/blog-automation/publish/",
        BlogAutomationPublishView.as_view(),
        name="blog-automation-publish",
    ),
    path("actions/quick/", QuickActionView.as_view(), name="quick-action"),
    path("actions/bulk-create/", BulkCreateUserActionView.as_view(), name="bulk-create-action"),
    # Weekly email test
    path("runs/s/<str:slug>/email/weekly-test/", WeeklyTestEmailView.as_view(), name="weekly-test-email"),
]
