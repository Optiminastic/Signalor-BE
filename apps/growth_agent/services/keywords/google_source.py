"""
Keywords from Google results, for brands without keyword data (no DataForSEO and
no Semrush/Ahrefs key of their own).

Built from the weekly rank check (``rank_tracking``), which searches Google
(Serper) for each tracked keyword and records the brand's and its top
competitors' positions. Gives the Keywords tab:
- keywords the brand ranks for (top 100), best position first;
- keywords a competitor wins (top 10) where the brand isn't in the top 10.
There are no search volumes in this source; the tab says so.
"""

from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from ...models import GrowthProgram, KeywordSnapshot, RankObservation

SOURCE = "google"
TOP_POSITION = 10


def _latest_rows(program: GrowthProgram) -> list[RankObservation]:
    seo = RankObservation.objects.filter(program=program, kind=RankObservation.Kind.SEO)
    week = seo.order_by("-week").values_list("week", flat=True).first()
    return list(seo.filter(week=week)) if week else []


def build(program: GrowthProgram, players: list) -> tuple[list[dict], list[dict]]:
    """(brand rows, gap rows) from the latest week's rank rows. ``players``: brand first."""
    if not players:
        return [], []
    brand, rivals = players[0], {p.domain: p for p in players[1:]}
    positions: dict[str, dict[str, int]] = defaultdict(dict)
    for row in _latest_rows(program):
        if row.position:
            positions[row.keyword][row.domain] = row.position
    brand_rows, gap_rows = [], []
    for keyword, by_domain in positions.items():
        own = by_domain.get(brand.domain)
        if own:
            brand_rows.append({"keyword": keyword, "position": own, "search_volume": 0, "url": ""})
        winners = [
            {"name": rivals[d].name, "domain": d, "position": pos}
            for d, pos in sorted(by_domain.items(), key=lambda item: item[1])
            if d in rivals and pos <= TOP_POSITION
        ]
        if winners and (not own or own > TOP_POSITION):
            gap_rows.append({"keyword": keyword, "search_volume": 0, "rivals": winners, "new": False})
    brand_rows.sort(key=lambda r: r["position"])
    gap_rows.sort(key=lambda g: g["rivals"][0]["position"])
    return brand_rows, gap_rows


def store(program: GrowthProgram, players: list) -> bool:
    """Save this week's Google-based keyword snapshots. False when there's nothing to show."""
    brand_rows, gap_rows = build(program, players)
    if not brand_rows and not gap_rows:
        return False
    used = [{"name": p.name, "domain": p.domain} for p in players[1:]]
    with transaction.atomic():
        KeywordSnapshot.objects.create(
            program=program, kind=KeywordSnapshot.Kind.BRAND, rows=brand_rows, meta={"source": SOURCE}
        )
        KeywordSnapshot.objects.create(
            program=program, kind=KeywordSnapshot.Kind.GAPS, rows=gap_rows, meta={"competitors": used}
        )
    return True
