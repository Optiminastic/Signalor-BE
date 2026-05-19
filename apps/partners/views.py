"""Partner / affiliate program REST endpoints."""

from __future__ import annotations

import os
import re
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Partner, PartnerAttribution, PartnerCommission
from .services import set_attribution

# Refund window before a pending commission is considered locked/payable. Keep
# this aligned with the Dodo refund policy and the user-facing copy.
COMMISSION_LOCK_WINDOW_DAYS = 30

# Audience-size buckets accepted by the public apply form.
_ALLOWED_AUDIENCE_SIZES = {"", "<1k", "1k-10k", "10k-100k", "100k-1m", "1m+"}

# Lightweight ISO 3166-1 alpha-2 check: two uppercase letters. We do not
# enforce the full membership list here — the frontend select already does.
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

# Allowed social platform keys (case-insensitive on input, lowercased on save).
_ALLOWED_PLATFORMS = {
    "youtube",
    "x",
    "twitter",
    "instagram",
    "tiktok",
    "linkedin",
    "substack",
    "facebook",
    "threads",
    "twitch",
    "podcast",
    "blog",
    "other",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _frontend_base() -> str:
    """Public origin used to build share + dashboard URLs."""
    return (os.getenv("FRONTEND_BASE_URL") or "http://localhost:3000").rstrip("/")


def _mask_email(email: str) -> str:
    """Mask the local-part of an email so public dashboards don't leak it."""
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    if len(local) <= 1:
        return f"{local}***@{domain}"
    return f"{local[0]}{'*' * max(2, len(local) - 1)}@{domain}"


def _clean_social_platforms(raw) -> list[dict]:
    """Normalize the form's socials payload into a clean list of {platform, handle}."""
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    seen_platforms: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        platform = str(entry.get("platform") or "").strip().lower()
        handle = str(entry.get("handle") or "").strip()
        if not platform or platform not in _ALLOWED_PLATFORMS:
            continue
        if not handle:
            continue
        if platform in seen_platforms:
            continue
        seen_platforms.add(platform)
        cleaned.append({"platform": platform, "handle": handle[:120]})
    return cleaned


class PartnerTrackView(APIView):
    """POST /api/partners/track/ — body {code, landing_path?}.

    Lightweight click acknowledgement. We do not record the click as a separate
    row (yet) — the frontend mainly calls this to verify the code is valid
    before stashing it in localStorage.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get("code") or "").strip().upper()
        if not code:
            return Response({"valid": False}, status=200)

        partner = Partner.objects.filter(code=code).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"valid": False}, status=200)

        return Response({"valid": True, "partner_name": partner.name or ""}, status=200)


class PartnerAttributeView(APIView):
    """POST /api/partners/attribute/ — body {code, email, landing_path?}.

    Called from the sign-up flow when the affiliate localStorage key is present.
    Last-click semantics: any new attribute call overwrites the existing row.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        code = (request.data.get("code") or "").strip().upper()
        email = (request.data.get("email") or "").strip().lower()
        landing_path = (request.data.get("landing_path") or "").strip()

        if not code or not email:
            return Response({"detail": "code and email required"}, status=400)

        attribution = set_attribution(email, code, landing_path=landing_path)
        if not attribution:
            return Response({"detail": "invalid or terminated code"}, status=400)

        return Response(
            {
                "partner_code": attribution.partner.code,
                "expires_at": attribution.expires_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class PartnerApplyView(APIView):
    """POST /api/partners/apply/ — public creators-program signup.

    Auto-approves every applicant: a new Partner row is created with
    ``status=ACTIVE`` and the default 20% commission. Idempotent on email — if
    someone submits the form twice we return their existing code rather than
    minting a duplicate.

    Body: {name, email, country, social_platforms: [{platform, handle}, ...],
           audience_size?}

    Returns 201 (or 200 if already existed) with the creator's code plus the
    shareable + dashboard URLs the frontend should display.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        name = (request.data.get("name") or "").strip()
        email = (request.data.get("email") or "").strip().lower()
        country = (request.data.get("country") or "").strip().upper()
        audience_size = (request.data.get("audience_size") or "").strip()
        social_platforms = _clean_social_platforms(request.data.get("social_platforms"))
        payout_method = (request.data.get("payout_method") or "").strip().lower()
        payout_details = (request.data.get("payout_details") or "").strip()

        # Validation. Keep messages short; the frontend renders them inline.
        if not name:
            return Response({"detail": "Name is required."}, status=400)
        if not email or not _EMAIL_RE.match(email):
            return Response({"detail": "A valid email is required."}, status=400)
        if not country or not _COUNTRY_RE.match(country):
            return Response({"detail": "Pick a country."}, status=400)
        if not social_platforms:
            return Response(
                {"detail": "Add at least one social platform with a handle."},
                status=400,
            )
        if audience_size and audience_size not in _ALLOWED_AUDIENCE_SIZES:
            return Response({"detail": "Invalid audience size."}, status=400)

        valid_payout_methods = {choice.value for choice in Partner.PayoutMethod}
        if not payout_method or payout_method not in valid_payout_methods:
            return Response({"detail": "Pick how you'd like to be paid."}, status=400)
        if not payout_details or len(payout_details) < 3:
            return Response(
                {"detail": "Add the details we need to pay you (account, email, or wallet)."},
                status=400,
            )
        # Hard cap to keep abuse manageable; the DB column is TextField so this
        # is the only effective limit.
        payout_details = payout_details[:2000]

        existing = Partner.objects.filter(email=email).first()
        if existing:
            # Idempotent re-apply: re-bind the latest application fields so a
            # creator can update their socials/payout by re-submitting the
            # form, but do not flip them back to ACTIVE if an admin has paused
            # them.
            existing.name = name or existing.name
            existing.country = country or existing.country
            existing.audience_size = audience_size or existing.audience_size
            existing.social_platforms = social_platforms or existing.social_platforms
            existing.payout_method = payout_method
            existing.payout_details = payout_details
            existing.save(
                update_fields=[
                    "name",
                    "country",
                    "audience_size",
                    "social_platforms",
                    "payout_method",
                    "payout_details",
                    "updated_at",
                ]
            )
            partner = existing
            created = False
        else:
            partner = Partner.objects.create(
                email=email,
                name=name,
                code=Partner.generate_unique_code(),
                country=country,
                social_platforms=social_platforms,
                audience_size=audience_size,
                payout_method=payout_method,
                payout_details=payout_details,
                status=Partner.Status.ACTIVE,
            )
            created = True

        base = _frontend_base()
        return Response(
            {
                "code": partner.code,
                "name": partner.name,
                "share_url": f"{base}/?aff={partner.code}",
                "dashboard_url": f"{base}/creators-program/{partner.code}",
                "status": partner.status,
                "commission_percent": partner.commission_percent,
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PartnerPublicStatsView(APIView):
    """GET /api/partners/stats/?code=ABC — public dashboard payload.

    Public on purpose: the code itself acts as a soft-secret share token. We
    deliberately omit ``email``, ``payout_method``, and ``payout_details`` from
    the response so a leaked dashboard URL can't be used to exfiltrate
    payout PII. Referee emails are masked.

    Pending vs locked logic:
    - A PENDING commission younger than 30 days is shown as "Pending — locking
      in N days" (still revocable if the customer refunds).
    - A PENDING commission older than 30 days is shown as "Locked — payable".
    - PAID rows are paid out.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        code = (request.query_params.get("code") or "").strip().upper()
        if not code:
            return Response({"detail": "code required"}, status=400)

        partner = Partner.objects.filter(code=code).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"detail": "not found"}, status=404)

        now = timezone.now()
        lock_cutoff = now - timedelta(days=COMMISSION_LOCK_WINDOW_DAYS)

        attributions_qs = PartnerAttribution.objects.filter(partner=partner)
        attributions_total = attributions_qs.count()
        attributions_active = attributions_qs.filter(expires_at__gt=now).count()

        commissions_qs = PartnerCommission.objects.filter(partner=partner)

        pending_qs = commissions_qs.filter(
            status=PartnerCommission.Status.PENDING,
            created_at__gte=lock_cutoff,
        )
        locked_qs = commissions_qs.filter(
            status=PartnerCommission.Status.PENDING,
            created_at__lt=lock_cutoff,
        )
        paid_qs = commissions_qs.filter(status=PartnerCommission.Status.PAID)

        def _bucket(qs):
            agg = qs.aggregate(total=Sum("commission_amount"))
            return {
                "count": qs.count(),
                "amount": float(agg["total"] or Decimal("0")),
            }

        # Bubble up the display bucket so the frontend doesn't have to
        # re-derive lock state from the timestamp.
        recent = []
        for c in commissions_qs.exclude(status=PartnerCommission.Status.CANCELLED).order_by("-created_at")[
            :20
        ]:
            if c.status == PartnerCommission.Status.PAID:
                bucket = "paid"
            elif (now - c.created_at).days >= COMMISSION_LOCK_WINDOW_DAYS:
                bucket = "locked"
            else:
                bucket = "pending"
            recent.append(
                {
                    "created_at": c.created_at.isoformat(),
                    "referee_email": _mask_email(c.referee_email),
                    "commission_amount": float(c.commission_amount),
                    "currency": c.currency,
                    "status": c.status,
                    "bucket": bucket,
                }
            )

        base = _frontend_base()
        return Response(
            {
                "code": partner.code,
                "name": partner.name,
                "country": partner.country,
                "social_platforms": partner.social_platforms or [],
                "status": partner.status,
                "commission_percent": partner.commission_percent,
                "created_at": partner.created_at.isoformat(),
                "share_url": f"{base}/?aff={partner.code}",
                "dashboard_url": f"{base}/creators-program/{partner.code}",
                "stats": {
                    "attributions_total": attributions_total,
                    "attributions_active": attributions_active,
                    "pending": _bucket(pending_qs),
                    "locked": _bucket(locked_qs),
                    "paid": _bucket(paid_qs),
                    "lock_window_days": COMMISSION_LOCK_WINDOW_DAYS,
                },
                "recent_commissions": recent,
            },
            status=200,
        )


def _build_stats_payload(partner: Partner) -> dict:
    """Aggregate the same buckets the public stats view computes, plus the
    full (unmasked) recent-commissions list for the authed dashboard."""
    now = timezone.now()
    lock_cutoff = now - timedelta(days=COMMISSION_LOCK_WINDOW_DAYS)

    attributions_qs = PartnerAttribution.objects.filter(partner=partner)
    commissions_qs = PartnerCommission.objects.filter(partner=partner)
    pending_qs = commissions_qs.filter(
        status=PartnerCommission.Status.PENDING,
        created_at__gte=lock_cutoff,
    )
    locked_qs = commissions_qs.filter(
        status=PartnerCommission.Status.PENDING,
        created_at__lt=lock_cutoff,
    )
    paid_qs = commissions_qs.filter(status=PartnerCommission.Status.PAID)

    def _bucket(qs):
        agg = qs.aggregate(total=Sum("commission_amount"))
        return {"count": qs.count(), "amount": float(agg["total"] or Decimal("0"))}

    recent = []
    for c in commissions_qs.exclude(
        status=PartnerCommission.Status.CANCELLED,
    ).order_by("-created_at")[:50]:
        if c.status == PartnerCommission.Status.PAID:
            bucket = "paid"
        elif (now - c.created_at).days >= COMMISSION_LOCK_WINDOW_DAYS:
            bucket = "locked"
        else:
            bucket = "pending"
        recent.append(
            {
                "created_at": c.created_at.isoformat(),
                "referee_email": _mask_email(c.referee_email),
                "commission_amount": float(c.commission_amount),
                "currency": c.currency,
                "status": c.status,
                "bucket": bucket,
            }
        )

    return {
        "attributions_total": attributions_qs.count(),
        "attributions_active": attributions_qs.filter(expires_at__gt=now).count(),
        "pending": _bucket(pending_qs),
        "locked": _bucket(locked_qs),
        "paid": _bucket(paid_qs),
        "lock_window_days": COMMISSION_LOCK_WINDOW_DAYS,
        "recent_commissions": recent,
    }


class PartnerExistsView(APIView):
    """GET /api/partners/exists/?email= — boolean check for the sign-in flow.

    Used right after creator sign-in to decide whether to send the user to the
    dashboard (Partner row exists) or to the apply form (first-time).
    """

    permission_classes = [AllowAny]

    def get(self, request):
        email = (request.query_params.get("email") or "").strip().lower()
        if not email:
            return Response({"exists": False}, status=200)
        partner = Partner.objects.filter(email=email).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"exists": False}, status=200)
        return Response(
            {"exists": True, "code": partner.code, "status": partner.status},
            status=200,
        )


class PartnerMeView(APIView):
    """GET / PATCH /api/partners/me/ — authed creator's own profile.

    Email comes from the query param (GET) or body (PATCH). Auth is enforced
    upstream by the better-auth cookie + the frontend pinning the request email
    to the session email — same pattern the rest of the app uses for AllowAny
    endpoints (see CLAUDE.md). Private fields (email, payout_method,
    payout_details) are exposed here but never on /api/partners/stats/.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        email = (request.query_params.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "email required"}, status=400)
        partner = Partner.objects.filter(email=email).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"detail": "not found"}, status=404)

        base = _frontend_base()
        return Response(
            {
                "email": partner.email,
                "code": partner.code,
                "name": partner.name,
                "country": partner.country,
                "social_platforms": partner.social_platforms or [],
                "audience_size": partner.audience_size,
                "payout_method": partner.payout_method,
                "payout_details": partner.payout_details,
                "status": partner.status,
                "commission_percent": partner.commission_percent,
                "created_at": partner.created_at.isoformat(),
                "share_url": f"{base}/?aff={partner.code}",
                "dashboard_url": f"{base}/creators-program/{partner.code}",
                "stats": _build_stats_payload(partner),
            },
            status=200,
        )

    def patch(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "email required"}, status=400)
        partner = Partner.objects.filter(email=email).first()
        if not partner or partner.status == Partner.Status.TERMINATED:
            return Response({"detail": "not found"}, status=404)

        # Each field is optional on PATCH — only touch what the client sends.
        update_fields: list[str] = []

        if "name" in request.data:
            name = (request.data.get("name") or "").strip()
            if not name:
                return Response({"detail": "Name can't be empty."}, status=400)
            partner.name = name
            update_fields.append("name")

        if "country" in request.data:
            country = (request.data.get("country") or "").strip().upper()
            if country and not _COUNTRY_RE.match(country):
                return Response({"detail": "Invalid country code."}, status=400)
            partner.country = country
            update_fields.append("country")

        if "social_platforms" in request.data:
            socials = _clean_social_platforms(request.data.get("social_platforms"))
            if not socials:
                return Response(
                    {"detail": "Add at least one social platform with a handle."},
                    status=400,
                )
            partner.social_platforms = socials
            update_fields.append("social_platforms")

        if "audience_size" in request.data:
            audience_size = (request.data.get("audience_size") or "").strip()
            if audience_size and audience_size not in _ALLOWED_AUDIENCE_SIZES:
                return Response({"detail": "Invalid audience size."}, status=400)
            partner.audience_size = audience_size
            update_fields.append("audience_size")

        if "payout_method" in request.data:
            payout_method = (request.data.get("payout_method") or "").strip().lower()
            valid = {choice.value for choice in Partner.PayoutMethod}
            if payout_method not in valid:
                return Response({"detail": "Invalid payout method."}, status=400)
            partner.payout_method = payout_method
            update_fields.append("payout_method")

        if "payout_details" in request.data:
            payout_details = (request.data.get("payout_details") or "").strip()
            if not payout_details or len(payout_details) < 3:
                return Response(
                    {"detail": "Add the details we need to pay you."},
                    status=400,
                )
            partner.payout_details = payout_details[:2000]
            update_fields.append("payout_details")

        if not update_fields:
            return Response({"detail": "Nothing to update."}, status=400)

        update_fields.append("updated_at")
        partner.save(update_fields=update_fields)

        base = _frontend_base()
        return Response(
            {
                "email": partner.email,
                "code": partner.code,
                "name": partner.name,
                "country": partner.country,
                "social_platforms": partner.social_platforms or [],
                "audience_size": partner.audience_size,
                "payout_method": partner.payout_method,
                "payout_details": partner.payout_details,
                "status": partner.status,
                "commission_percent": partner.commission_percent,
                "share_url": f"{base}/?aff={partner.code}",
                "dashboard_url": f"{base}/creators-program/{partner.code}",
            },
            status=200,
        )
