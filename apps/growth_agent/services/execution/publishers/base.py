"""
Publisher port: where owned content goes live. One adapter per platform.

The channel depends only on this interface, so adding Webflow or Shopify later
never touches the executor or the content channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..article import ArticleDraft


@dataclass(frozen=True)
class PublishResult:
    url: str
    reference: str  # platform id: WordPress post id, merged PR number, ...


class PublishError(Exception):
    """Publishing failed; the message is safe to store and show."""


class Publisher(Protocol):
    name: str

    def publish(self, article: ArticleDraft, slug: str) -> PublishResult: ...
