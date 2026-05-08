"""
High-level referral flow used by views and webhooks.

Reward model (post-Dodo-SDK reality check):
- Referee side (10% off first payment): handled at checkout via Dodo's
  ``checkout_sessions.create(discount_code=...)`` — see CreateCheckoutSessionView.
  ``link_referee`` just records the relationship.
- Referrer side (tiered discount, refund-on-renewal): we cannot attach
  discounts to an existing Dodo subscription via SDK. Instead, each successful
  referee payment queues a PENDING ReferralReward. On each
  ``subscription.renewed`` webhook for the referrer, we count all queued
  rewards, derive a tier %, and issue ONE partial refund on the just-charged
  renewal via ``refunds.create``. All consumed rewards are marked APPLIED.
  No carryover — any unused PENDING rewards are consumed at the next renewal.

  Tier table (per billing cycle):
      1-4 referrals  →  20% off
      5-9 referrals  →  40% off
      10+ referrals  →  60% off (cap)
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from .models import Referral, ReferralCode, ReferralReward
from .dodo_discounts import create_referee_discount

logger = logging.getLogger(__name__)


def get_or_create_code(email: str) -> ReferralCode:
    """Idempotent: return the user's referral code, creating it on first call."""
    return ReferralCode.for_email(email)


def link_referee(code: str, referee_email: str) -> Optional[Referral]:
    """Called at sign-up when ?ref=CODE is present. Idempotent."""
    code = (code or "").strip().upper()
    if not code or not referee_email:
        return None

    code_row = ReferralCode.objects.filter(code=code).first()
    if not code_row:
        logger.info("referrals: invalid code attempted code=%s", code)
        return None

    if code_row.owner_email.lower() == referee_email.lower():
        logger.info("referrals: self-referral blocked email=%s", referee_email)
        return None

    existing = Referral.objects.filter(referee_email=referee_email).first()
    if existing:
        return existing

    referral = Referral.objects.create(
        referrer_email=code_row.owner_email,
        referee_email=referee_email,
        code_used=code,
        status=Referral.Status.PENDING,
    )

    discount_id = create_referee_discount(referee_email)
    if discount_id:
        referral.referee_discount_id = discount_id
        referral.save(update_fields=["referee_discount_id"])

    logger.info(
        "referrals: linked referrer=%s referee=%s code=%s",
        code_row.owner_email, referee_email, code,
    )
    return referral


def on_referee_first_payment(referee_email: str, **_unused) -> None:
    """Webhook hook: the referee's first paid invoice cleared.

    Marks the Referral as PAID and queues a 20% reward for the referrer.
    Stacking is allowed — each successful referral creates a fresh queued
    reward, consumed one-per-renewal by ``on_referrer_renewal``.
    """
    referral = Referral.objects.filter(referee_email=referee_email).first()
    if not referral or referral.status != Referral.Status.PENDING:
        return

    referral.status = Referral.Status.PAID
    referral.paid_at = timezone.now()
    referral.save(update_fields=["status", "paid_at"])

    ReferralReward.objects.create(
        referral=referral,
        referrer_email=referral.referrer_email,
        percent_off=20,
    )

    logger.info(
        "referrals: queued 20%% reward referrer=%s from referee=%s (queue len=%d)",
        referral.referrer_email,
        referee_email,
        ReferralReward.objects.filter(
            referrer_email=referral.referrer_email,
            status=ReferralReward.Status.PENDING,
        ).count(),
    )


def on_referee_cancelled(referee_email: str) -> None:
    """Webhook hook: the referee cancelled before the renewal that would have
    consumed their reward. Revoke the corresponding PENDING reward (if any).
    Already-APPLIED rewards stay applied — we don't claw back issued refunds.
    """
    referral = Referral.objects.filter(referee_email=referee_email).first()
    if not referral:
        return

    referral.status = Referral.Status.CANCELLED
    referral.save(update_fields=["status"])

    reward = getattr(referral, "reward", None)
    if not reward or reward.status != ReferralReward.Status.PENDING:
        return

    reward.status = ReferralReward.Status.REVOKED
    reward.revoked_at = timezone.now()
    reward.save(update_fields=["status", "revoked_at"])

    logger.info(
        "referrals: revoked queued reward for referrer=%s (referee %s cancelled)",
        referral.referrer_email, referee_email,
    )


MAX_REFUND_ATTEMPTS = 3


def tier_percent_for(count: int) -> int:
    """Map referral-count-this-cycle to discount %.
    1-4 → 20, 5-9 → 40, 10+ → 60 (cap), 0 → 0.
    """
    if count >= 10:
        return 60
    if count >= 5:
        return 40
    if count >= 1:
        return 20
    return 0


def on_referrer_renewal(referrer_email: str, webhook_data: Optional[dict] = None) -> None:
    """Webhook hook: the referrer's renewal invoice was processed.

    Counts queued PENDING ReferralRewards, derives the tier %, and issues a
    single partial refund on the just-charged renewal. All counted rewards are
    marked APPLIED — no carryover. If the refund call fails, the rewards are
    left PENDING for retry on the next renewal.
    """
    pending = list(
        ReferralReward.objects
        .filter(referrer_email=referrer_email, status=ReferralReward.Status.PENDING)
        .order_by("created_at")
    )
    if not pending:
        return

    count = len(pending)
    tier = tier_percent_for(count)
    if tier == 0:
        return

    payment_id, charged_amount, currency = _extract_renewal_charge(webhook_data or {})
    if not payment_id:
        logger.info(
            "referrals: skipping refund for referrer=%s — no payment_id in webhook",
            referrer_email,
        )
        return
    if charged_amount <= 0:
        logger.info(
            "referrals: skipping refund for referrer=%s payment=%s — amount missing/zero",
            referrer_email, payment_id,
        )
        return

    refund_amount = _round_currency_units(charged_amount * Decimal(tier) / Decimal(100))

    refund_id = _create_partial_refund(
        payment_id=payment_id,
        amount=refund_amount,
        currency=currency,
        referrer_email=referrer_email,
        tier_percent=tier,
        reward_count=count,
    )
    if not refund_id:
        # Refund call failed. Bump attempt counters; rewards that exceed the
        # max attempt cap get REVOKED so they don't keep blocking future
        # renewals' tier counts. Surviving rewards stay PENDING for retry.
        now = timezone.now()
        revoked = []
        retried = []
        for r in pending:
            r.refund_attempts = (r.refund_attempts or 0) + 1
            if r.refund_attempts >= MAX_REFUND_ATTEMPTS:
                r.status = ReferralReward.Status.REVOKED
                r.revoked_at = now
                revoked.append(r)
            else:
                retried.append(r)
        ReferralReward.objects.bulk_update(
            pending, ["refund_attempts", "status", "revoked_at"]
        )
        logger.warning(
            "referrals: refund failed referrer=%s payment=%s — %d rewards retrying, %d REVOKED (attempt cap)",
            referrer_email, payment_id, len(retried), len(revoked),
        )
        return

    now = timezone.now()
    for r in pending:
        r.status = ReferralReward.Status.APPLIED
        r.applied_at = now
        r.dodo_discount_id = refund_id  # repurposed: stores Dodo refund ID
    ReferralReward.objects.bulk_update(
        pending, ["status", "applied_at", "dodo_discount_id"]
    )

    logger.info(
        "referrals: applied tier=%s%% (%d referrals) referrer=%s payment=%s refund=%s amount=%s %s",
        tier, count, referrer_email, payment_id, refund_id, refund_amount, currency,
    )


# ── webhook payload helpers ──────────────────────────────────────────────────

def _extract_renewal_charge(data: dict) -> tuple[str, Decimal, str]:
    """Pull (payment_id, amount, currency) from a Dodo subscription.renewed
    payload. Dodo amounts are in **minor units** (paise/cents) — we keep them
    that way so the refund call mirrors what was charged.

    We prefer the post-tax total ("what the user actually paid this cycle")
    over the pre-tax amount, so the refund matches the user's mental model
    of "20% off next payment". Falls back to pre-tax if total is missing.
    """
    payment_id = ""
    for key in ("payment_id", "paymentId"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            payment_id = v.strip()
            break

    amount_raw = (
        data.get("total_amount")
        or data.get("amount")
        or data.get("recurring_pre_tax_amount")
        or 0
    )
    try:
        amount = Decimal(str(amount_raw))
    except Exception:
        amount = Decimal(0)

    currency = (data.get("currency") or "USD").upper()
    return payment_id, amount, currency


def _round_currency_units(amount: Decimal) -> Decimal:
    """Round to integer minor units (paise/cents) — Dodo amounts have no decimals."""
    return amount.quantize(Decimal("1"))


def _create_partial_refund(
    *,
    payment_id: str,
    amount: Decimal,
    currency: str,
    referrer_email: str,
    tier_percent: int,
    reward_count: int,
) -> str:
    """Call Dodo's refunds.create with a single partial-refund item.

    Returns the Dodo refund_id on success, "" on failure (caller leaves the
    rewards PENDING for retry on the next renewal).
    """
    from apps.accounts.dodo_env import dodo_live_mode_enabled, normalized_dodo_api_key
    try:
        from dodopayments import DodoPayments
    except ImportError:
        logger.error("referrals: dodopayments SDK not installed — refund skipped")
        return ""

    key = normalized_dodo_api_key()
    if not key:
        logger.warning("referrals: no Dodo API key — refund skipped")
        return ""

    environment = "live_mode" if dodo_live_mode_enabled() else "test_mode"
    client = DodoPayments(bearer_token=key, environment=environment)

    try:
        refund = client.refunds.create(
            payment_id=payment_id,
            items=[{"amount": int(amount), "item_id": "referral_reward"}],
            reason=f"Referral reward — {reward_count} referral(s) → {tier_percent}% off",
            metadata={
                "kind": "referral_reward",
                "referrer_email": referrer_email,
                "tier_percent": str(tier_percent),
                "reward_count": str(reward_count),
            },
        )
        return getattr(refund, "refund_id", "") or getattr(refund, "id", "") or ""
    except Exception as e:
        logger.warning(
            "referrals: refund failed referrer=%s payment=%s err=%s",
            referrer_email, payment_id, e,
        )
        return ""
