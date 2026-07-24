"""Persistence layer for the Task Satisfaction Gate (Phase 2).

Keeps the pure deterministic engine in ``pipeline/satisfaction.py`` free of DB
I/O, and adds the cross-run memory around it:

- ``record_satisfied`` — upsert one (org, page, finding) → done row.
- ``recorded_satisfied`` — batch-read the ledger for a run's pages.
- ``apply_gate`` — the imperative shell: Tier 0 (ledger hit on an unchanged
  page) → Tier 1 (deterministic verifier) → write confirmations back.

"Done stays done": once a (page, finding) is recorded satisfied, a later run
whose page hash still matches suppresses the task instantly, without re-running
the verifier. A content change (hash differs) invalidates the entry, so a real
regression resurfaces.
"""

from __future__ import annotations

import logging

from apps.analyzer.pipeline.satisfaction import (
    SATISFACTION_VERIFIERS,
    PageSignals,
    _norm,
    filter_satisfied,
)

logger = logging.getLogger("apps")


def record_satisfied(
    *,
    organization_id: int | None,
    page_url: str,
    finding_code: str,
    content_hash: str,
    source: str = "heuristic",
    confidence: float = 1.0,
    evidence: dict | None = None,
) -> None:
    """Upsert a satisfied (org, page, finding) row. No-op without an org/page/code."""
    if not organization_id or not page_url or not finding_code:
        return
    from django.utils import timezone

    from apps.analyzer.models import TaskSatisfaction

    try:
        TaskSatisfaction.objects.update_or_create(
            organization_id=organization_id,
            page_url=_norm(page_url),
            finding_code=finding_code,
            defaults={
                "content_hash": content_hash or "",
                "source": source,
                "confidence": confidence,
                "evidence": evidence or {},
                "verified_at": timezone.now(),
            },
        )
    except Exception:
        logger.exception("record_satisfied failed (org=%s finding=%s)", organization_id, finding_code)


def recorded_satisfied(organization_id: int, page_urls: list[str]) -> dict[tuple[str, str], str]:
    """Return ``{(norm_url, finding_code): content_hash}`` for a run's pages — one query."""
    from apps.analyzer.models import TaskSatisfaction

    norm_urls = [_norm(u) for u in page_urls]
    rows = TaskSatisfaction.objects.filter(
        organization_id=organization_id, page_url__in=norm_urls
    ).values_list("page_url", "finding_code", "content_hash")
    return {(u, c): h for u, c, h in rows}


def _verify(code: str, page: PageSignals) -> bool:
    verifier = SATISFACTION_VERIFIERS.get(code)
    if verifier is None:
        return False
    try:
        return bool(verifier(page))
    except Exception:
        logger.exception("satisfaction verifier failed for %s", code)
        return False


def apply_gate(
    run, recs: list[dict], page_signals: dict[str, PageSignals]
) -> tuple[list[dict], list[dict]]:
    """Filter already-done tasks using the ledger (Tier 0) then the deterministic
    verifiers (Tier 1), recording new confirmations. Returns (kept, suppressed).

    Falls back to the pure deterministic filter for anonymous runs (no org → no
    persistent ledger).
    """
    org_id = getattr(run, "organization_id", None)
    keyed = {_norm(u): ps for u, ps in page_signals.items()}
    if not org_id:
        return filter_satisfied(recs, page_signals)

    ledger = recorded_satisfied(org_id, list(keyed.keys()))
    to_record: dict[tuple[str, str], tuple[str, str]] = {}  # (url, code) -> (page_url, hash)
    kept: list[dict] = []
    suppressed: list[dict] = []

    for rec in recs:
        code = rec.get("finding_code", "")
        affected = [(u, keyed[u]) for u in {_norm(x) for x in rec.get("affected_pages") or []} if u in keyed]
        if not affected:
            kept.append(rec)
            continue

        all_satisfied = True
        for url, ps in affected:
            # Tier 0 — a prior confirmation on an unchanged page suppresses instantly.
            if ps.content_hash and ledger.get((url, code)) == ps.content_hash:
                continue
            # Tier 1 — deterministic multi-signal check; record fresh confirmations.
            if _verify(code, ps):
                to_record[(url, code)] = (ps.url, ps.content_hash)
                continue
            all_satisfied = False
            break

        (suppressed if all_satisfied else kept).append(rec)

    for (_url, code), (page_url, chash) in to_record.items():
        record_satisfied(
            organization_id=org_id,
            page_url=page_url,
            finding_code=code,
            content_hash=chash,
            source="heuristic",
        )

    return kept, suppressed
