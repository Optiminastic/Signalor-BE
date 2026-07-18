"""Mandatory Shopify GDPR compliance webhooks.

Required for any app with Custom/unlisted or Public distribution (not just a
single-store custom app). Shopify sends these directly, HMAC-signed with the
app's client secret — the same verification as the app/uninstalled webhook.

Signalor stores no per-customer PII (only aggregate order counts and product
readiness), so the two customer webhooks have nothing to compile or delete and
simply acknowledge; shop/redact purges everything held for the shop.
"""

import logging
import os

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger("apps")


def _verify_shop(request):
    """(shop_domain, None) when the Shopify HMAC verifies, else (None, Response)."""
    from .services.shopify import normalize_shop_domain, verify_shopify_webhook_hmac

    secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
    shop_header = request.headers.get("X-Shopify-Shop-Domain", "")
    if not secret or not hmac_header or not shop_header:
        return None, Response(status=status.HTTP_400_BAD_REQUEST)
    if not verify_shopify_webhook_hmac(request.body, hmac_header, secret):
        return None, Response(status=status.HTTP_401_UNAUTHORIZED)
    return normalize_shop_domain(shop_header), None


def _purge_shop_data(shop_domain: str) -> None:
    """Delete everything Signalor derived from this shop (shop/redact)."""
    from apps.analyzer.models import ShopifyProduct

    from .models import Integration

    integration = Integration.objects.filter(
        provider=Integration.Provider.SHOPIFY, metadata__shop_domain=shop_domain
    ).first()
    if not integration:
        return
    ShopifyProduct.objects.filter(organization=integration.organization).delete()
    integration.shopify_snapshots.all().delete()
    integration.delete()


class ShopifyCustomerDataRequestWebhookView(APIView):
    """POST /api/integrations/shopify/webhooks/customers-data-request/

    Merchant asked for a customer's stored data. Signalor holds no per-customer
    PII, so there is nothing to compile — acknowledge per Shopify's contract."""

    permission_classes = [AllowAny]

    def post(self, request):
        shop, err = _verify_shop(request)
        if err:
            return err
        logger.info("Shopify customers/data_request for %s (no PII stored)", shop)
        return Response(status=status.HTTP_200_OK)


class ShopifyCustomerRedactWebhookView(APIView):
    """POST /api/integrations/shopify/webhooks/customers-redact/

    Delete a customer's data. Signalor stores no per-customer rows, so there is
    nothing to delete — acknowledge."""

    permission_classes = [AllowAny]

    def post(self, request):
        shop, err = _verify_shop(request)
        if err:
            return err
        logger.info("Shopify customers/redact for %s (no per-customer data)", shop)
        return Response(status=status.HTTP_200_OK)


class ShopifyShopRedactWebhookView(APIView):
    """POST /api/integrations/shopify/webhooks/shop-redact/

    Sent ~48h after uninstall. Purge all data Signalor holds for the shop."""

    permission_classes = [AllowAny]

    def post(self, request):
        shop, err = _verify_shop(request)
        if err:
            return err
        _purge_shop_data(shop)
        logger.info("Shopify shop/redact processed for %s", shop)
        return Response(status=status.HTTP_200_OK)
