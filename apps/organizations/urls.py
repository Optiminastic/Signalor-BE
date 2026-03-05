from django.urls import path

from .views import (
    CheckOrganizationView,
    OnboardView,
    OrganizationDetailView,
    OrganizationListView,
)

app_name = "organizations"

urlpatterns = [
    path("organizations/onboard/", OnboardView.as_view(), name="onboard"),
    path("organizations/check/", CheckOrganizationView.as_view(), name="check"),
    path("organizations/", OrganizationListView.as_view(), name="org-list"),
    path("organizations/<int:pk>/", OrganizationDetailView.as_view(), name="org-detail"),
]
