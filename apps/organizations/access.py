"""Who may edit or delete a brand through the organizations API.

The single place this rule lives. The endpoint enforces it, and the request
observation reports against the same functions, so the two can never disagree.

Identity is always a server-verified email. Roles are re-derived from server
records on every call, never taken from the request.
"""

from __future__ import annotations

from apps.accounts.agency_utils import writable_owner_emails

from .models import Organization


def _normalized(email: str | None) -> str:
    return (email or "").strip().lower()


def may_edit(email: str | None, org: Organization) -> bool:
    """The brand's owner, or an admin of the agency that owns it."""
    caller = _normalized(email)
    if not caller:
        return False
    return _normalized(org.owner_email) in writable_owner_emails(caller)


def may_delete(email: str | None, org: Organization) -> bool:
    """The brand's owner only. Agency admins may edit a brand but not delete it."""
    caller = _normalized(email)
    if not caller:
        return False
    return caller == _normalized(org.owner_email)
