"""
Golden-set evaluation harness.

Runs the LLM-as-judge (``pipeline.judge``) over a fixtures file and prints per-row
scores plus an aggregate report, so prompt/retrieval changes can be measured
instead of eyeballed. This is the explicit, opt-in counterpart to inline sampling —
it judges every row regardless of ``LLM_JUDGE_ENABLED``.

Fixtures file: a JSON list of rows. Each row is one of:
  - {"name", "task", "output", "context"?, "reference"?}   → judge the given output
  - {"name", "task", "prompt", "context"?, "reference"?}   → generate via LLM, then judge

Usage:
  python manage.py eval_golden                        # default fixtures
  python manage.py eval_golden --file path/to/set.json
  python manage.py eval_golden --tier strong --json
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.analyzer.pipeline.judge import judge_output
from apps.analyzer.pipeline.llm import ask_llm

_DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "eval" / "golden_set.json"


class Command(BaseCommand):
    help = "Run the LLM-as-judge over a golden fixtures file and report scores."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(_DEFAULT_FIXTURES), help="Path to the fixtures JSON.")
        parser.add_argument("--tier", default="strong", help="Judge model tier (cheap/medium/strong).")
        parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")

    def handle(self, *args, **opts):
        rows = self._load(opts["file"])
        tier = opts["tier"]

        results = []
        for i, row in enumerate(rows):
            name = row.get("name") or f"row-{i + 1}"
            task = row.get("task", "")
            output = row.get("output")
            if not output and row.get("prompt"):
                output = ask_llm(row["prompt"], tier=tier, purpose=f"eval:{name}")
            verdict = judge_output(
                task,
                output or "",
                context=row.get("context", ""),
                reference=row.get("reference", ""),
                tier=tier,
                purpose=f"judge:{name}",
            )
            results.append({"name": name, "verdict": verdict})

        report = self._summarize(results)
        if opts["json"]:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._print(results, report)

    def _load(self, path: str) -> list[dict]:
        p = Path(path)
        if not p.exists():
            raise CommandError(f"Fixtures file not found: {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {p}: {exc}") from exc
        if not isinstance(data, list):
            raise CommandError("Fixtures file must be a JSON list of rows.")
        return data

    def _summarize(self, results: list[dict]) -> dict:
        judged = [r["verdict"] for r in results if r["verdict"] is not None]
        n = len(judged)
        if not n:
            return {"rows": len(results), "judged": 0, "pass_rate": None, "averages": None}

        def _avg(attr):
            return round(sum(getattr(v, attr) for v in judged) / n, 2)

        return {
            "rows": len(results),
            "judged": n,
            "unscored": len(results) - n,
            "pass_rate": round(sum(1 for v in judged if v.passed) / n, 2),
            "averages": {
                "relevance": _avg("relevance"),
                "faithfulness": _avg("faithfulness"),
                "format_quality": _avg("format_quality"),
                "overall": round(sum(v.average for v in judged) / n, 2),
            },
        }

    def _print(self, results: list[dict], report: dict) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Golden-set evaluation"))
        for r in results:
            v = r["verdict"]
            if v is None:
                self.stdout.write(f"  {r['name']:<28} UNSCORED (judge failed)")
                continue
            flag = self.style.SUCCESS("PASS") if v.passed else self.style.ERROR("FAIL")
            self.stdout.write(
                f"  {r['name']:<28} {flag}  "
                f"rel={v.relevance} faith={v.faithfulness} fmt={v.format_quality} "
                f"avg={v.average}  {v.notes[:80]}"
            )
        self.stdout.write("")
        if report.get("averages"):
            a = report["averages"]
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"Judged {report['judged']}/{report['rows']} | "
                    f"pass rate {report['pass_rate']:.0%} | "
                    f"avg rel {a['relevance']} faith {a['faithfulness']} "
                    f"fmt {a['format_quality']} overall {a['overall']}"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("No rows could be judged."))
