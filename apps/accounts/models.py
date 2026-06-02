from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

# ── Plan Limits ───────────────────────────────────────────────────────────
PLAN_LIMITS = {
    "starter": {
        "label": "Starter",
        "price_gbp": 19.99,
        "max_projects": 2,
        "max_prompts": 25,
        # Gemini + Google SERP only — no ChatGPT / Perplexity / Claude (Pro+)
        "engines": ["gemini", "google", "bing"],
        "features": [
            "2 projects",
            "Up to 25 prompts",
            "Gemini & Google prompt visibility",
            "GEO analysis & scoring",
            "Recommendations & verify",
            "PDF report exports",
        ],
    },
    "pro": {
        "label": "Pro",
        "price_gbp": 49.99,
        "max_projects": 3,
        "max_prompts": 75,
        # ChatGPT, Perplexity, Gemini, Google, Bing — Claude reserved for Max
        "engines": ["chatgpt", "gemini", "perplexity", "google", "bing"],
        "features": [
            "3 projects",
            "Up to 75 prompts",
            "ChatGPT, Gemini & Perplexity",
            "Everything in Starter",
            "Shopify & WordPress integration",
            "Scheduled re-analysis",
            "Score history & trends",
            "Brand visibility tracking",
        ],
    },
    "business": {
        "label": "Max",
        "price_gbp": 59.99,
        "max_projects": 6,
        "max_prompts": 200,
        "engines": ["chatgpt", "gemini", "perplexity", "claude", "google", "bing"],
        "features": [
            "6 projects",
            "Up to 200 prompts",
            "All AI engines including Claude",
            "Everything in Pro",
            "Priority support",
            "Advanced competitor analysis",
            "Citation trend tracking",
        ],
    },
}


class UserManager(BaseUserManager):
    def create_user(self, username, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(username, email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=30, blank=True)
    last_name = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)
    # B2 object key for a user-uploaded profile photo (e.g.
    # "profile-photos/123_a1b2c3.jpg"). Empty → frontend falls back to the
    # Google OAuth photo from the better-auth session.
    profile_photo_key = models.CharField(max_length=255, blank=True, default="")
    phone_number = models.CharField(max_length=32, blank=True, default="")
    # Dashboard product tour: shown once per user, then suppressed everywhere
    # (set true when the user Skips or finishes the tour). DB-backed so it
    # doesn't re-trigger on a new browser/device/cache-clear.
    has_seen_product_tour = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.username


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        CANCELED = "canceled"
        PAST_DUE = "past_due"
        UNPAID = "unpaid"
        TRIALING = "trialing"

    class Plan(models.TextChoices):
        STARTER = "starter", "Starter"
        PRO = "pro", "Pro"
        BUSINESS = "business", "Max"

    email = models.EmailField(unique=True, db_index=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    payment_customer_id = models.CharField(max_length=255, blank=True, default="")
    payment_subscription_id = models.CharField(max_length=255, blank=True, default="")
    # Latest Dodo payment_id — used to download invoice PDF (webhooks update this)
    last_invoice_payment_id = models.CharField(max_length=255, blank=True, default="")
    # Keep old Stripe fields for backwards compatibility during migration
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    current_period_end = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="usd")
    # Billing-email idempotency. last_billing_emails_payment_id is the
    # payment_id we last fired the success/invoice/welcome trio for, so
    # retried webhooks don't re-spam the customer. welcome_email_sent_at
    # marks the first activation — the welcome template flips copy from
    # "Welcome to Signalor" to "Thanks for renewing" once this is set.
    last_billing_emails_payment_id = models.CharField(max_length=255, blank=True, default="")
    welcome_email_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.email} ({self.status})"

    @property
    def is_active(self):
        return self.status in ("active", "trialing")

    @property
    def limits(self):
        return PLAN_LIMITS.get(self.plan, PLAN_LIMITS["starter"])


class InvoiceRecord(models.Model):
    """One row per successful Dodo payment so we can show full billing
    history without round-tripping the Dodo API on every request. Inserted
    by the webhook handler on subscription.active / subscription.renewed /
    payment.succeeded. Idempotent on payment_id."""

    email = models.EmailField(db_index=True)
    payment_id = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(max_length=50, default="succeeded")
    plan = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} {self.payment_id} {self.amount} {self.currency}"
