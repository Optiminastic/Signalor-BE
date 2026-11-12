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
from .views_control import (
    APPROVE,
    REJECT,
    REVOKE,
    ActionDecisionView,
    ActionDetailView,
    ActionEditView,
    ActionResolveView,
    ApprovalListView,
    TaskContactView,
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
    path("programs/<str:org_slug>/approvals/", ApprovalListView.as_view(), name="approvals"),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/approve/",
        ActionDecisionView.as_view(decision=APPROVE),
        name="action-approve",
    ),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/reject/",
        ActionDecisionView.as_view(decision=REJECT),
        name="action-reject",
    ),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/revoke/",
        ActionDecisionView.as_view(decision=REVOKE),
        name="action-revoke",
    ),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/edit/",
        ActionEditView.as_view(),
        name="action-edit",
    ),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/resolve/",
        ActionResolveView.as_view(),
        name="action-resolve",
    ),
    path(
        "programs/<str:org_slug>/actions/<str:public_id>/",
        ActionDetailView.as_view(),
        name="action-detail",
    ),
    path("programs/<str:org_slug>/activity/", ActivityView.as_view(), name="activity"),
    path("programs/<str:org_slug>/tracking/", TrackingView.as_view(), name="tracking"),
    path("programs/<str:org_slug>/plan/", PlanView.as_view(), name="plan"),
    path(
        "programs/<str:org_slug>/tasks/<int:task_id>/contact/",
        TaskContactView.as_view(),
        name="task-contact",
    ),
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
