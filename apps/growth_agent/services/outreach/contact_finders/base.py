"""The shape every contact finder returns."""

from __future__ import annotations

from dataclasses import dataclass

# Roles that usually decide on an editorial mention or a listicle inclusion.
PITCH_TITLES = ("editor", "content", "marketing", "communications", "pr", "writer", "journalist", "seo")
TIMEOUT_SECONDS = 15


class FinderError(Exception):
    """The finder couldn't answer: bad key, no credits, rate limit, network."""


@dataclass(frozen=True)
class Candidate:
    """A person a finder suggests contacting.

    ``ref`` identifies them to the finder (Hunter: the email; Apollo: the
    person id). ``email`` is empty when the finder only reveals it on request.
    """

    provider: str
    ref: str
    name: str
    title: str
    email: str
    confidence: str

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ref": self.ref,
            "name": self.name,
            "title": self.title,
            "email": self.email,
            "confidence": self.confidence,
        }
