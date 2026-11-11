"""Channel port shared by every executable channel, the executor and the dispatcher.

A channel works in two separate steps:

- ``prepare`` builds the exact thing to send (and where), with no outside effect.
  Nothing leaves Signalor here.
- ``execute`` performs one approved draft, using only what was stored when it
  was prepared. It never regenerates or alters content.

The brand owner approves in between, so what they saw is what gets sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ...models import GrowthProgram, GrowthTask

if TYPE_CHECKING:
    from ...models import ActionVersion


@dataclass(frozen=True)
class ChannelOutcome:
    succeeded: bool
    reason: str = ""
    evidence_url: str = ""
    payload: dict = field(default_factory=dict)
    dry_run: bool = False
    retryable: bool = True  # False when trying again tomorrow cannot help (no contact, opted out)
    # False only when a failure is certain to have happened BEFORE any outside
    # call. Then nothing can have been sent. Left True whenever a send or a
    # publish may have gone out, so the result is never assumed to be "not sent".
    external_call_made: bool = True


@dataclass(frozen=True)
class Prepared:
    """A draft ready for approval: what will be sent, and where."""

    kind: str
    connector: str
    payload: dict
    destination: dict
    # What a person reviewing the draft is shown. Derived from the payload.
    preview: dict = field(default_factory=dict)


class Channel(Protocol):
    def readiness(self, program: GrowthProgram) -> str: ...  # "" when ready, else why not

    def prepare(self, task: GrowthTask) -> Prepared | ChannelOutcome: ...  # ChannelOutcome only on failure

    def execute(self, task: GrowthTask, version: ActionVersion) -> ChannelOutcome: ...
