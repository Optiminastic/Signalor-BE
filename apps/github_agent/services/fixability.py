"""
Upfront AI fixability triage.

One cheap LLM call labels each finding as code-fixable or manual (with a short
reason), so the Fixes page can show an AI-decided status BEFORE the user clicks.
Off-page / infra findings are decided deterministically (no LLM). The deeper,
repo-aware check still happens in the fix agent at click time — this is the fast
pre-filter, cached per run so it doesn't re-run on status polls.
"""

import hashlib
import json
import logging
import re

from apps.analyzer._cache import cached_or_compute
from apps.analyzer.pipeline.llm import ask_llm

from .fixable import NOT_CODE_FIXABLE

logger = logging.getLogger("apps")

_TTL = 1800  # 30 min — findings don't change between status polls
_MAX_FINDINGS = 60


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _classify(framework: str, findings: list[dict]) -> dict:
    lines = [
        f"- {f['finding_code']}: {f.get('title', '')} — {(f.get('description') or '')[:240]}"
        for f in findings
    ]
    prompt = (
        f"You triage SEO/GEO findings for a {framework} website to decide which can be FIXED BY "
        "EDITING THE SITE'S CODE/CONTENT in a pull request, vs. which need real-world information "
        "an AI must NOT fabricate.\n\n"
        "fixable=true: markup, JSON-LD/schema, metadata, canonical/robots/sitemap, headings, "
        "components, internal links, about/contact pages, wiring up content that already exists.\n"
        "fixable=false: it would require facts you cannot invent — real expert quotes, real "
        "statistics/research numbers, real external citations, genuine first-hand experience, or "
        "off-site/infrastructure work (backlinks, Wikipedia, social, HTTPS, hosting).\n\n"
        'Return ONLY JSON mapping each code to {"fixable": true|false, "reason": "<=12 words"}.\n\n'
        "Findings:\n" + "\n".join(lines)
    )
    text = ask_llm(prompt, max_tokens=1500, temperature=0.0, purpose="github.fixability")
    data = _extract_json(text)

    out: dict[str, dict] = {}
    for f in findings:
        code = f["finding_code"]
        v = data.get(code)
        if isinstance(v, dict) and "fixable" in v:
            out[code] = {"fixable": bool(v["fixable"]), "reason": str(v.get("reason", ""))[:160]}
        else:
            # Missing / unparseable → let the agent still get a chance to try.
            out[code] = {"fixable": True, "reason": ""}
    return out


def classify_fixability(slug: str, profile: dict, findings: list[dict]) -> dict:
    """Return ``{code: {"fixable": bool, "reason": str}}`` for the run's findings.

    Off-page/infra are decided without an LLM; the rest go through one cached call.
    """
    verdicts: dict[str, dict] = {}
    to_classify: list[dict] = []
    for f in findings:
        code = f.get("finding_code")
        if not code:
            continue
        if code in NOT_CODE_FIXABLE:
            verdicts[code] = {
                "fixable": False,
                "reason": "Off-site or infrastructure — not a code change.",
            }
        else:
            to_classify.append(f)

    if not to_classify:
        return verdicts

    to_classify = to_classify[:_MAX_FINDINGS]
    framework = (profile or {}).get("framework") or "a website"
    codes_key = ",".join(sorted(f["finding_code"] for f in to_classify))
    digest = hashlib.md5(f"{framework}|{codes_key}".encode()).hexdigest()[:12]
    key = f"gh-fixability:{slug}:{digest}"
    try:
        classified = cached_or_compute(key, _TTL, lambda: _classify(framework, to_classify))
    except Exception as exc:  # noqa: BLE001
        logger.warning("fixability classify failed for %s: %s", slug, exc)
        classified = {f["finding_code"]: {"fixable": True, "reason": ""} for f in to_classify}
    verdicts.update(classified)
    return verdicts
