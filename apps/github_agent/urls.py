from django.urls import path

from .views import (
    GithubCallbackView,
    GithubDisconnectView,
    GithubFixView,
    GithubInstallURLView,
    GithubJobsView,
    GithubOrgInstallURLView,
    GithubOrgStatusView,
    GithubStatusView,
    GithubWebhookView,
)

app_name = "github_agent"

urlpatterns = [
    # Global (App install callback + webhook)
    path("callback/", GithubCallbackView.as_view(), name="callback"),
    path("webhook/", GithubWebhookView.as_view(), name="webhook"),
    # Org-scoped (onboarding — no run slug yet)
    path("install-url/", GithubOrgInstallURLView.as_view(), name="org-install-url"),
    path("status/", GithubOrgStatusView.as_view(), name="org-status"),
    # Run-scoped (resolved by AnalysisRun slug)
    path("runs/s/<str:slug>/install-url/", GithubInstallURLView.as_view(), name="install-url"),
    path("runs/s/<str:slug>/status/", GithubStatusView.as_view(), name="status"),
    path("runs/s/<str:slug>/fix/", GithubFixView.as_view(), name="fix"),
    path("runs/s/<str:slug>/jobs/", GithubJobsView.as_view(), name="jobs"),
    path("runs/s/<str:slug>/jobs/<int:job_id>/", GithubJobsView.as_view(), name="job-detail"),
    path("runs/s/<str:slug>/disconnect/", GithubDisconnectView.as_view(), name="disconnect"),
]
