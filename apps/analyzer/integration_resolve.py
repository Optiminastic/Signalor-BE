"""
Resolve the active store integration (WordPress, Shopify, or Webflow) for an
analysis run.

Business rule: an organization has at most one *active* store integration —
WordPress, Shopify, or Webflow (`_deactivate_other_store_integration` in
integrations.views enforces this).
"""

from __future__ import annotations

from apps.integrations.models import Integration


def resolve_store_integration_for_run(organization, run_url: str = "") -> Integration | None:
    """
    Return the active WordPress, Shopify, or Webflow integration for this org.

    `run_url` is reserved for future URL-based checks; with a single active store it
    is unused.
    """
    _ = run_url  # kept for call-site compatibility
    return (
        Integration.objects.filter(
            organization=organization,
            is_active=True,
            provider__in=[
                Integration.Provider.SHOPIFY,
                Integration.Provider.WORDPRESS,
                Integration.Provider.WEBFLOW,
            ],
        )
        .order_by("-updated_at")
        .first()
    )
