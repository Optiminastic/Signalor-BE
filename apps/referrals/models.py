"""
Referrals app — single-cycle, two-sided referral discounts.

Referrer reward: 20% off **one** billing cycle (applied on referee's first paid invoice).
Referee reward:  10% off the **first** payment (applied at signup checkout).

No stacking — only one ReferralReward per referrer can be `pending` at a time.
No churn protection — if the referee cancels before the referrer reward fires,
it gets revoked.
"""
from __future__ import annotations

import secrets
import string

from django.db import models


def _gen_code(length: int = 8) -> str:
    """Generate a URL-safe, unambiguous referral code (no 0/O/I/l)."""
    alphabet = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0OI1L")
    return "".join(secrets.choice(alphabet) for _ in range(length))


class ReferralCode(models.Model):
    """One per user (keyed by email — Subscription is keyed by email too).

    The user shares `https://signalor.ai/?ref=<code>`. New sign-ups arriving with
    that param create a `Referral` row linking referee → this code's owner.
    """

    owner_email = models.EmailField(unique=True, db_index=True)
    code = models.CharField(max_length=12, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referrals_referralcode"

    def __str__(self) -> str:
        return f"{self.code} → {self.owner_email}"

    @classmethod
    def for_email(cls, email: str) -> "ReferralCode":
        """Get-or-create a code for `email`, retrying on rare code collisions."""
        existing = cls.objects.filter(owner_email=email).first()
        if existing:
            return existing
        for _ in range(5):
            code = _gen_code()
            if not cls.objects.filter(code=code).exists():
                return cls.objects.create(owner_email=email, code=code)
        # Extremely unlikely after 5 tries — fall back to a longer code.
        return cls.objects.create(owner_email=email, code=_gen_code(12))


class Referral(models.Model):
    """One per referrer→referee relationship. Linked at sign-up."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending — awaiting first payment"
        PAID = "paid", "Paid — discounts triggered"
        CANCELLED = "cancelled", "Referee cancelled before discount fired"

    referrer_email = models.EmailField(db_index=True)
    referee_email = models.EmailField(unique=True, db_index=True)
    code_used = models.CharField(max_length=12)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)

    # Dodo discount IDs — set once the discount is created via the Dodo API.
    referee_discount_id = models.CharField(max_length=255, blank=True, default="")
    referrer_discount_id = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "referrals_referral"
        indexes = [
            models.Index(fields=["referrer_email", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.referrer_email} → {self.referee_email} ({self.status})"


class ReferralReward(models.Model):
    """A pending or applied 20%-off-one-cycle credit on the referrer's next invoice.

    Created when the referee's first payment succeeds. Marked `applied` when we
    confirm the referrer's next invoice was discounted, or `revoked` if the
    referee cancels before the referrer's next billing cycle.

    Only one `pending` reward per referrer at a time (no stacking).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending — discount staged for next renewal"
        APPLIED = "applied", "Applied — referrer's invoice was discounted"
        REVOKED = "revoked", "Revoked — referee cancelled before discount fired"

    referral = models.OneToOneField(
        Referral, on_delete=models.CASCADE, related_name="reward"
    )
    referrer_email = models.EmailField(db_index=True)
    percent_off = models.PositiveSmallIntegerField(default=20)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    dodo_discount_id = models.CharField(max_length=255, blank=True, default="")

    refund_attempts = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of failed refund calls — REVOKED after MAX_REFUND_ATTEMPTS.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "referrals_referralreward"

    def __str__(self) -> str:
        return f"{self.referrer_email} {self.percent_off}% off ({self.status})"
