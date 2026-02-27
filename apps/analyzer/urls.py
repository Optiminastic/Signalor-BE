from django.urls import path

from .views import (
    AnalysisRunDetailView,
    AnalysisRunListView,
    AnalysisRunStatusView,
    ExportPDFView,
    StartAnalysisView,
    HealthCheckView,  # Add this import
    # Gamification views
    UserGamificationView,
    ActionTemplatesView,
    AchievementsView,
    UserActionListView,
    CreateUserActionView,
    UpdateUserActionView,
    ActionStatsView,
    QuickActionView,
    BulkCreateUserActionView,
)

app_name = "analyzer"

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("analyze/", StartAnalysisView.as_view(), name="start-analysis"),
    path("runs/", AnalysisRunListView.as_view(), name="run-list"),
    path("runs/<int:run_id>/", AnalysisRunDetailView.as_view(), name="run-detail"),
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
    path("actions/quick/", QuickActionView.as_view(), name="quick-action"),
    path("actions/bulk-create/", BulkCreateUserActionView.as_view(), name="bulk-create-action"),
]
