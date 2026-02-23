from django.urls import path

from .views import (
    GAAuthURLView,
    GACallbackView,
    GADataView,
    GADisconnectView,
    GAPropertiesListView,
    GASelectPropertyView,
    GASyncView,
    IntegrationStatusView,
    ScoreTrafficCorrelationView,
    ShopifyConnectView,
    ShopifyDataView,
    ShopifyDisconnectView,
    ShopifySyncView,
)

app_name = "integrations"

urlpatterns = [
    # OAuth flow
    path(
        "google-analytics/auth-url/",
        GAAuthURLView.as_view(),
        name="ga-auth-url",
    ),
    path(
        "google-analytics/callback/",
        GACallbackView.as_view(),
        name="ga-callback",
    ),
    path(
        "google-analytics/disconnect/",
        GADisconnectView.as_view(),
        name="ga-disconnect",
    ),

    # Property selection
    path(
        "google-analytics/properties/",
        GAPropertiesListView.as_view(),
        name="ga-properties",
    ),
    path(
        "google-analytics/select-property/",
        GASelectPropertyView.as_view(),
        name="ga-select-property",
    ),

    # Data sync
    path(
        "google-analytics/sync/",
        GASyncView.as_view(),
        name="ga-sync",
    ),
    path(
        "google-analytics/data/",
        GADataView.as_view(),
        name="ga-data",
    ),

    # Correlation
    path(
        "score-traffic-correlation/",
        ScoreTrafficCorrelationView.as_view(),
        name="score-traffic-correlation",
    ),

    # Shopify
    path(
        "shopify/connect/",
        ShopifyConnectView.as_view(),
        name="shopify-connect",
    ),
    path(
        "shopify/disconnect/",
        ShopifyDisconnectView.as_view(),
        name="shopify-disconnect",
    ),
    path(
        "shopify/sync/",
        ShopifySyncView.as_view(),
        name="shopify-sync",
    ),
    path(
        "shopify/data/",
        ShopifyDataView.as_view(),
        name="shopify-data",
    ),

    # Status
    path("status/", IntegrationStatusView.as_view(), name="status"),
]
