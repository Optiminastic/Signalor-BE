"""
Partners app — affiliate / creator program.

Distinct from `apps.referrals`:
- Referrals are user-to-user (paying users invite friends, both get a discount).
- Partners are creators we recruit; they get a cash commission, their audience
  gets the same 10% off discount as referees (same Dodo discount ID is reused).

Attribution model: 30-day **last-click**. A click on an affiliate link locks
attribution to that partner for 30 days; subsequent clicks on a different
partner's link (or a referral link) override it.

Commission model: one-time on the referee's first paid invoice. Paid out
manually (admin marks commissions paid and records a Payout row).
"""
from __future__ import annotations

import secrets
import string
from datetime import timedelta

from django.db import models
from django.utils import timezone


def _gen_partner_code(length: int = 8) -> str:
    """URL-safe unambiguous code (no 0/O/I/1/L) — same alphabet as referrals."""
    alphabet = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0OI1L")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _default_attribution_expiry():
    """30-day last-click window."""
    return timezone.now() + timedelta(days=30)


class Partner(models.Model):
    """A creator we've recruited. Manually created in Django admin."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused — links still work but no new commissions"
        TERMINATED = "terminated", "Terminated — links disabled"

    class PayoutMethod(models.TextChoices):
        WISE = "wise", "Wise"
        PAYPAL = "paypal", "PayPal"
        BANK = "bank", "Bank transfer"
        CRYPTO = "crypto", "Crypto wallet"
        OTHER = "other", "Other"

    email = models.EmailField(unique=True, db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    code = models.CharField(max_length=32, unique=True, db_index=True)

    commission_percent = models.PositiveSmallIntegerField(
        default=20,
        help_text="% of post-discount revenue paid to the partner on first payment.",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    payout_method = models.CharField(
        max_length=16, choices=PayoutMethod.choices, default=PayoutMethod.WISE
    )
    payout_details = models.TextField(
        blank=True,
        default="",
        help_text="Free-text payout details (account number, PayPal email, IBAN, etc.).",
    )
    notes = models.TextField(blank=True, default="")

    # Application profile (collected from the public /creators-program form).
    country = models.CharField(
        max_length=2, blank=True, default="",
        help_text="ISO 3166-1 alpha-2 code (e.g. 'US', 'IN', 'DE').",
    )
    social_platforms = models.JSONField(
        default=list, blank=True,
        help_text='List of {"platform": "...", "handle": "..."} entries.',
    )
    audience_size = models.CharField(
        max_length=32, blank=True, default="",
        help_text="Self-reported audience bucket: '<1k', '1k-10k', '10k-100k', '100k-1m', '1m+'.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_partner"

    def __str__(self) -> str:
        return f"{self.code} ({self.email})"

    @classmethod
    def generate_unique_code(cls) -> str:
        for _ in range(5):
            code = _gen_partner_code()
            if not cls.objects.filter(code=code).exists():
                return code
        return _gen_partner_code(12)


class PartnerAttribution(models.Model):
    """Last-click attribution: an email is currently attributed to one partner.

    Created at sign-up when the frontend reports the affiliate localStorage code.
    Overwritten on every new attribute call (last-click). Expires 30 days after
    the last touch — after that, the row is treated as inactive but kept for
    audit.
    """

    email = models.EmailField(unique=True, db_index=True)
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="attributions"
    )
    attributed_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(default=_default_attribution_expiry, db_index=True)
    landing_path = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "partners_partnerattribution"

    def __str__(self) -> str:
        return f"{self.email} → {self.partner.code}"

    @property
    def is_active(self) -> bool:
        return self.expires_at > timezone.now()


class PartnerCommission(models.Model):
    """Earnings record. One row per referee payment we credit a partner for.

    Commission amount is calculated at creation time from
    ``post_discount_amount * partner.commission_percent / 100`` and frozen — we
    do not recompute later, so changing a partner's commission_percent only
    affects future payments.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending — owed to partner"
        PAID = "paid", "Paid — payout recorded"
        CANCELLED = "cancelled", "Cancelled — refund/chargeback or fraud"

    partner = models.ForeignKey(
        Partner, on_delete=models.PROTECT, related_name="commissions"
    )
    attribution = models.ForeignKey(
        PartnerAttribution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commissions",
    )
    referee_email = models.EmailField(db_index=True)
    payment_id = models.CharField(
        max_length=255, db_index=True,
        help_text="Dodo payment_id for this transaction.",
    )

    gross_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Pre-discount price.",
    )
    post_discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="What Dodo actually charged the customer (commission base).",
    )
    commission_percent_snapshot = models.PositiveSmallIntegerField(
        help_text="Partner's commission_percent at the time this row was created.",
    )
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    payout = models.ForeignKey(
        "PartnerPayout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commissions",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "partners_partnercommission"
        constraints = [
            models.UniqueConstraint(
                fields=["payment_id"],
                name="partners_unique_commission_per_payment",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner.code} {self.commission_amount} {self.currency} ({self.status})"


class PartnerPayout(models.Model):
    """Manual payout record. Admin creates this when they actually send money.

    Linking commissions to a payout flips their status to ``paid``.
    """

    partner = models.ForeignKey(
        Partner, on_delete=models.PROTECT, related_name="payouts"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="USD")
    method = models.CharField(
        max_length=16, choices=Partner.PayoutMethod.choices,
        default=Partner.PayoutMethod.WISE,
    )
    reference = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Transaction id / Wise transfer id / bank reference.",
    )
    notes = models.TextField(blank=True, default="")

    paid_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "partners_partnerpayout"
        ordering = ["-paid_at"]

    def __str__(self) -> str:
        return f"{self.partner.code} {self.amount} {self.currency} on {self.paid_at:%Y-%m-%d}"
