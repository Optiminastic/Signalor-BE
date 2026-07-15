"""Run prompt evaluations (Epic 6).

    python manage.py eval_prompts                 # all golden cases, recorded known-good
    python manage.py eval_prompts --prompt brand_prompts
    python manage.py eval_prompts --live          # generate fresh output via the LLM
    python manage.py eval_prompts --no-persist    # do not write PromptEvalLog rows

Exits non-zero if any case scores below its thresholds, so it can gate CI.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.analyzer.evals import runner


class Command(BaseCommand):
    help = "Run prompt evaluations against the golden dataset using the LLM-as-judge."

    def add_arguments(self, parser):
        parser.add_argument("--prompt", help="Only run cases for this prompt name.")
        parser.add_argument(
            "--pin", dest="pin", help="Only run cases pinned to this prompt version (e.g. v1)."
        )
        parser.add_argument("--live", action="store_true", help="Generate output live via the LLM.")
        parser.add_argument("--no-persist", action="store_true", help="Do not write PromptEvalLog rows.")

    def handle(self, *args, **opts):
        results = runner.run(
            prompt=opts.get("prompt"),
            version=opts.get("pin"),
            live=opts["live"],
            persist=not opts["no_persist"],
        )
        if not results:
            self.stdout.write("No matching eval cases.")
            return

        failed = 0
        for r in results:
            v = r.verdict
            if v is None:
                failed += 1
                self.stdout.write(f"[ERR ] {r.case_id} ({r.prompt}/{r.version}) - judge unavailable")
                continue
            if not r.passed:
                failed += 1
            label = "PASS" if r.passed else "FAIL"
            self.stdout.write(
                f"[{label}] {r.case_id} ({r.prompt}/{r.version}) "
                f"F={v.faithfulness:.2f} R={v.relevance:.2f} Fmt={v.format_score:.2f}"
            )

        total = len(results)
        self.stdout.write(f"\n{total - failed}/{total} passed.")
        if failed:
            raise CommandError(f"{failed} eval case(s) failed.")
