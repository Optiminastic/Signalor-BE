"""
Executable channels. A channel is ready when its platform is connected.
Preparing turns one task into one draft; executing turns one approved draft
into one real-world action.

Only channels registered in ``REGISTRY`` ever run; tasks for other channels
simply wait until their channel ships.
"""

from __future__ import annotations

from ...models import ActionVersion, GrowthAction, GrowthProgram, GrowthTask
from ..outreach.channel import OutreachEmailChannel
from .article import ArticleDraft, render_markdown, slugify
from .brand import display_name
from .channel_base import Channel, ChannelOutcome, Prepared
from .publishers.base import PublishError
from .publishers.resolver import NOT_CONNECTED, connected_platform, resolve_publisher
from .safety import check_article
from .satellite_channel import SatelliteContentChannel
from .writer import gather_knowledge, write_article

PLATFORM_CHANGED = "The publishing platform changed after this draft was approved."


class OwnedContentChannel:
    """Answer-first and comparison pages, published to the brand's own site."""

    def readiness(self, program: GrowthProgram) -> str:
        return "" if connected_platform(program.organization) else NOT_CONNECTED

    def prepare(self, task: GrowthTask) -> Prepared | ChannelOutcome:
        org = task.cycle.program.organization
        knowledge = gather_knowledge(task)
        article = write_article(task, knowledge)
        if article is None:
            return ChannelOutcome(
                False, "The writer model did not return an article.", external_call_made=False
            )
        verdict = check_article(article, display_name(org), knowledge)
        if not verdict.passed:
            return ChannelOutcome(
                False,
                "Blocked by brand-safety check.",
                payload={"issues": list(verdict.issues)},
                external_call_made=False,
            )
        slug = slugify(article.title)
        return Prepared(
            kind=GrowthAction.Kind.PUBLISH_ARTICLE,
            connector=connected_platform(org) or "",
            payload={"article": article.model_dump(), "slug": slug},
            destination={"platform": connected_platform(org) or ""},
            preview={"title": article.title, "slug": slug, "markdown": render_markdown(article, {})},
        )

    def execute(self, task: GrowthTask, version: ActionVersion) -> ChannelOutcome:
        org = task.cycle.program.organization
        publisher = resolve_publisher(org)
        if publisher is None:
            return ChannelOutcome(False, NOT_CONNECTED, external_call_made=False)
        if publisher.name != version.destination.get("platform"):
            return ChannelOutcome(False, PLATFORM_CHANGED, retryable=False, external_call_made=False)
        article = ArticleDraft.model_validate(version.payload["article"])
        slug = version.payload["slug"]
        try:
            result = publisher.publish(article, slug)
        except PublishError as exc:
            # The publisher may have got part of the way. Never assume nothing went out.
            return ChannelOutcome(False, str(exc), payload={"title": article.title})
        return ChannelOutcome(
            True,
            evidence_url=result.url,
            payload={
                "title": article.title,
                "slug": slug,
                "platform": publisher.name,
                "reference": result.reference,
            },
        )


REGISTRY: dict[str, Channel] = {
    "owned_content": OwnedContentChannel(),
    "outreach_email": OutreachEmailChannel(),
    "satellite_content": SatelliteContentChannel(),
}
