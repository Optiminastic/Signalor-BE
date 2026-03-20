from django.urls import path

from .views import RecommendationFromDiscoveryReportView

urlpatterns = [
    path("from-discovery-report/", RecommendationFromDiscoveryReportView.as_view(), name="recommendation-from-discovery-report"),
]
