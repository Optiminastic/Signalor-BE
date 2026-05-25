from django.urls import path

from .dashboard_views import WebhookDeleteView, WebhookListCreateView

app_name = "public_api_webhooks"

urlpatterns = [
    path("", WebhookListCreateView.as_view(), name="webhooks-list-create"),
    path("<int:pk>/", WebhookDeleteView.as_view(), name="webhooks-delete"),
]
