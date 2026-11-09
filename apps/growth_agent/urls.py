from django.urls import path

from .views import (
    ActivityView,
    BrandAnalysisView,
    BrandOnboardView,
    ConversationChatView,
    ConversationListView,
    GmailCallbackView,
    GmailConnectionView,
    MeView,
    PlanView,
    PositioningReportView,
    ProgramDetailView,
    ProgramListCreateView,
    TaskActionView,
    TaskUpdateView,
    TrackingView,
)

app_name = "growth_agent"

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path("brands/", BrandOnboardView.as_view(), name="brands"),
    path("brands/<str:org_slug>/analysis/", BrandAnalysisView.as_view(), name="brand-analysis"),
    path("programs/", ProgramListCreateView.as_view(), name="programs"),
    path("programs/<str:org_slug>/positioning/", PositioningReportView.as_view(), name="positioning"),
    path("oauth/gmail/callback/", GmailCallbackView.as_view(), name="gmail-callback"),
    path(
        "programs/<str:org_slug>/connections/gmail/", GmailConnectionView.as_view(), name="gmail-connection"
    ),
    path("programs/<str:org_slug>/chats/", ConversationListView.as_view(), name="chats"),
    path(
        "programs/<str:org_slug>/chats/<str:public_id>/",
        ConversationChatView.as_view(),
        name="chat",
    ),
    path("programs/<str:org_slug>/activity/", ActivityView.as_view(), name="activity"),
    path("programs/<str:org_slug>/tracking/", TrackingView.as_view(), name="tracking"),
    path("programs/<str:org_slug>/plan/", PlanView.as_view(), name="plan"),
    path(
        "programs/<str:org_slug>/tasks/<int:task_id>/preview/",
        TaskActionView.as_view(action="preview"),
        name="task-preview",
    ),
    path(
        "programs/<str:org_slug>/tasks/<int:task_id>/run/",
        TaskActionView.as_view(action="run"),
        name="task-run",
    ),
    path("programs/<str:org_slug>/tasks/<int:task_id>/", TaskUpdateView.as_view(), name="task"),
    path("programs/<str:org_slug>/", ProgramDetailView.as_view(), name="program-detail"),
]
