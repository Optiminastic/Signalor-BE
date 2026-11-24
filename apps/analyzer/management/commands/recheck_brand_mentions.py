"""
Re-read stored AI answers with the current brand matcher and mark the ones that
do name the brand.

Run it after the matcher learns a new spelling (for example a one-word brand
written as two words, "Scrnstudio" as "SCRN STUDIO"): answers stored before
the fix keep "not mentioned" until something re-reads them.

Only ever turns "not mentioned" into "mentioned". It never un-marks an answer,
asks no AI engine again, and changes nothing without ``--apply``.

  python manage.py recheck_brand_mentions --org <brand slug>           # dry run
  python manage.py recheck_brand_mentions --org <brand slug> --apply
  python manage.py recheck_brand_mentions --all --apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.analyzer.models import AnalysisRun, PromptResult
from apps.analyzer.pipeline.ai_visibility import _build_brand_aliases, _match_brand


def aliases_for(run: AnalysisRun) -> list[str]:
    """The run's brand aliases, from its detected brand name and the brand's own name."""
    names = {n.strip() for n in (run.brand_name, run.organization.name if run.organization else "") if n}
    found: set[str] = set()
    for name in names:
        found.update(_build_brand_aliases(name, run.url or ""))
    return sorted(found)


def newly_mentioned(run: AnalysisRun) -> list[PromptResult]:
    """Stored answers for ``run`` marked not mentioned that do name the brand."""
    aliases = aliases_for(run)
    if not aliases:
        return []
    missed = PromptResult.objects.filter(prompt_track__analysis_run=run, brand_mentioned=False).exclude(
        response_text=""
    )
    return [r for r in missed if _match_brand(aliases, r.response_text)[0]]


class Command(BaseCommand):
    help = "Mark stored AI answers that name the brand but were stored as not mentioned."

    def add_arguments(self, parser):
        parser.add_argument("--org", help="Brand slug (the slug in the dashboard URL).")
        parser.add_argument("--all", action="store_true", help="Every brand's analyses.")
        parser.add_argument("--apply", action="store_true", help="Save the changes (default: dry run).")

    def handle(self, *args, **options):
        if not options["org"] and not options["all"]:
            raise CommandError("Pass --org <brand slug> or --all.")
        runs = AnalysisRun.objects.select_related("organization").exclude(organization=None)
        if options["org"]:
            runs = runs.filter(organization__slug=options["org"])
        total = 0
        for run in runs.order_by("id"):
            found = newly_mentioned(run)
            if not found:
                continue
            total += len(found)
            self.stdout.write(f"run {run.id} ({run.organization.slug}): {len(found)} answers name the brand")
            if options["apply"]:
                for result in found:
                    result.brand_mentioned = True
                    result.save(update_fields=["brand_mentioned"])
        verb = "Marked" if options["apply"] else "Would mark"
        self.stdout.write(f"{verb} {total} answers as mentioning the brand.")
