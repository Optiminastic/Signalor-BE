from django.urls import path

from .dashboard_views import ApiKeyListCreateView, ApiKeyRevokeView

app_name = "public_api_dashboard"

urlpatterns = [
    path("", ApiKeyListCreateView.as_view(), name="api-keys-list-create"),
    path("<int:pk>/", ApiKeyRevokeView.as_view(), name="api-keys-revoke"),
]
