"""
Social posts (LinkedIn, X, Instagram): the agent drafts, the owner approves.

- ``prepare`` writes one post for the platform, within its length limit and
  grounded only in the brand's own facts. Nothing is posted.
- After approval the post waits (``send_blocker``) until the brand connects
  Buffer; meanwhile the owner can copy it and post it by hand, then mark it
  posted (``control/manual_post.py``) so it's never sent again.
- Once Buffer is connected, ``execute`` adds the approved post to the matching
  channel's Buffer queue (Buffer publishes it at the channel's next slot).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from core.llm.structured import ask_structured

from ...models import ActionVersion, GrowthAction, GrowthProgram, GrowthTask
from ..social import buffer
from ..social.posts import POSTED_PLATFORMS, platform_of
from .brand import brand_card, display_name
from .channel_base import ChannelOutcome, Prepared
from .writer import gather_knowledge

CONNECTOR = "buffer"
PURPOSE = "growth-social-post"
WRITER_TIER = "medium"
LIMITS = {"x": 280, "linkedin": 1300, "instagram": 2200}
BUFFER_NEEDED = "Connect Buffer to schedule approved posts, or copy the post and publish it yourself."
URL = re.compile(r"https?://\S+")

_STYLE = {
    "linkedin": "a LinkedIn post: a strong first line, 3 to 6 short paragraphs, a practical takeaway, at most 3 hashtags",
    "x": "one X post under 280 characters including hashtags: one clear point, at most 2 hashtags",
    "instagram": "an Instagram caption: a hook first, short lines, a call to action, 5 to 8 relevant hashtags at the end",
}


class SocialPost(BaseModel):
    text: str = Field(description="The full post text exactly as it should be published")


def _prompt(platform: str, brand: str, topic: str, knowledge: str, brand_url: str) -> str:
    return (
        f"Brand: {brand}\nWrite {_STYLE[platform]} about: {topic}\n\n"
        f"KNOWLEDGE (from the brand's own site):\n{knowledge or '(none available)'}\n\n"
        "Rules:\n"
        "- Use only facts about the brand from KNOWLEDGE and BRAND CONTEXT; leave out anything not there.\n"
        "- Never invent numbers, customers, awards or results. No claims about competitors.\n"
        "- Write like a person, not an ad: no hype words, no emoji walls.\n"
        f"- Stay within {LIMITS[platform]} characters.\n"
        f"- The only link allowed is {brand_url or 'none'}, at most once."
    )


def review_notes(text: str, platform: str, brand_url: str) -> list[str]:
    """Deterministic checks shown to the owner with the draft. Pure."""
    notes = []
    if len(text) > LIMITS[platform]:
        notes.append(
            f"Longer than {POSTED_PLATFORMS[platform]}'s {LIMITS[platform]} characters; shorten it before posting."
        )
    brand_host = (urlparse(brand_url).netloc or "").lower()
    if any((urlparse(u).netloc or "").lower() != brand_host for u in URL.findall(text)):
        notes.append("Contains a link to another site; check it before approving.")
    if platform == "instagram":
        notes.append("Instagram posts need an image; add one when you post it.")
    return notes


class OwnedSocialChannel:
    """Drafting needs nothing connected; sending needs Buffer (``send_blocker``)."""

    def readiness(self, program: GrowthProgram) -> str:
        return ""

    def send_blocker(self, program: GrowthProgram) -> str:
        return "" if buffer.connection(program.organization) else BUFFER_NEEDED

    def prepare(self, task: GrowthTask) -> Prepared | ChannelOutcome:
        platform = platform_of(task.target_url)
        if platform not in LIMITS:
            return ChannelOutcome(
                False, "Not a platform the agent posts to.", retryable=False, external_call_made=False
            )
        org = task.cycle.program.organization
        prompt = _prompt(platform, display_name(org), task.target, gather_knowledge(task), org.url or "")
        post = ask_structured(
            prompt,
            SocialPost,
            system=brand_card(org) or None,
            tier=WRITER_TIER,
            max_tokens=900,
            temperature=0.4,
            purpose=PURPOSE,
        )
        if post is None or not post.text.strip():
            return ChannelOutcome(False, "The writer model did not return a post.", external_call_made=False)
        text = post.text.strip()
        return Prepared(
            kind=GrowthAction.Kind.PUBLISH_SOCIAL_POST,
            connector=CONNECTOR,
            payload={"platform": platform, "text": text},
            destination={"platform": platform, "profile_url": task.target_url},
            preview={"platform": platform, "text": text},
            review_notes=tuple(review_notes(text, platform, org.url or "")),
        )

    def execute(self, task: GrowthTask, version: ActionVersion) -> ChannelOutcome:
        """Add the approved post to the matching channel's Buffer queue."""
        org = task.cycle.program.organization
        integration = buffer.connection(org)
        if integration is None:
            return ChannelOutcome(False, BUFFER_NEEDED, external_call_made=False)
        platform = version.payload["platform"]
        channel = buffer.channel_for(buffer.saved_channels(org), platform) or buffer.channel_for(
            buffer.save_channels(org), platform
        )
        if channel is None:
            reason = f"Your Buffer has no {POSTED_PLATFORMS[platform]} channel. Add it in Buffer, or post it yourself."
            return ChannelOutcome(False, reason, retryable=False, external_call_made=False)
        answer = buffer.add_to_queue(integration, channel["id"], version.payload["text"])
        evidence = {"platform": platform, "buffer_channel": channel["name"], "buffer_answer": answer}
        return ChannelOutcome(True, "Added to your Buffer queue.", payload=evidence)
