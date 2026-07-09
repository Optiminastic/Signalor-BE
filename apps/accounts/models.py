from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

# ── Plan Limits ───────────────────────────────────────────────────────────
# Public Individual plans reuse the legacy keys so live Subscription rows,
# Dodo webhook metadata, the checkout product_map, and frontend literals keep
# working unchanged — only the meaning/labels/limits/price change here:
#   starter  -> Self-Serve Brand  (£69.99, 1 brand, 10 prompts)
#   pro      -> Managed Growth Brand (£99.69, 1 brand, 25 prompts, human support)
# "business" (legacy Max) is RETAINED for grandfathered subscribers + internal
# emails, but is no longer sold publicly (removed from pricing UI). Enterprise
# is handled by the Contact Sales form and never gets a plan key / Dodo checkout.
# Both Individual plans get the full engine set — prompts + human support are the
# differentiators, not engine access.
_ALL_ENGINES = ["chatgpt", "gemini", "perplexity", "claude", "google", "bing"]

# Interim project cap for Agency accounts — effectively "unlimited" until
# per-brand Dodo billing lands (each added client brand becomes a paid line
# item, and this constant is replaced by a count of active per-brand
# subscriptions). See AccountProfile + subscription_utils.effective_max_projects.
AGENCY_MAX_PROJECTS = 1000

PLAN_LIMITS = {
    "starter": {
        "label": "Self-Serve Brand",
        "price_gbp": 69.99,
        "max_projects": 1,
        "max_prompts": 10,
        "engines": _ALL_ENGINES,
        "features": [
            "1 brand / domain",
            "10 prompts to rank & track",
            "AI visibility score",
            "Prompt ranking across AI engines",
            "Competitor visibility tracking",
            "Recommendations & improvement guidance",
        ],
    },
    "pro": {
        "label": "Managed Growth Brand",
        "price_gbp": 99.69,
        "max_projects": 1,
        "max_prompts": 25,
        "engines": _ALL_ENGINES,
        "features": [
            "1 brand / domain",
            "25 prompts to rank & track",
            "Everything in Self-Serve",
            "Daily agency-style support from our team",
            "Guidance on recommendations, fixes & actions",
        ],
    },
    "business": {
        # Legacy "Max" — grandfathered subscribers + internal emails only.
        # Not shown on the public pricing page.
        "label": "Max",
        "price_gbp": 59.99,
        "max_projects": 6,
        "max_prompts": 200,
        "engines": _ALL_ENGINES,
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


class AccountProfile(models.Model):
    """Email-keyed account identity that exists BEFORE any payment.

    Deliberately decoupled from ``Subscription`` (billing) and the Django
    ``User`` (auth): the Individual-vs-Agency choice is made during sign-up,
    before a Subscription or a fully-populated User row necessarily exists
    (``ProfileView`` already handles missing User rows). Absence of a row is
    treated as ``individual`` everywhere — see
    ``subscription_utils.get_account_type``.

    Account type only affects the project cap (see ``effective_max_projects``);
    it is orthogonal to ``Subscription.plan`` (starter/pro/business), which
    stays the stable Dodo contract.
    """

    class AccountType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual / Brand"
        AGENCY = "agency", "Agency"

    email = models.EmailField(unique=True, db_index=True)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.INDIVIDUAL,
    )
    # The agency's own name, captured on the dedicated agency sign-up step.
    # Distinct from the brand/project name entered during onboarding. Blank for
    # individuals (and agencies created before this field existed).
    agency_name = models.CharField(max_length=255, blank=True, default="")
    # The person's position at the agency (e.g. "Founder / CEO", "Marketing /
    # Growth"), captured on the same sign-up step. Free-form label from a fixed
    # dropdown; blank for individuals.
    role = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_account_profile"

    def __str__(self):
        return f"{self.email} ({self.account_type})"


class AgencyMembership(models.Model):
    """A person on an agency's team, keyed by email (consistent with the rest of
    the email-based identity model).

    The agency is identified by the owner's email (an ``AccountProfile`` with
    ``account_type=agency``); that owner is the implicit Admin and does NOT need a
    membership row. Invited teammates get one row each, with an access ``role``
    (admin/member) distinct from ``AccountProfile.role`` (a cosmetic job title).
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"

    agency_email = models.EmailField(db_index=True)
    member_email = models.EmailField(db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.EmailField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_agency_membership"
        unique_together = ("agency_email", "member_email")

    def __str__(self):
        return f"{self.member_email} @ {self.agency_email} ({self.role})"


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        CANCELED = "canceled"
        PAST_DUE = "past_due"
        UNPAID = "unpaid"
        TRIALING = "trialing"

    class Plan(models.TextChoices):
        # Value strings are stable contracts (live rows, Dodo metadata, product_map);
        # only the human labels track the new packaging.
        STARTER = "starter", "Self-Serve Brand"
        PRO = "pro", "Managed Growth Brand"
        BUSINESS = "business", "Max"

    email = models.EmailField(unique=True, db_index=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.STARTER)
    payment_customer_id = models.CharField(max_length=255, blank=True, default="")
    # Looked up on every Dodo billing webhook via _find_subscription() before
    # falling back to email. Indexed so that hot path isn't a full table scan.
    payment_subscription_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # Latest Dodo payment_id — used to download invoice PDF (webhooks update this)
    last_invoice_payment_id = models.CharField(max_length=255, blank=True, default="")
    # Keep old Stripe fields for backwards compatibility during migration
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    deactivated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    current_period_end = models.DateTimeField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="gbp")
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
    currency = models.CharField(max_length=3, default="gbp")
    status = models.CharField(max_length=50, default="succeeded")
    plan = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} {self.payment_id} {self.amount} {self.currency}"


class EnterpriseLead(models.Model):
    """A 'Contact Sales' submission from the Enterprise pricing card.

    Captured both in this table (so nothing is lost / leads are queryable) and
    emailed to the sales inbox by the create view. All fields are client-supplied
    claims — treat as untrusted; the server stamps created_at and status.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    class SupportLevel(models.TextChoices):
        SELF_SERVE = "self_serve", "Self-serve"
        MANAGED = "managed", "Managed / agency-style"
        DEDICATED = "dedicated", "Dedicated team"

    brand_name = models.CharField(max_length=255)
    website = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    prompts_required = models.PositiveIntegerField(null=True, blank=True)
    brands_count = models.PositiveIntegerField(null=True, blank=True)
    current_investment = models.TextField(blank=True, default="")
    support_level = models.CharField(max_length=20, blank=True, default="")
    preferred_currency = models.CharField(max_length=8, blank=True, default="")
    team_size = models.CharField(max_length=64, blank=True, default="")
    # AI engines the prospect wants tracked (e.g. ["chatgpt", "gemini"]).
    ai_engines = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.brand_name} <{self.email}> ({self.status})"
