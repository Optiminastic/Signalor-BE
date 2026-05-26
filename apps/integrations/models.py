import os

from cryptography.fernet import Fernet
from django.db import models


def _get_fernet():
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


class Integration(models.Model):
    class Provider(models.TextChoices):
        GOOGLE_ANALYTICS = "google_analytics", "Google Analytics"
        SHOPIFY = "shopify", "Shopify"
        WORDPRESS = "wordpress", "WordPress"
        WOOCOMMERCE = "woocommerce", "WooCommerce"
        WEBFLOW = "webflow", "Webflow"
        NEXTJS = "nextjs", "Next.js"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="integrations",
    )
    provider = models.CharField(max_length=30, choices=Provider.choices)
    is_active = models.BooleanField(default=True)

    # Encrypted provider credentials (tokens/passwords)
    access_token_encrypted = models.TextField(blank=True, default="")
    refresh_token_encrypted = models.TextField(blank=True, default="")
    token_expiry = models.DateTimeField(null=True, blank=True)

    # Provider-specific metadata (e.g. GA4 property_id, Shopify domain, WP site_url)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "provider")]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider"]),
        ]

    def __str__(self):
        return f"{self.organization.name} - {self.get_provider_display()}"

    def set_access_token(self, token: str):
        self.access_token_encrypted = encrypt_token(token)

    def get_access_token(self) -> str:
        if not self.access_token_encrypted:
            return ""
        return decrypt_token(self.access_token_encrypted)

    def set_refresh_token(self, token: str):
        self.refresh_token_encrypted = encrypt_token(token)

    def get_refresh_token(self) -> str:
        if not self.refresh_token_encrypted:
            return ""
        return decrypt_token(self.refresh_token_encrypted)


class GADataSnapshot(models.Model):
    integration = models.ForeignKey(
        Integration,
        on_delete=models.CASCADE,
        related_name="ga_snapshots",
    )
    # Date range this snapshot covers
    date_start = models.DateField()
    date_end = models.DateField()

    # Summary metrics
    sessions = models.IntegerField(default=0)
    organic_sessions = models.IntegerField(default=0)
    bounce_rate = models.FloatField(default=0)
    avg_session_duration = models.FloatField(default=0)  # seconds

    # Detailed data stored as JSON
    top_pages = models.JSONField(default=list, blank=True)
    # [{"path": "/page", "sessions": 100, "bounce_rate": 0.5}, ...]

    traffic_sources = models.JSONField(default=list, blank=True)
    # [{"source": "google", "medium": "organic", "sessions": 50}, ...]

    daily_trend = models.JSONField(default=list, blank=True)
    # [{"date": "2026-01-01", "sessions": 100, "organic_sessions": 60}, ...]

    countries = models.JSONField(default=list, blank=True)
    # [{"country": "India", "country_id": "IN", "sessions": 5000}, ...]

    # Sync metadata
    sync_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("syncing", "Syncing"),
            ("complete", "Complete"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["integration", "-created_at"]),
        ]

    def __str__(self):
        return f"GA Snapshot {self.date_start} - {self.date_end} ({self.sync_status})"


class ShopifyDataSnapshot(models.Model):
    integration = models.ForeignKey(
        Integration,
        on_delete=models.CASCADE,
        related_name="shopify_snapshots",
    )
    date_start = models.DateField()
    date_end = models.DateField()

    # Summary metrics
    total_orders = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    average_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_customers = models.IntegerField(default=0)

    # Detailed data stored as JSON
    top_products = models.JSONField(default=list, blank=True)
    # [{"title": "...", "quantity_sold": 10, "revenue": "99.99", "product_id": "123"}, ...]

    daily_orders = models.JSONField(default=list, blank=True)
    # [{"date": "2026-01-01", "orders": 5, "revenue": "250.00"}, ...]

    # Sync metadata
    sync_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("syncing", "Syncing"),
            ("complete", "Complete"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["integration", "-created_at"]),
        ]

    def __str__(self):
        return f"Shopify Snapshot {self.date_start} - {self.date_end} ({self.sync_status})"


class WordPressDataSnapshot(models.Model):
    integration = models.ForeignKey(
        Integration,
        on_delete=models.CASCADE,
        related_name="wordpress_snapshots",
    )
    date_start = models.DateField()
    date_end = models.DateField()

    total_posts = models.IntegerField(default=0)
    total_pages = models.IntegerField(default=0)
    published_posts_30d = models.IntegerField(default=0)
    updated_posts_30d = models.IntegerField(default=0)

    top_posts = models.JSONField(default=list, blank=True)
    # [{"id": 123, "title": "...", "slug": "...", "url": "...", ...}, ...]

    daily_publishing = models.JSONField(default=list, blank=True)
    # [{"date": "2026-01-01", "published_posts": 3}, ...]

    sync_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("syncing", "Syncing"),
            ("complete", "Complete"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["integration", "-created_at"]),
        ]

    def __str__(self):
        return f"WordPress Snapshot {self.date_start} - {self.date_end} ({self.sync_status})"


class WooCommerceDataSnapshot(models.Model):
    integration = models.ForeignKey(
        Integration,
        on_delete=models.CASCADE,
        related_name="woocommerce_snapshots",
    )
    date_start = models.DateField()
    date_end = models.DateField()

    # Summary metrics
    total_orders = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    average_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_products = models.IntegerField(default=0)
    total_customers = models.IntegerField(default=0)

    # Detailed JSON data
    top_products = models.JSONField(default=list, blank=True)
    # [{"id": 123, "name": "...", "slug": "...", "total_sales": 50, "price": "29.99"}, ...]

    daily_orders = models.JSONField(default=list, blank=True)
    # [{"date": "2026-01-01", "orders": 5, "revenue": "250.00"}, ...]

    sync_status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("syncing", "Syncing"),
            ("complete", "Complete"),
            ("failed", "Failed"),
        ],
        default="pending",
    )
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["integration", "-created_at"]),
        ]

    def __str__(self):
        return f"WooCommerce Snapshot {self.date_start} - {self.date_end} ({self.sync_status})"
