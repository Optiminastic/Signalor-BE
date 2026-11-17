"""Weekly SEO / GEO / Brand tracking for the brand and its top competitors.

GET  programs/<slug>/rank-tracking/                          -> players, tracked keywords, SEO/GEO/Brand by week
POST programs/<slug>/rank-tracking/check/                    -> check positions now (at most once a day)
POST programs/<slug>/rank-tracking/keywords/                 {"keyword": "..."} -> track a keyword
POST programs/<slug>/rank-tracking/keywords/<id>/remove/     -> stop tracking it (never re-added automatically)

Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response

from .models import TrackedKeyword
from .services.rank_tracking import check, rules
from .services.rank_tracking import report as tracking_report
from .views import GrowthBaseView
from .views_control import _program_or_404


class KeywordSerializer(serializers.Serializer):
    keyword = serializers.CharField(max_length=rules.MAX_KEYWORD_CHARS, trim_whitespace=True)


class RankTrackingView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        return missing or Response(tracking_report.report(program))


class RankTrackingCheckView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        if not check.start(program, guard=check.START_GUARD, actor_email=request.user.email):
            detail = "Positions were checked recently. They can be checked at most once a day."
            return Response({"detail": detail, "code": "check_not_started"}, status=status.HTTP_409_CONFLICT)
        return Response(tracking_report.report(program), status=status.HTTP_202_ACCEPTED)


class TrackedKeywordAddView(GrowthBaseView):
    def post(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = KeywordSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        keyword = rules.clean(payload.validated_data["keyword"])
        if program.tracked_keywords.filter(active=True).count() >= rules.MAX_KEYWORDS:
            detail = f"You can track up to {rules.MAX_KEYWORDS} keywords. Remove one first."
            return Response({"detail": detail, "code": "too_many"}, status=status.HTTP_400_BAD_REQUEST)
        TrackedKeyword.objects.update_or_create(
            program=program, keyword=keyword, defaults={"active": True, "source": TrackedKeyword.Source.OWNER}
        )
        return Response(tracking_report.report(program))


class TrackedKeywordRemoveView(GrowthBaseView):
    def post(self, request, org_slug: str, keyword_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        program.tracked_keywords.filter(pk=keyword_id).update(active=False)
        return Response(tracking_report.report(program))
