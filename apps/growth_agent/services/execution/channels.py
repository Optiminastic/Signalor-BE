"""
Executable channels. A channel is ready when its platform is connected, and
running it turns one task into one real-world action.

Only channels registered in ``REGISTRY`` ever execute; tasks for other channels
simply wait until their channel ships (outreach email comes next).
"""

from __future__ import annotations

from ...models import ChannelMode, GrowthProgram, GrowthTask
from ..outreach.channel import OutreachEmailChannel
from .article import render_markdown, slugify
from .brand import display_name
from .channel_base import Channel, ChannelOutcome
from .publishers.base import PublishError
from .publishers.resolver import NOT_CONNECTED, connected_platform, resolve_publisher
from .safety import check_article
from .writer import gather_knowledge, write_article


class OwnedContentChannel:
    """Answer-first and comparison pages, published to the brand's own site."""

    def readiness(self, program: GrowthProgram) -> str:
        return "" if connected_platform(program.organization) else NOT_CONNECTED

    def run(self, task: GrowthTask, mode: str) -> ChannelOutcome:
        org = task.cycle.program.organization
        knowledge = gather_knowledge(task)
        article = write_article(task, knowledge)
        if article is None:
            return ChannelOutcome(False, "The writer model did not return an article.")
        verdict = check_article(article, display_name(org), knowledge)
        if not verdict.passed:
            return ChannelOutcome(
                False, "Blocked by brand-safety check.", payload={"issues": list(verdict.issues)}
            )
        slug = slugify(article.title)
        draft = {"title": article.title, "slug": slug, "markdown": render_markdown(article, {})}
        if mode == ChannelMode.DRY_RUN:
            return ChannelOutcome(True, payload=draft, dry_run=True)
        publisher = resolve_publisher(org)
        if publisher is None:
            return ChannelOutcome(False, NOT_CONNECTED)
        try:
            result = publisher.publish(article, slug)
        except PublishError as exc:
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
}
