"""Review batches: the brand's drafts grouped by day.

GET  programs/<slug>/batches/                  -> recent batches and their items
GET  programs/<slug>/batches/<id>/             -> one batch
POST programs/<slug>/batches/<id>/decide/      {"decision": "rejected", "items": [{action_id, version, payload_hash}], "reason"?}

Bulk reject only: each item is checked and decided on its own and gets its own
result (HTTP 200 with per-item results). A bulk approve is refused with
``requires_individual``: every approval is one decision on one exact draft.
Owner-only (the program lookup is scoped to the signed-in owner).
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response

from .models import ApprovalBatch, ApprovalDecision
from .services.control import batches
from .views import GrowthBaseView
from .views_control import _program_or_404

RECENT_BATCHES = 14
MAX_ITEMS = 100
NOT_FOUND = {"detail": "Batch not found."}


class BatchItemSerializer(serializers.Serializer):
    action_id = serializers.CharField(max_length=32)
    version = serializers.IntegerField(min_value=1)
    payload_hash = serializers.CharField(max_length=64)


class BatchDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[ApprovalDecision.Decision.APPROVED, ApprovalDecision.Decision.REJECTED]
    )
    items = BatchItemSerializer(many=True, allow_empty=False, max_length=MAX_ITEMS)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


def _present(batch: ApprovalBatch) -> dict:
    return {
        "id": batch.id,
        "day": batch.day.isoformat(),
        "items": [
            {
                "action_id": item.action.public_id,
                "version": item.version.number,
                "payload_hash": item.version.payload_hash,
                "current": item.action.current_version_id == item.version_id,
                "state": item.action.state,
                "kind": item.action.kind,
                "title": item.action.task.title,
                "added_reason": item.added_reason,
            }
            for item in batch.items.all()
        ],
    }


def _queryset(program):
    return batches.batches_for(program).prefetch_related("items__action__task", "items__version")


class BatchListView(GrowthBaseView):
    def get(self, request, org_slug: str):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        return Response({"batches": [_present(b) for b in _queryset(program)[:RECENT_BATCHES]]})


class BatchDetailView(GrowthBaseView):
    def get(self, request, org_slug: str, batch_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        batch = _queryset(program).filter(pk=batch_id).first()
        if batch is None:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response({"batch": _present(batch)})


class BatchDecideView(GrowthBaseView):
    def post(self, request, org_slug: str, batch_id: int):
        program, missing = _program_or_404(request, org_slug)
        if missing:
            return missing
        batch = batches.batches_for(program).filter(pk=batch_id).first()
        if batch is None:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        payload = BatchDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data["decision"] == ApprovalDecision.Decision.APPROVED:
            detail = "Drafts are approved one at a time: open each draft and approve it."
            return Response(
                {"detail": detail, "code": batches.REQUIRES_INDIVIDUAL}, status=status.HTTP_400_BAD_REQUEST
            )
        items = [
            batches.BatchItemRequest(i["action_id"], i["version"], i["payload_hash"])
            for i in payload.validated_data["items"]
        ]
        results = batches.reject(
            program, batch, items, actor_email=request.user.email, reason=payload.validated_data["reason"]
        )
        return Response({"results": results})
