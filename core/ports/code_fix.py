"""Port: can the code agent fix this finding in-repo?

``RecommendationSerializer.get_code_fixable`` called
``github_agent.services.fixable.is_agent_fixable`` directly, which was the whole
``analyzer <-> github_agent`` cycle. It also pointed the wrong way: the GitHub
agent consumes analyzer findings, so it sits above analyzer, and nothing below
may import it.

Whether a finding is code-fixable is genuinely the agent's knowledge, not the
analyzer's, so this stays a question rather than moving the logic down.

Unregistered answers False: with no agent installed nothing is offered as an
in-repo fix, which is exactly the correct behaviour for a deployment without the
GitHub integration.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger("apps")


class CodeFixCapability(Protocol):
    def is_agent_fixable(self, finding_code: str) -> bool:
        """Whether the agent should OFFER to fix this finding code."""
        ...


_capability: CodeFixCapability | None = None


def register(capability: CodeFixCapability) -> None:
    global _capability
    _capability = capability


def reset() -> None:
    """Drop the adapter. For tests exercising the unregistered path."""
    global _capability
    _capability = None


def is_registered() -> bool:
    return _capability is not None


def is_agent_fixable(finding_code: str) -> bool:
    """False when unregistered or on error — never offer a fix we cannot make."""
    if _capability is None or not finding_code:
        return False
    try:
        return bool(_capability.is_agent_fixable(finding_code))
    except Exception:
        logger.warning("code-fix capability check failed", exc_info=True)
        return False
