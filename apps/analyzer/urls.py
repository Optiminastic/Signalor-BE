from django.urls import path

from .views import (
    CompetitorListCreateView,
    CompetitorDetailView,
    AnalysisRunBySlugView,
    AnalysisRunDetailView,
    AnalysisRunListView,
    AnalysisRunStatusView,
    ExportPDFView,
    StartAnalysisView,
    HealthCheckView,
    # Gamification views
    UserGamificationView,
    ActionTemplatesView,
    AchievementsView,
    UserActionListView,
    CreateUserActionView,
    UpdateUserActionView,
    ActionStatsView,
    CrawlEssentialsStatusView,
    BlogAutomationConfigView,
    BlogAutomationCalendarView,
    BlogAutomationProcessDueView,
    BlogAutomationGenerateView,
    BlogAutomationPublishView,
    QuickActionView,
    BulkCreateUserActionView,
    # Prompt tracking views
    PromptListCreateView,
    ShareOfVoiceView,
    CitationTrendView,
    RecheckPromptView,
    RecheckAllPromptsView,
    # New features
    ScoreHistoryView,
    ScheduledAnalysisView,
    AutoFixView,
    GeoImprovementsView,
    ApplyGeoFixesAndReanalyzeView,
)

app_name = "analyzer"

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("analyze/", StartAnalysisView.as_view(), name="start-analysis"),
    path("runs/history/", ScoreHistoryView.as_view(), name="run-history"),
    path("schedule/", ScheduledAnalysisView.as_view(), name="schedule"),
    path("runs/", AnalysisRunListView.as_view(), name="run-list"),
    path("runs/<int:run_id>/", AnalysisRunDetailView.as_view(), name="run-detail"),
    path("runs/s/<str:slug>/prompts/",                          PromptListCreateView.as_view(),   name="prompt-list-create"),
    path("runs/s/<str:slug>/prompts/<int:track_id>/recheck/",  RecheckPromptView.as_view(),      name="prompt-recheck"),
    path("runs/s/<str:slug>/recheck-all/",                     RecheckAllPromptsView.as_view(),  name="prompt-recheck-all"),
    path("runs/s/<str:slug>/share-of-voice/",                  ShareOfVoiceView.as_view(),       name="share-of-voice"),
    path("runs/s/<str:slug>/citation-trend/",                  CitationTrendView.as_view(),      name="citation-trend"),
    path("runs/s/<str:slug>/geo-improvements/", GeoImprovementsView.as_view(), name="geo-improvements"),
    path("runs/s/<str:slug>/apply-geo-fixes/", ApplyGeoFixesAndReanalyzeView.as_view(), name="apply-geo-fixes"),
    path("runs/s/<str:slug>/competitors/", CompetitorListCreateView.as_view(), name="competitor-list-create"),
    path("runs/s/<str:slug>/competitors/<int:competitor_id>/", CompetitorDetailView.as_view(), name="competitor-detail"),
    path("runs/s/<str:slug>/auto-fix/", AutoFixView.as_view(), name="auto-fix"),
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
    path("actions/blog-automation/config/", BlogAutomationConfigView.as_view(), name="blog-automation-config"),
    path("actions/blog-automation/calendar/", BlogAutomationCalendarView.as_view(), name="blog-automation-calendar"),
    path("actions/blog-automation/process-due/", BlogAutomationProcessDueView.as_view(), name="blog-automation-process-due"),
    path("actions/blog-automation/generate/", BlogAutomationGenerateView.as_view(), name="blog-automation-generate"),
    path("actions/blog-automation/publish/", BlogAutomationPublishView.as_view(), name="blog-automation-publish"),
    path("actions/quick/", QuickActionView.as_view(), name="quick-action"),
    path("actions/bulk-create/", BulkCreateUserActionView.as_view(), name="bulk-create-action"),
]
