from django.urls import path

from .views import (
    StartVisibilityCheckView,
    VisibilityCheckDetailView,
    VisibilityCheckListView,
    VisibilityCheckStatusView,
)

app_name = "visibility"

urlpatterns = [
    path("check/", StartVisibilityCheckView.as_view(), name="start-check"),
    path("checks/", VisibilityCheckListView.as_view(), name="check-list"),
    path("checks/<int:check_id>/", VisibilityCheckDetailView.as_view(), name="check-detail"),
    path("checks/<int:check_id>/status/", VisibilityCheckStatusView.as_view(), name="check-status"),
]
