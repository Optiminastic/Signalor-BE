"""
High-level partner-program orchestration.

Three jobs:
- ``set_attribution`` — called from the sign-up flow when the frontend reports
  the affiliate code stashed in localStorage. Last-click wins (we always
  overwrite the existing row and reset the 30-day window).
- ``get_active_attribution`` — used by the checkout view and the payment
  webhook to look up the partner an email is currently attributed to.
- ``record_commission`` — called from the payment webhook on a referee's first
  paid invoice. Idempotent on ``payment_id`` (a uniqueness constraint guards
  against double-firing).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from .models import Partner, PartnerAttribution, PartnerCommission

logger = logging.getLogger(__name__)


def set_attribution(
    email: str,
    code: str,
    landing_path: str = "",
) -> Optional[PartnerAttribution]:
    """Lock in last-click attribution for ``email`` to the partner with ``code``.

    Returns the attribution row, or None if the code is invalid / partner is
    inactive. The 30-day window resets on every call (last-click semantics).
    """
    email = (email or "").strip().lower()
    code = (code or "").strip().upper()
    if not email or not code:
        return None

    partner = Partner.objects.filter(code=code).first()
    if not partner:
        logger.info("partners: invalid affiliate code attempted code=%s email=%s", code, email)
        return None
    if partner.status == Partner.Status.TERMINATED:
        logger.info("partners: terminated partner code=%s ignored", code)
        return None
    if partner.email and partner.email.lower() == email:
        # Self-referral: the creator is trying to use their own code. Block
        # silently so the discount doesn't apply and no commission is minted
        # later when their payment lands.
        logger.info(
            "partners: self-referral blocked partner=%s email=%s",
            partner.code, email,
        )
        return None

    attribution, created = PartnerAttribution.objects.update_or_create(
        email=email,
        defaults={
            "partner": partner,
            "expires_at": timezone.now() + timedelta(days=30),
            "landing_path": (landing_path or "")[:500],
        },
    )
    logger.info(
        "partners: attribution %s email=%s partner=%s",
        "created" if created else "updated", email, partner.code,
    )
    return attribution


def get_active_attribution(email: str) -> Optional[PartnerAttribution]:
    """Return the active attribution row for ``email`` or None.

    "Active" = exists, not expired, partner is not terminated. Paused partners
    still keep their existing attribution (so a visitor in-flight gets the
    discount), but the webhook will skip new commission rows for them.
    """
    email = (email or "").strip().lower()
    if not email:
        return None
    attribution = (
        PartnerAttribution.objects
        .select_related("partner")
        .filter(email=email, expires_at__gt=timezone.now())
        .first()
    )
    if not attribution:
        return None
    if attribution.partner.status == Partner.Status.TERMINATED:
        return None
    return attribution


def cancel_commission_for_refund(payment_id: str) -> Optional[PartnerCommission]:
    """Mark a PENDING commission as CANCELLED when Dodo refunds its payment.

    A PAID commission is never reversed — once you've actually wired the money
    to the creator you eat the cost of the refund. Cancelling a PENDING row is
    safe because nothing has been paid out yet.
    """
    payment_id = (payment_id or "").strip()
    if not payment_id:
        return None
    commission = PartnerCommission.objects.filter(payment_id=payment_id).first()
    if not commission:
        return None
    if commission.status == PartnerCommission.Status.PAID:
        logger.warning(
            "partners: refund landed on already-paid commission payment=%s partner=%s "
            "(creator was already paid out — eat the loss)",
            payment_id, commission.partner.code,
        )
        return commission
    if commission.status == PartnerCommission.Status.CANCELLED:
        return commission
    commission.status = PartnerCommission.Status.CANCELLED
    commission.save(update_fields=["status", "updated_at"])
    logger.info(
        "partners: commission cancelled by refund payment=%s partner=%s amount=%s",
        payment_id, commission.partner.code, commission.commission_amount,
    )
    return commission


def record_commission(
    *,
    referee_email: str,
    payment_id: str,
    gross_amount: Decimal,
    post_discount_amount: Decimal,
    currency: str = "USD",
) -> Optional[PartnerCommission]:
    """Create a PENDING commission row if ``referee_email`` is attributed.

    Idempotent on ``payment_id`` thanks to the model's UniqueConstraint.
    Returns the created (or pre-existing) commission row, or None if there's
    no active attribution.
    """
    attribution = get_active_attribution(referee_email)
    if not attribution:
        return None

    partner = attribution.partner
    if partner.status != Partner.Status.ACTIVE:
        logger.info(
            "partners: skipping commission — partner %s is %s",
            partner.code, partner.status,
        )
        return None

    commission_amount = (
        Decimal(post_discount_amount) * Decimal(partner.commission_percent) / Decimal(100)
    ).quantize(Decimal("0.01"))

    commission, created = PartnerCommission.objects.get_or_create(
        payment_id=payment_id,
        defaults={
            "partner": partner,
            "attribution": attribution,
            "referee_email": referee_email.strip().lower(),
            "gross_amount": Decimal(gross_amount),
            "post_discount_amount": Decimal(post_discount_amount),
            "commission_percent_snapshot": partner.commission_percent,
            "commission_amount": commission_amount,
            "currency": currency or "USD",
        },
    )
    if created:
        logger.info(
            "partners: commission created partner=%s referee=%s amount=%s %s payment=%s",
            partner.code, referee_email, commission_amount, currency, payment_id,
        )
    else:
        logger.info(
            "partners: commission already exists for payment=%s — skipped",
            payment_id,
        )
    return commission
