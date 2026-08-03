"""Commerce catalog sync."""

from django.db import models


class ShopifyProduct(models.Model):
    """AI-shopping readiness snapshot of one store product, refreshed by the
    shopping sync endpoint from the connected Shopify integration."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="shopify_products"
    )
    product_id = models.CharField(max_length=40)
    handle = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(max_length=20, blank=True, default="")
    price = models.CharField(max_length=40, blank=True, default="")
    description_chars = models.IntegerField(default=0)
    images_total = models.IntegerField(default=0)
    images_missing_alt = models.IntegerField(default=0)
    readiness = models.IntegerField(default=0)
    issues = models.JSONField(default=list)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "product_id"], name="uniq_org_shopify_product"
            )
        ]
        indexes = [models.Index(fields=["organization", "readiness"])]

    def __str__(self):
        return f"{self.title} ({self.readiness}/100)"

