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
    ShopifyAppUninstalledWebhookView,
    ShopifyBillingUpdateView,
    ShopifyAuthURLView,
    ShopifyCallbackView,
    ShopifyConnectView,
    ShopifyDataView,
    ShopifyDisconnectView,
    ShopifyLinkAppView,
    ShopifySyncView,
    WooCommerceConnectView,
    WooCommerceDataView,
    WooCommerceDisconnectView,
    WooCommerceSyncView,
    WordPressConnectView,
    WordPressCallbackView,
    WordPressDataView,
    WordPressDisconnectView,
    WordPressSyncView,
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
        "shopify/auth-url/",
        ShopifyAuthURLView.as_view(),
        name="shopify-auth-url",
    ),
    path(
        "shopify/callback/",
        ShopifyCallbackView.as_view(),
        name="shopify-callback",
    ),
    path(
        "shopify/webhooks/app-uninstalled/",
        ShopifyAppUninstalledWebhookView.as_view(),
        name="shopify-app-uninstalled-webhook",
    ),
    path(
        "shopify/billing-update/",
        ShopifyBillingUpdateView.as_view(),
        name="shopify-billing-update",
    ),
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
    path(
        "shopify/link-app/",
        ShopifyLinkAppView.as_view(),
        name="shopify-link-app",
    ),
    path(
        "wordpress/connect/",
        WordPressConnectView.as_view(),
        name="wordpress-connect",
    ),
    path(
        "wordpress/callback/",
        WordPressCallbackView.as_view(),
        name="wordpress-callback",
    ),
    path(
        "wordpress/disconnect/",
        WordPressDisconnectView.as_view(),
        name="wordpress-disconnect",
    ),
    path(
        "wordpress/sync/",
        WordPressSyncView.as_view(),
        name="wordpress-sync",
    ),
    path(
        "wordpress/data/",
        WordPressDataView.as_view(),
        name="wordpress-data",
    ),

    # WooCommerce
    path("woocommerce/connect/",    WooCommerceConnectView.as_view(),    name="woocommerce-connect"),
    path("woocommerce/disconnect/", WooCommerceDisconnectView.as_view(), name="woocommerce-disconnect"),
    path("woocommerce/sync/",       WooCommerceSyncView.as_view(),       name="woocommerce-sync"),
    path("woocommerce/data/",       WooCommerceDataView.as_view(),       name="woocommerce-data"),

    # Status
    path("status/", IntegrationStatusView.as_view(), name="status"),
]
