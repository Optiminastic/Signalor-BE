"""
Dodo Payments discount integration for the referee side of referrals.

Only the referee's 10%-off discount is exposed via Dodo's discount system —
applied at checkout time via ``checkout_sessions.create(discount_code=...)``.

The referrer side is handled by the refund-on-renewal flow in ``services.py``
(the Dodo SDK does not expose a way to attach a discount to an existing
subscription, so we issue partial refunds instead).

Env vars expected:
- ``DODO_REFEREE_DISCOUNT_ID``    — internal dsc_... ID, stamped on Referral
                                    rows for audit. Not used at API call time.
- ``DODO_REFEREE_DISCOUNT_CODE``  — human code (e.g. ``VSV4K3RN2DD``); the
                                    actual value passed to Dodo's checkout.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _referee_discount_id() -> str:
    return os.getenv("DODO_REFEREE_DISCOUNT_ID", "").strip()


def create_referee_discount(referee_email: str) -> Optional[str]:
    """Return the static referee discount ID for storage on the Referral row.

    No Dodo API call — the discount already exists in the Dodo dashboard. The
    checkout view reads ``DODO_REFEREE_DISCOUNT_CODE`` env var directly to get
    the value passed as ``discount_code`` to Dodo. The ID returned here is
    audit metadata.
    """
    discount_id = _referee_discount_id()
    if not discount_id:
        logger.warning(
            "referrals: DODO_REFEREE_DISCOUNT_ID not set — referee %s will not get 10%% off",
            referee_email,
        )
        return None
    logger.info(
        "referrals: referee=%s eligible for 10%% off (discount_id=%s)",
        referee_email, discount_id,
    )
    return discount_id
