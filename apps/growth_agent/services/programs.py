"""
Program lookups, always scoped to the verified caller.

Every query that returns an organization or program goes through
``owned_organizations`` so no endpoint can forget the owner filter (IDOR).
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.organizations.models import Organization

from ..models import GrowthProgram


def owned_organizations(email: str) -> QuerySet[Organization]:
    return Organization.objects.filter(owner_email__iexact=email)


def get_owned_organization(email: str, org_slug: str) -> Organization | None:
    if not org_slug:
        return None
    return owned_organizations(email).filter(slug=org_slug).first()


def get_program(email: str, org_slug: str) -> GrowthProgram | None:
    return (
        GrowthProgram.objects.select_related("organization")
        .filter(organization__in=owned_organizations(email), organization__slug=org_slug)
        .first()
    )


def get_or_create_program(email: str, organization: Organization) -> tuple[GrowthProgram, bool]:
    """Idempotent: re-posting for the same brand returns the existing program."""
    return GrowthProgram.objects.get_or_create(
        organization=organization,
        defaults={"created_by_email": email},
    )
