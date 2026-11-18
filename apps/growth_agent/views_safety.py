"""Safety checks: changes to who owns a brand or where its connectors point.

GET  programs/<slug>/safety-changes/                 -> unresolved changes for this brand
POST programs/<slug>/safety-changes/<id>/challenge/  -> email a one-time code to the owner
POST programs/<slug>/safety-changes/<id>/confirm/    {code, old_identity, new_identity, reason}

Owner-only (the program lookup is scoped to the signed-in owner). Ownership and
brand moves are listed but can only be reviewed by a superuser with TOTP.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response

from .services.control import confirmations, safety
from .views import GrowthBaseView
from .views_control import _program_or_404


class ConfirmSerializer(serializers.Serializer):
    code = serializers.RegexField(r"^\s*\d{8}\s*$", max_length=12)
    old_identity = serializers.CharField(max_length=300, allow_blank=True)
    new_identity = serializers.CharField(max_length=300, allow_blank=True)
    reason = serializers.CharField(max_length=500)


def _present(change) -> dict:
    return {
        "id": change.id,
        "entity": change.entity,
        "provider": change.provider,
        "field": change.field,
        "old": change.old_display,
        "new": change.new_display,
        "changed_at": change.created_at.isoformat(),
        "owner_can_confirm": safety.owner_may_confirm(change),
        "incident": change.review_state == "confirmed_incident",
    }


def _error(exc: confirmations.ConfirmationError) -> Response:
    body = {"detail": str(exc), "code": exc.code}
    if exc.attempts_left is not None:
        body["attempts_left"] = max(exc.attempts_left, 0)
    return Response(body, status=exc.status)


class SafetyChangeListView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        changes = safety.unresolved(program.organization_id).order_by("-created_at")[:50]
        return Response({"blocked": bool(changes), "changes": [_present(c) for c in changes]})


class SafetyChallengeView(GrowthBaseView):
    def post(self, request, org_slug: str, change_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        try:
            return Response(confirmations.issue(program, change_id), status=status.HTTP_201_CREATED)
        except confirmations.ConfirmationError as exc:
            return _error(exc)


class SafetyConfirmView(GrowthBaseView):
    def post(self, request, org_slug: str, change_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        payload = ConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        confirm = confirmations.ConfirmRequest(
            code=data["code"].strip(),
            old_identity=data["old_identity"],
            new_identity=data["new_identity"],
            reason=data["reason"],
            owner_email=request.user.email,
        )
        try:
            confirmations.confirm(program, change_id, confirm)
        except confirmations.ConfirmationError as exc:
            return _error(exc)
        return Response({"confirmed": True, "blocked": safety.blocked(program.organization_id)})
