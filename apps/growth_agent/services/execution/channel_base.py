"""Channel port shared by every executable channel and the executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ...models import GrowthProgram, GrowthTask


@dataclass(frozen=True)
class ChannelOutcome:
    succeeded: bool
    reason: str = ""
    evidence_url: str = ""
    payload: dict = field(default_factory=dict)
    dry_run: bool = False
    retryable: bool = True  # False when trying again tomorrow cannot help (no contact, opted out)


class Channel(Protocol):
    def readiness(self, program: GrowthProgram) -> str: ...  # "" when ready, else why not

    def run(self, task: GrowthTask, mode: str) -> ChannelOutcome: ...
