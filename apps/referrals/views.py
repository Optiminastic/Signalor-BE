"""Referral REST endpoints."""
from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Referral, ReferralReward
from .services import get_or_create_code, link_referee


def _summary_for(email: str) -> dict:
    referrals = Referral.objects.filter(referrer_email=email).order_by("-created_at")
    pending_count = referrals.filter(status=Referral.Status.PENDING).count()
    paid_count = referrals.filter(status=Referral.Status.PAID).count()
    cancelled_count = referrals.filter(status=Referral.Status.CANCELLED).count()

    pending_reward = (
        ReferralReward.objects
        .filter(referrer_email=email, status=ReferralReward.Status.PENDING)
        .first()
    )
    applied_count = ReferralReward.objects.filter(
        referrer_email=email, status=ReferralReward.Status.APPLIED
    ).count()

    return {
        "referrals": [
            {
                "referee_email": r.referee_email,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
                "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            }
            for r in referrals
        ],
        "stats": {
            "total": referrals.count(),
            "pending": pending_count,
            "paid": paid_count,
            "cancelled": cancelled_count,
            "rewards_applied": applied_count,
            "pending_reward": (
                {
                    "percent_off": pending_reward.percent_off,
                    "created_at": pending_reward.created_at.isoformat(),
                }
                if pending_reward
                else None
            ),
        },
    }


class ReferralMeView(APIView):
    """GET /api/referrals/me/?email=user@x.com — returns code + referral history."""

    permission_classes = [AllowAny]

    def get(self, request):
        email = (request.query_params.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "email query param required"}, status=400)

        code_row = get_or_create_code(email)
        return Response({
            "email": email,
            "code": code_row.code,
            "share_url": f"/?ref={code_row.code}",
            **_summary_for(email),
        })


class ReferralRedeemView(APIView):
    """POST /api/referrals/redeem/ — body {code, email}.

    Called from the frontend right after sign-up when ?ref= was present.
    Idempotent — safe to call multiple times.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get("code") or "").strip().upper()
        email = (request.data.get("email") or "").strip().lower()
        if not code or not email:
            return Response({"detail": "code and email required"}, status=400)

        referral = link_referee(code, email)
        if not referral:
            return Response({"detail": "invalid code or self-referral"}, status=400)

        return Response({
            "referrer_email": referral.referrer_email,
            "referee_email": referral.referee_email,
            "status": referral.status,
            "discount_percent": 10,  # the referee's first-payment discount
        }, status=status.HTTP_201_CREATED)
