"""
Backlink-opportunity service.

Owns the per-prompt list of "places where the brand can earn a backlink"
shown in the prompt's Actions panel. This module is the single place where:

  - the LLM is asked for fresh suggestions
  - rows are persisted / mutated / deleted
  - status transitions (suggested -> submitted -> live -> dismissed) are policed

Views in ``apps/analyzer/views.py`` delegate to ``OpportunityService`` and do
nothing else with the data.

SOLID notes:
  * SRP — this module only handles opportunities; nothing else depends on it.
  * Open/closed — adding a new ``OpportunityStatus`` value or category only
    needs a model-level change; no service code edits.
  * DI — ``OpportunityService`` takes a ``PromptTrack`` in its constructor,
    so callers can pass a real or stubbed track in tests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from apps.analyzer.models import BacklinkOpportunity, PromptTrack
from apps.analyzer.pipeline.backlink_opportunities import (
    delete_for_prompt,
    generate_for_prompt,
)

logger = logging.getLogger("apps")

ALLOWED_STATUSES = {s.value for s in BacklinkOpportunity.Status}


class OpportunityServiceError(Exception):
    """Raised when an opportunity operation can't proceed (e.g. invalid status)."""


@dataclass
class OpportunityService:
    """
    Business operations for a single prompt's backlink opportunities.

    All methods return plain dicts / lists ready to put into a DRF Response —
    no `request` or `Response` types leak in. Errors are raised as
    ``OpportunityServiceError`` (HTTP 400) or stdlib exceptions (HTTP 500).
    """

    track: PromptTrack

    def list(self) -> dict:
        """Return persisted opportunities for the track plus a `has_generated` flag."""
        qs = BacklinkOpportunity.objects.filter(prompt_track=self.track)
        return {
            "rows": [serialize(o) for o in qs],
            "has_generated": qs.exists(),
        }

    def regenerate(self) -> dict:
        """
        Wipe all stored rows and ask the LLM for a fresh batch.

        Raises ``OpportunityServiceError`` if generation fails so the view
        can surface a meaningful 502.
        """
        delete_for_prompt(self.track)
        try:
            generate_for_prompt(self.track)
        except Exception as exc:
            logger.warning(
                "regenerate failed for track %d: %s", self.track.pk, exc, exc_info=True,
            )
            raise OpportunityServiceError(f"Generation failed: {exc}") from exc

        qs = BacklinkOpportunity.objects.filter(prompt_track=self.track)
        return {"rows": [serialize(o) for o in qs], "has_generated": True}

    def update_status(
        self,
        opp_id: int,
        *,
        new_status: str | None = None,
        live_url: str | None = None,
    ) -> dict:
        """Patch a single opportunity. Validates inputs; sets `submitted_at` if appropriate."""
        try:
            opp = BacklinkOpportunity.objects.get(pk=opp_id, prompt_track=self.track)
        except BacklinkOpportunity.DoesNotExist as exc:
            raise OpportunityServiceError("Opportunity not found.") from exc

        update_fields: list[str] = []
        if new_status:
            normalized = new_status.strip().lower()
            if normalized not in ALLOWED_STATUSES:
                raise OpportunityServiceError(f"Invalid status '{new_status}'.")
            opp.status = normalized
            update_fields.append("status")
            if normalized == BacklinkOpportunity.Status.SUBMITTED and not opp.submitted_at:
                opp.submitted_at = timezone.now()
                update_fields.append("submitted_at")
        if live_url:
            opp.live_url = live_url[:2048]
            update_fields.append("live_url")
        if update_fields:
            update_fields.append("updated_at")
            opp.save(update_fields=update_fields)
        return serialize(opp)

    def delete(self, opp_id: int) -> None:
        """Permanently remove an opportunity row."""
        try:
            opp = BacklinkOpportunity.objects.get(pk=opp_id, prompt_track=self.track)
        except BacklinkOpportunity.DoesNotExist as exc:
            raise OpportunityServiceError("Opportunity not found.") from exc
        opp.delete()


def serialize(opp: BacklinkOpportunity) -> dict:
    """Plain-dict representation for JSON responses."""
    return {
        "id": opp.pk,
        "name": opp.name,
        "description": opp.description,
        "rationale": opp.rationale,
        "submit_url": opp.submit_url,
        "category": opp.category,
        "priority": opp.priority,
        "status": opp.status,
        "submitted_at": opp.submitted_at.isoformat() if opp.submitted_at else None,
        "live_url": opp.live_url,
        "created_at": opp.created_at.isoformat() if opp.created_at else None,
    }
