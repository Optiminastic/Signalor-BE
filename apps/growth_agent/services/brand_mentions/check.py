"""
Weekly brand mentions for the brand and its top competitors: what the internet
said about each of them this past week, kept per week for the Brand tab.

A page already stored for a player is never stored twice (unique per player and
URL), so a re-run in the same week only adds new pages.
"""

from __future__ import annotations

from django.db import transaction

from ...models import BrandMention, GrowthProgram
from ..keywords.gaps import Rival
from . import classify, search

MAX_PER_PLAYER = 25


def _mentions(program: GrowthProgram, week, player: Rival) -> list[BrandMention]:
    candidates = search.find(player.name, player.domain)
    known = set(
        BrandMention.objects.filter(program=program, domain=player.domain).values_list("url", flat=True)
    )
    fresh = [c for c in candidates if c.url not in known][:MAX_PER_PLAYER]
    return [
        BrandMention(
            program=program,
            week=week,
            domain=player.domain,
            url=c.url,
            title=c.title,
            snippet=c.snippet,
            source=c.source,
            channel=c.channel,
            published=c.published,
            sentiment=sentiment,
        )
        for c, sentiment in classify.classify(player.name, player.domain, fresh)
    ]


def run(program: GrowthProgram, players: list[Rival], week) -> int:
    """Find and store this week's mentions for every player. Returns how many were added."""
    rows: list[BrandMention] = []
    for player in players:
        if player.name and player.domain:
            rows += _mentions(program, week, player)
    with transaction.atomic():
        BrandMention.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)
