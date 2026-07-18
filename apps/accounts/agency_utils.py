"""Agency team role resolution.

Identity is email-based across this codebase, so an agency is identified by its
owner's email (an ``AccountProfile`` with ``account_type=agency``). The owner is
the implicit Admin; invited teammates are ``AgencyMembership`` rows. Roles are
always re-derived here from server records — never trusted from the request.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.organizations.models import Organization

from .models import AgencyMembership
from .subscription_utils import get_account_type

# 1 Admin (the owner) + this many invited teammates.
MAX_AGENCY_MEMBERS = 2


@dataclass(frozen=True)
class AgencyContext:
    """The caller's place in an agency: which agency, and what role."""

    agency_email: str
    role: str  # "admin" | "member"

    @property
    def is_admin(self) -> bool:
        return self.role == AgencyMembership.Role.ADMIN


def get_agency_context(email: str | None) -> AgencyContext | None:
    """Resolve an email to its agency + role, or ``None`` if not on any agency.

    - An agency owner (``account_type=agency``) is that agency's Admin.
    - Otherwise, an active membership row grants its stored role.
    """
    normalized = (email or "").strip().lower()
    if not normalized:
        return None

    if get_account_type(normalized) == "agency":
        return AgencyContext(agency_email=normalized, role=AgencyMembership.Role.ADMIN)

    membership = (
        AgencyMembership.objects.filter(member_email=normalized, status=AgencyMembership.Status.ACTIVE)
        .only("agency_email", "role")
        .first()
    )
    if membership is not None:
        return AgencyContext(agency_email=membership.agency_email, role=membership.role)
    return None


def is_agency_admin(email: str | None) -> bool:
    ctx = get_agency_context(email)
    return bool(ctx and ctx.is_admin)


def agency_org_ids(agency_email: str) -> list[int]:
    """The brand/project ids owned by the agency (all brands its team works on)."""
    return list(
        Organization.objects.filter(owner_email=(agency_email or "").strip().lower()).values_list(
            "id", flat=True
        )
    )
