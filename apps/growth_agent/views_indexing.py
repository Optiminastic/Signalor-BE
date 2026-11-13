"""The brand's indexing report, and "check now".

GET  programs/<slug>/indexing/        -> latest Search Console check vs the one before
POST programs/<slug>/indexing/check/  -> queue a check now (at most one per 6 hours)
POST programs/<slug>/indexing/requested/ {"url"} -> the owner opened that page's
     "Request indexing" link in Search Console; don't offer it again for a week
POST programs/<slug>/indexing/bing/ {"enabled"} -> turn weekly Bing sending on or off
POST programs/<slug>/indexing/bing/verify/       -> check the IndexNow key file now
POST programs/<slug>/indexing/bing/send/         -> send new and changed pages now

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response

from .services.indexing import bing, report, requests, weekly
from .views import GrowthBaseView
from .views_control import _program_or_404


class IndexingView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(report.build(program.organization))


class IndexingCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not weekly.start_check(program, actor_email=request.user.email):
            built = report.build(program.organization)
            if built["status"] in ("not_connected", "no_property"):
                detail = "Connect Search Console and pick the site's property first."
            else:
                detail = (
                    "A check ran recently. Each check reads every page, so it runs at most every 6 hours."
                )
            return Response({"detail": detail, "code": "check_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(report.build(program.organization), status=status.HTTP_202_ACCEPTED)


class RequestedSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=2000)


class IndexingRequestedView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = RequestedSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        url = payload.validated_data["url"]
        built = report.build(program.organization)
        offered = {item["url"] for item in built["to_request"]}
        if url not in offered:
            # Only pages from this week's list, never an arbitrary URL from the client.
            return Response(
                {"detail": "That page isn't on this week's list."}, status=status.HTTP_409_CONFLICT
            )
        requests.mark_requested(program, url, request.user.email)
        return Response(report.build(program.organization))


class BingSwitchSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class BingSwitchView(GrowthBaseView):
    """The owner's one-time decision: send new and changed pages to Bing every week."""

    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = BingSwitchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        bing.set_enabled(program, payload.validated_data["enabled"], request.user.email)
        return Response(report.build(program.organization))


class BingVerifyView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        verified, message = bing.verify(program)
        return Response({"verified": verified, "message": message})


class BingSendView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not bing.start(program, guard=bing.SEND_NOW_GUARD, actor_email=request.user.email):
            detail = "Pages were sent to Bing recently. Sending runs at most every 6 hours."
            return Response({"detail": detail, "code": "send_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(report.build(program.organization), status=status.HTTP_202_ACCEPTED)
