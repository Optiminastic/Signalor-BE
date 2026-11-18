"""
Safety changes: who owns a brand, and which mailbox, site or repository its
connectors point at. Database triggers record every change (migration 0024);
this module decides what they mean.

A **high-risk** change is one to ownership or to a connector's identity made by
an **unknown** actor (not the app's own owner, system or admin paths). While one
is **unresolved** (no review yet, or its newest review is ``confirmed_incident``)
the brand's approvals are refused and its sends wait. Turning a connector off or
deleting it is recorded but not high-risk: an inactive or missing connector
already can't send.

Who may clear what (Phase 1 decision): the brand owner may confirm a change to
their own connector with an emailed code; ownership and brand moves are reviewed
by a superuser with TOTP.
"""

from __future__ import annotations

import hashlib

from django.db import IntegrityError, connection, transaction
from django.db.models import OuterRef, Q, QuerySet, Subquery

from ...models import GrowthSafetyChange, GrowthSafetyReview

UNKNOWN = "unknown"
ACTOR_SETTING = "signalor.actor"

# (entity, field) pairs whose unknown-source change blocks the brand.
HIGH_RISK = frozenset(
    {
        ("organization", "owner_email"),
        ("integration", "organization_id"),
        ("integration", "provider"),
        ("integration", "metadata.email"),
        ("integration", "metadata.site_url"),
        ("integration", "metadata.blog_id"),
        ("integration", "metadata.is_wpcom"),
        ("integration", "metadata.username"),
        ("github_installation", "organization_id"),
        ("github_installation", "installation_id"),
        ("github_installation", "repo_full_name"),
    }
)
# Ownership and brand moves: a superuser with TOTP only, never the owner.
SUPERUSER_ONLY_FIELDS = frozenset({"owner_email", "organization_id"})

# What may follow the newest review (None = no review yet).
NEXT_OUTCOMES = {
    None: {GrowthSafetyReview.Outcome.CLEARED, GrowthSafetyReview.Outcome.CONFIRMED_INCIDENT},
    GrowthSafetyReview.Outcome.CLEARED: {GrowthSafetyReview.Outcome.CONFIRMED_INCIDENT},
    GrowthSafetyReview.Outcome.CONFIRMED_INCIDENT: {GrowthSafetyReview.Outcome.INCIDENT_RESOLVED},
    GrowthSafetyReview.Outcome.INCIDENT_RESOLVED: set(),
}


class ReviewRefused(Exception):
    """The review can't be recorded (illegal step, or someone else reviewed first)."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def owner_digest(email: str) -> str:
    return digest((email or "").strip().lower())


def set_actor(actor: str) -> None:
    """Name who is changing monitored rows in this transaction (Postgres only).

    The app's own writers call this (``owner:<digest>``, ``system:<part>``,
    ``admin:<id>``); anything that doesn't is recorded as ``unknown``.
    """
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [ACTOR_SETTING, actor])


def owner_actor(email: str) -> str:
    return f"owner:{owner_digest(email)}"


def is_high_risk(change: GrowthSafetyChange) -> bool:
    return change.actor == UNKNOWN and (change.entity, change.field) in HIGH_RISK


def owner_may_confirm(change: GrowthSafetyChange) -> bool:
    return change.field not in SUPERUSER_ONLY_FIELDS


def _latest_outcome() -> Subquery:
    newest = GrowthSafetyReview.objects.filter(change=OuterRef("pk")).order_by("-review_no")
    return Subquery(newest.values("outcome")[:1])


def unresolved(org_id: int) -> QuerySet[GrowthSafetyChange]:
    """The brand's high-risk unknown-source changes with no review or a confirmed incident."""
    risky = GrowthSafetyChange.objects.filter(organization_id=org_id, actor=UNKNOWN)
    condition = Q(pk__in=[])
    for entity, field in HIGH_RISK:
        condition |= Q(entity=entity, field=field)
    risky = risky.filter(condition).annotate(review_state=_latest_outcome())
    return risky.filter(
        Q(review_state__isnull=True) | Q(review_state=GrowthSafetyReview.Outcome.CONFIRMED_INCIDENT)
    )


def blocked(org_id: int) -> bool:
    """Whether approvals and sends for this brand must wait for a safety review."""
    return unresolved(org_id).exists()


def ownership_in_doubt(org_id: int) -> bool:
    return unresolved(org_id).filter(field__in=SUPERUSER_ONLY_FIELDS).exists()


def latest_review(change: GrowthSafetyChange) -> GrowthSafetyReview | None:
    return change.reviews.order_by("-review_no").first()


def record_review(
    change: GrowthSafetyChange,
    outcome: str,
    *,
    step_up: str,
    reason: str,
    reviewer_user_id: int | None = None,
    reviewer_owner_digest: str = "",
) -> GrowthSafetyReview:
    """Append the next review event, if that step is allowed from the newest one."""
    if not reason.strip():
        raise ReviewRefused("A reason is required.")
    current = latest_review(change)
    allowed = NEXT_OUTCOMES[current.outcome if current else None]
    if outcome not in allowed:
        raise ReviewRefused(f"'{outcome}' can't follow '{current.outcome if current else 'no review'}'.")
    try:
        with transaction.atomic():
            return GrowthSafetyReview.objects.create(
                organization_id=change.organization_id,
                change=change,
                review_no=(current.review_no if current else 0) + 1,
                outcome=outcome,
                step_up=step_up,
                reviewer_user_id=reviewer_user_id,
                reviewer_owner_digest=reviewer_owner_digest,
                reason=reason.strip()[:500],
            )
    except IntegrityError as exc:
        raise ReviewRefused("Someone else reviewed this change at the same moment. Look again.") from exc
