"""Per-account LLM spend accounting and budget enforcement.

A single analysis costs real money - measured between roughly $0.30 and $3
depending on how many prompts are tracked and whether the answer engines run
with web search. Nothing previously counted it, so an account on a £69.99 plan
could quietly outspend its own subscription by re-analysing on a loop.

This module is the meter and the fuse:

* **Meter** - ``record_run_cost`` denormalizes the exact per-call charge
  OpenRouter reports onto ``AnalysisRun.llm_cost_usd``, so spend can be summed
  per user, per organization and per month with a plain aggregate rather than by
  parsing a 128 KB JSON blob per run.
* **Fuse** - ``check_budget`` compares month-to-date spend against the plan's
  ``max_llm_spend_usd`` ceiling.

Two deliberate choices:

1. **Spend is measured, never estimated.** Every figure traces back to a real
   provider charge. A run whose cost we failed to capture contributes 0 rather
   than a guess, which errs toward letting work through rather than blocking a
   paying customer on a number we invented.
2. **The fuse fails open.** If the budget check itself errors, the run proceeds.
   Refusing to analyse because the accounting layer broke is a worse outcome
   than briefly overspending, and the overspend is visible in Langfuse either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger("apps")

# Rolling window for the ceiling. A calendar month resets on the 1st, which
# lets an account burn a full budget on the 31st and another on the 1st; a
# trailing 30-day window cannot be gamed that way.
WINDOW_DAYS = 30


@dataclass(frozen=True)
class BudgetStatus:
    allowed: bool
    spent_usd: float
    limit_usd: float
    email: str

    @property
    def remaining_usd(self) -> float:
        if self.limit_usd <= 0:
            return float("inf")
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def uncapped(self) -> bool:
        return self.limit_usd <= 0


def record_run_cost(run, logs: list[dict] | None = None) -> float:
    """Persist what this run spent. Returns the total in USD.

    Fail-soft: accounting must not break a completed analysis, so any error
    leaves the field at its default and is logged.
    """
    try:
        from apps.analyzer.pipeline.llm import summarize_llm_logs

        total = float(summarize_llm_logs(logs if logs is not None else (run.llm_logs or []))["total_cost_usd"])
        run.llm_cost_usd = total
        run.save(update_fields=["llm_cost_usd"])
        return total
    except Exception:
        logger.exception("Run %s: failed to record LLM cost", getattr(run, "id", "?"))
        return 0.0


def spend_since(email: str, days: int = WINDOW_DAYS) -> float:
    """Total USD this account spent on LLM calls in the trailing window."""
    from apps.analyzer.models import AnalysisRun

    account = (email or "").strip().lower()
    if not account:
        return 0.0
    since = timezone.now() - timedelta(days=days)
    total = (
        AnalysisRun.objects.filter(email__iexact=account, created_at__gte=since)
        .aggregate(total=Sum("llm_cost_usd"))
        .get("total")
    )
    return float(total or 0.0)


def limit_for(email: str) -> float:
    """The account's monthly ceiling in USD. ``0`` means uncapped."""
    try:
        from apps.accounts.subscription_utils import get_plan_limits

        return float(get_plan_limits(email).get("max_llm_spend_usd", 0.0) or 0.0)
    except Exception:
        logger.exception("Could not resolve spend limit for %s; treating as uncapped", email)
        return 0.0


def check_budget(email: str) -> BudgetStatus:
    """Whether this account may start more billable work right now.

    Fails open on any internal error - see rule 2 in the module docstring.
    """
    account = (email or "").strip().lower()
    try:
        limit = limit_for(account)
        spent = spend_since(account)
        allowed = limit <= 0 or spent < limit
        return BudgetStatus(allowed=allowed, spent_usd=spent, limit_usd=limit, email=account)
    except Exception:
        logger.exception("Budget check failed for %s; allowing the run", account)
        return BudgetStatus(allowed=True, spent_usd=0.0, limit_usd=0.0, email=account)


def top_spenders(days: int = WINDOW_DAYS, limit: int = 20) -> list[dict]:
    """Accounts ranked by spend in the window. For ops and margin review."""
    from apps.analyzer.models import AnalysisRun

    since = timezone.now() - timedelta(days=days)
    rows = (
        AnalysisRun.objects.filter(created_at__gte=since)
        .exclude(email="")
        .values("email")
        .annotate(spent=Sum("llm_cost_usd"))
        .order_by("-spent")[:limit]
    )
    return [{"email": r["email"], "spent_usd": round(float(r["spent"] or 0.0), 4)} for r in rows]
