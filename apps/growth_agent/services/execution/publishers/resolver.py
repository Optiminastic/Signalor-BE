"""Pick where a brand's owned content is published: WordPress, else GitHub (Next.js)."""

from __future__ import annotations

from apps.github_agent.models import GithubInstallation
from apps.integrations.models import Integration

from .base import Publisher
from .github import GithubPublisher
from .wordpress import WordPressPublisher

NOT_CONNECTED = "Connect WordPress or the Signalor GitHub App to publish content."


def connected_platform(organization) -> str | None:
    """Cheap check (no network): which publishing platform is connected, if any."""
    if _wordpress(organization):
        return "wordpress"
    if _github(organization):
        return "github"
    return None


def _wordpress(organization) -> Integration | None:
    return (
        Integration.objects.filter(
            organization=organization, provider=Integration.Provider.WORDPRESS, is_active=True
        )
        .order_by("-updated_at")
        .first()
    )


def _github(organization) -> GithubInstallation | None:
    return (
        GithubInstallation.objects.filter(organization=organization, is_active=True)
        .exclude(repo_full_name="")
        .order_by("-created_at")
        .first()
    )


def resolve_publisher(organization) -> Publisher | None:
    integration = _wordpress(organization)
    if integration:
        return WordPressPublisher(integration)
    installation = _github(organization)
    if installation:
        return GithubPublisher(installation)
    return None
