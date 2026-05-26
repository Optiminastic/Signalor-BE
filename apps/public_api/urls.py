from django.urls import include, path

from .views import (
    CreateAnalysisView,
    GetAnalysisRecommendationsView,
    GetAnalysisView,
    UsageView,
)

app_name = "public_api"

urlpatterns = [
    path("analyses/", CreateAnalysisView.as_view(), name="analyses-create"),
    path("analyses/<str:slug>/", GetAnalysisView.as_view(), name="analyses-get"),
    path(
        "analyses/<str:slug>/recommendations/",
        GetAnalysisRecommendationsView.as_view(),
        name="analyses-recommendations",
    ),
    path("usage/", UsageView.as_view(), name="usage"),
    path("nextjs/", include("apps.public_api.nextjs.urls")),
]
