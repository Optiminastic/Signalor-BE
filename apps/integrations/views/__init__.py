"""Views, split by resource.

Was a single 2,723-line module. Everything is re-exported here so
urls.py and existing imports keep working - the split is a pure move.
"""

from ._shared import (  # noqa: F401
    _ALLOWED_RANGE_DAYS,
    _SHOPIFY_OAUTH_CALLBACK_PATH,
    GA_SCOPES,
    GSC_SCOPES,
    _append_query_params,
    _build_credentials,
    _deactivate_other_store_integration,
    _get_org_or_400,
    _gsc_redirect,
    _org_id_param,
    _redirect_with_status,
    _refresh_if_needed,
    _requested_days,
    _resolve_org,
    _resolve_shopify_redirect_uri,
    _sign_state,
    _verify_state,
    logger,
)
from .ga4 import (  # noqa: F401
    GAAuthURLView,
    GACallbackView,
    GADataView,
    GADisconnectView,
    GAPropertiesListView,
    GASelectPropertyView,
    GASyncView,
    ScoreTrafficCorrelationView,
)
from .gsc import (  # noqa: F401
    GSCAuthURLView,
    GSCCallbackView,
    GSCCoverageView,
    GSCDataView,
    GSCDisconnectView,
    GSCSelectSiteView,
    GSCSitemapsView,
    GSCSitesListView,
    GSCSyncView,
    GSCUrlInspectView,
)
from .live import (  # noqa: F401
    LiveVisitorsView,
)
from .shopify import (  # noqa: F401
    ShopifyAppUninstalledWebhookView,
    ShopifyAuthURLView,
    ShopifyBillingUpdateView,
    ShopifyCallbackView,
    ShopifyConnectView,
    ShopifyDataView,
    ShopifyDisconnectView,
    ShopifyLinkAppView,
    ShopifySyncView,
)
from .status import (  # noqa: F401
    IntegrationStatusView,
)
from .woocommerce import (  # noqa: F401
    WooCommerceConnectView,
    WooCommerceDataView,
    WooCommerceDisconnectView,
    WooCommerceSyncView,
)
from .wordpress import (  # noqa: F401
    WordPressCallbackView,
    WordPressConnectView,
    WordPressDataView,
    WordPressDisconnectView,
    WordPressSyncView,
)

__all__ = [
    "GAAuthURLView",
    "GACallbackView",
    "GADataView",
    "GADisconnectView",
    "GAPropertiesListView",
    "GASelectPropertyView",
    "GASyncView",
    "GSCAuthURLView",
    "GSCCallbackView",
    "GSCCoverageView",
    "GSCDataView",
    "GSCDisconnectView",
    "GSCSelectSiteView",
    "GSCSitemapsView",
    "GSCSitesListView",
    "GSCSyncView",
    "GSCUrlInspectView",
    "IntegrationStatusView",
    "ScoreTrafficCorrelationView",
    "ShopifyAppUninstalledWebhookView",
    "ShopifyAuthURLView",
    "ShopifyBillingUpdateView",
    "ShopifyCallbackView",
    "ShopifyConnectView",
    "ShopifyDataView",
    "ShopifyDisconnectView",
    "ShopifyLinkAppView",
    "ShopifySyncView",
    "WooCommerceConnectView",
    "WooCommerceDataView",
    "WooCommerceDisconnectView",
    "WooCommerceSyncView",
    "WordPressCallbackView",
    "WordPressConnectView",
    "WordPressDataView",
    "WordPressDisconnectView",
    "WordPressSyncView",
]

from .slack import (  # noqa: F401
    SlackAuthURLView,
    SlackCallbackView,
    SlackChannelsView,
    SlackDisconnectView,
    SlackSelectChannelView,
)
