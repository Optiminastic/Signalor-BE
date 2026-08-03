"""Who may touch which org, run and task — the analyzer app's scoping seam.

Analyzer views historically resolved scope from a client-supplied ``?email=`` or
``org_id``, then either checked nothing or checked that value against itself. Two
consequences, both verified against a live test database:

- ``org_id`` is ``Organization``'s sequential integer PK, so ``org_id=1,2,3…``
  walked every tenant. ``Organization.slug`` exists precisely because the PK is
  guessable ("Unguessable public identifier", see organizations.models) — the
  analyzer endpoints just never used it.
- ``UserAction`` rows were fetched by integer id with no ownership check at all,
  so an anonymous caller could flip another account's task to VERIFIED, inject
  ``notes`` and award that account points.

Identity comes from ``core.auth.identity.resolve_request_email``: a verified
better-auth JWT when the frontend sends one, else the legacy ``email`` field
until ``REQUIRE_VERIFIED_IDENTITY`` is switched on. The helpers here take that
*resolved* email and answer the authorization question.

Note what this does and does not close while enforcement is off. Requiring
ownership removes integer-PK enumeration outright — an attacker must now know the
victim's address rather than count from 1. It does not stop someone who knows an
address from claiming it; only flipping ``REQUIRE_VERIFIED_IDENTITY`` does that,
and routing every call site through this module is what makes that one-line
change sufficient.

Agencies are why this is not a plain ``owner_email ==`` comparison: a member acts
on the brands their agency owns, so access is the union of the caller's own orgs
and their agency's.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from apps.organizations.models import Organization


def accessible_org_ids(email: str) -> set[int]:
    """Org ids ``email`` may read or write: their own, plus their agency's brands."""
    normalized = (email or "").lower().strip()
    if not normalized:
        return set()

    from apps.accounts.agency_utils import agency_org_ids, get_agency_context

    ids = set(Organization.objects.filter(owner_email=normalized).values_list("id", flat=True))
    context = get_agency_context(normalized)
    if context:
        ids.update(agency_org_ids(context.agency_email))
    return ids


def resolve_scoped_org(email: str, org_id) -> tuple[Organization | None, Response | None]:
    """Return ``(org, error_response)`` for a caller-supplied ``org_id``.

    Callers do ``org, err = resolve_scoped_org(...); if err: return err``. When
    ``org_id`` is absent this falls back to the caller's own org, which is what the
    dashboard does on first load.

    404 rather than 403 for an org outside the caller's scope: a 403 confirms the
    row exists, which is the enumeration signal we are removing.
    """
    normalized = (email or "").lower().strip()
    if not normalized:
        return None, Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

    not_found = Response(
        {"detail": "Brand not found for this account.", "code": "not_found"},
        status=status.HTTP_404_NOT_FOUND,
    )

    if org_id:
        if not str(org_id).isdigit():
            return None, not_found
        if int(org_id) not in accessible_org_ids(normalized):
            return None, not_found
        org = Organization.objects.filter(pk=int(org_id)).first()
        if org is None:
            return None, not_found
        return org, None

    org = Organization.objects.filter(owner_email=normalized).first()
    if org is None:
        return None, Response(
            {"error": "No organization found for this email."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return org, None


def caller_owns_run(email: str, run) -> bool:
    """Whether ``email`` may attach work to ``run``.

    Anonymous free-tool scans have no organization, so the run's own ``email`` is
    the only link back to a caller — hence both arms rather than an org check alone.
    """
    normalized = (email or "").lower().strip()
    if not normalized or run is None:
        return False
    if (run.email or "").lower().strip() == normalized:
        return True
    return bool(run.organization_id and run.organization_id in accessible_org_ids(normalized))


def caller_owns_action(email: str, action) -> bool:
    """Whether ``email`` may mutate ``action``.

    True when the task is the caller's own, is assigned to them, or belongs to a
    brand their agency works on. The agency arm is what keeps a teammate able to
    complete work assigned to them without widening access to everyone else.
    """
    normalized = (email or "").lower().strip()
    if not normalized:
        return False
    if (action.user_email or "").lower().strip() == normalized:
        return True
    if (action.assignee_email or "").lower().strip() == normalized:
        return True

    org_id = getattr(action.analysis_run, "organization_id", None)
    return bool(org_id and org_id in accessible_org_ids(normalized))
