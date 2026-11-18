"""The brand's connected Gmail mailbox, shared by the outreach modules."""

from __future__ import annotations

from apps.integrations.models import Integration


def gmail_integration(organization) -> Integration | None:
    return (
        Integration.objects.filter(
            organization=organization, provider=Integration.Provider.GMAIL, is_active=True
        )
        .order_by("-updated_at")
        .first()
    )


def mailbox_address(integration: Integration | None) -> str:
    """The connected mailbox's address, or "" when there is none."""
    return (integration.metadata.get("email", "") if integration else "") or ""
