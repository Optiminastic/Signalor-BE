"""WordPress adapter, reusing Signalor's existing WordPress integration."""

from __future__ import annotations

from apps.integrations.services.wordpress import publish_wordpress_post

from ..article import ArticleDraft, render_html
from .base import PublishError, PublishResult


class WordPressPublisher:
    name = "wordpress"

    def __init__(self, integration):
        self.integration = integration

    def publish(self, article: ArticleDraft, slug: str) -> PublishResult:
        try:
            result = publish_wordpress_post(
                self.integration,
                title=article.title,
                content=render_html(article),
                excerpt=article.meta_description,
                status="publish",
                slug=slug,
            )
        except ValueError as exc:
            raise PublishError(str(exc)) from exc
        if not result.get("url"):
            raise PublishError("WordPress did not return a URL for the new post.")
        return PublishResult(url=result["url"], reference=str(result.get("id", "")))
