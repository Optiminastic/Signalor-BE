"""Shopping feed sync and readiness."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    ShopifyProduct,
)
from ._shared import (
    _shopify_integration_for,
)


class ShoppingSyncView(APIView):
    """POST /runs/s/<slug>/shopping/sync/

    Pulls the connected store's product catalog through the Admin API and
    refreshes per-product AI-shopping readiness rows. Interim pull path until
    the Remix app's catalog-sync webhook lands (P1 of the Shopify app plan).
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.shopify import fetch_shopify_products

        from ..shopping import analyze_product

        run = get_object_or_404(AnalysisRun.objects.select_related("organization"), slug=slug)
        integration = _shopify_integration_for(run.organization)
        if integration is None:
            return Response(
                {"detail": "Connect your Shopify store first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        products = fetch_shopify_products(integration)
        seen_ids = []
        for product in products:
            metrics = analyze_product(product)
            product_id = str(product.get("id") or "")
            if not product_id:
                continue
            seen_ids.append(product_id)
            variants = product.get("variants") or []
            ShopifyProduct.objects.update_or_create(
                organization=run.organization,
                product_id=product_id,
                defaults={
                    "handle": str(product.get("handle") or "")[:255],
                    "title": str(product.get("title") or "")[:512],
                    "status": str(product.get("status") or "")[:20],
                    "price": str(variants[0].get("price") if variants else "")[:40],
                    **metrics,
                },
            )
        # A partial fetch (capped pages) must not delete rows we didn't see.
        if len(products) < 1000:
            ShopifyProduct.objects.filter(organization=run.organization).exclude(
                product_id__in=seen_ids
            ).delete()
        return Response({"synced": len(seen_ids)})

class ShoppingReadinessView(APIView):
    """GET /runs/s/<slug>/shopping/

    AI-shopping readiness rollup: average score, issue counts, and the worst
    products first — plus connection state so the page can prompt setup.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from collections import Counter

        from django.shortcuts import get_object_or_404

        from ..shopping import ISSUE_LABELS

        run = get_object_or_404(AnalysisRun.objects.select_related("organization"), slug=slug)
        integration = _shopify_integration_for(run.organization)
        shop_domain = integration.metadata.get("shop_domain", "") if integration else ""

        rows = list(
            ShopifyProduct.objects.filter(organization=run.organization).order_by(
                "readiness", "title"
            )
        )
        issue_counts: Counter = Counter()
        for row in rows:
            issue_counts.update(row.issues or [])

        avg = round(sum(r.readiness for r in rows) / len(rows)) if rows else 0
        return Response(
            {
                "connected": integration is not None,
                "shop_domain": shop_domain,
                "product_count": len(rows),
                "avg_readiness": avg,
                "last_synced": max((r.synced_at for r in rows), default=None),
                "issues": [
                    {"code": code, "label": ISSUE_LABELS.get(code, code), "count": count}
                    for code, count in issue_counts.most_common()
                ],
                "products": [
                    {
                        "product_id": r.product_id,
                        "handle": r.handle,
                        "title": r.title,
                        "status": r.status,
                        "price": r.price,
                        "readiness": r.readiness,
                        "issues": r.issues,
                        "images_total": r.images_total,
                        "images_missing_alt": r.images_missing_alt,
                        "description_chars": r.description_chars,
                    }
                    for r in rows[:100]
                ],
            }
        )
