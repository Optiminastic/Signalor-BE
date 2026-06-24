from django.urls import include, path

from .views import (
    CreateAnalysisView,
    GetAnalysisRecommendationsView,
    GetAnalysisView,
    PublicSitePostDetailView,
    PublicSitePostsView,
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
    # Satellite blog network (consumed by the external blog sites, no DB).
    path("sites/<str:site>/posts/", PublicSitePostsView.as_view(), name="site-posts"),
    path(
        "sites/<str:site>/posts/<str:slug>/",
        PublicSitePostDetailView.as_view(),
        name="site-post-detail",
    ),
    path("nextjs/", include("apps.public_api.nextjs.urls")),
]
