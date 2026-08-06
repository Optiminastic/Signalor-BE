"""The outreach benchmark — a deliberately small analysis, built to be pasted
into a cold email.

Sales outreach needs three things, and only three: the buyer prompts a prospect
is losing, who AI engines recommend instead, and a short list of what to do.
The full pipeline answers far more than that, takes 10-20 minutes, and costs
$0.30-$3 a run because it fans every prompt across seven answer engines with
web search on. Most of that work never reaches the email.

So this profile keeps the one part that cannot be faked — really asking answer
engines the question and recording what they say — and drops everything else.
Three engines instead of seven and six prompts instead of ten put a run at
roughly $0.20, which is affordable per prospect.

The measurement stays real on purpose. A cheap model writes the prompts and the
opportunity text, but nothing here ever *predicts* what an engine would answer:
a fabricated benchmark is worse than none, because it is sent to a named person
who can check it in thirty seconds.
"""

from __future__ import annotations

import logging

from ..models import AnalysisRun, PromptTrack

logger = logging.getLogger("apps")

# Plan-engine ids (see prompt_tracker._providers_and_search_from_plan_engines).
# The three a buyer would actually name, and the three cheapest per call that
# still carry web search.
OUTREACH_ENGINES = ["chatgpt", "claude", "perplexity"]

# Six is enough to show a pattern without paying for ten.
OUTREACH_PROMPT_COUNT = 6

# Homepage only. Extra pages sharpen the generated prompts slightly and cost a
# crawl each; for an outreach benchmark that trade is not worth it.
OUTREACH_MAX_PAGES = 1

_MAX_OPPORTUNITIES = 5


def _brand_and_industry(crawl, run: AnalysisRun) -> tuple[str, str]:
    """Brand label and industry for prompt generation, from the homepage crawl."""
    from ..pipeline.aggregator import detect_industry
    from ..pipeline.utils import extract_brand_name

    brand = (run.brand_name or "").strip()
    if not brand:
        try:
            brand = extract_brand_name(crawl.soup, crawl.url)
        except Exception:
            logger.warning("outreach: brand extraction failed for %s", run.url, exc_info=True)
    try:
        industry = detect_industry(crawl.soup, crawl.text)
    except Exception:
        logger.warning("outreach: industry detection failed for %s", run.url, exc_info=True)
        industry = ""
    return brand or run.url, industry


def _prompts_for(run: AnalysisRun, crawl, brand: str, industry: str) -> list[str]:
    """Pinned prompts if the founder supplied them, else generated ones.

    ``onboarding_prompts`` is honoured verbatim so a founder who knows the
    buyer's language can override the model — which is the better report, and
    the only way two runs of the same domain measure the same questions.
    """
    pinned = [p.strip() for p in (run.onboarding_prompts or []) if isinstance(p, str) and p.strip()]
    if pinned:
        return pinned[:OUTREACH_PROMPT_COUNT]

    from ..pipeline.prompt_tracker import generate_brand_prompts

    meta_description = ""
    if crawl.soup:
        tag = crawl.soup.find("meta", attrs={"name": "description"})
        if tag and tag.get("content"):
            meta_description = tag["content"].strip()

    try:
        return generate_brand_prompts(
            brand_name=brand,
            brand_url=run.url,
            industry=industry,
            page_content=(crawl.text or "")[:2500],
            meta_description=meta_description,
            country=(run.country or "").strip(),
            count=OUTREACH_PROMPT_COUNT,
        )[:OUTREACH_PROMPT_COUNT]
    except Exception:
        logger.warning("outreach: prompt generation failed for %s", run.url, exc_info=True)
        return []


def _measure(run: AnalysisRun, prompts: list[str], brand: str) -> None:
    """Fire each prompt across the outreach engines and persist the results.

    Sequential, not pooled: six prompts across three engines is well inside a
    worker's budget, and ``fire_prompt_across_engines`` already fans out across
    engines internally. A failing prompt is skipped rather than aborting the
    run — five measured prompts still make a usable benchmark.
    """
    from ..pipeline.citations import competitor_hosts_for_run, host_of, persist_prompt_result
    from ..pipeline.prompt_tracker import classify_prompt_intent_and_type, fire_prompt_across_engines
    from ..tasks import progress

    brand_host = host_of(run.url)
    rival_hosts = competitor_hosts_for_run(run)
    total = len(prompts)

    for done, prompt_text in enumerate(prompts, start=1):
        try:
            intent, prompt_type = classify_prompt_intent_and_type(prompt_text, brand, run.url)
            results = fire_prompt_across_engines(
                prompt_text,
                brand,
                run.url,
                runs=1,
                allowed_engines=OUTREACH_ENGINES,
            )
            track = PromptTrack.objects.create(
                analysis_run=run,
                prompt_text=prompt_text,
                is_custom=False,
                intent=intent,
                prompt_type=prompt_type,
            )
            for result in results:
                persist_prompt_result(track, result, brand_host, rival_hosts)
        except Exception:
            logger.warning("outreach: prompt failed for run %s", run.id, exc_info=True)

        progress._update_sub_progress(run, 20, 85, done, total, "Asking AI engines your buyer prompts")


def _findings(run: AnalysisRun) -> dict:
    """Roll the persisted results up into the shape the email needs.

    Reuses the export's row builder so the PDF, the API and the founder's
    copy-paste all describe the same run the same way — including its
    "not measured" honesty about engines that returned nothing.
    """
    from ..views.runs import _prompt_benchmark_rows

    rows = _prompt_benchmark_rows(run)
    measured = [row for row in rows if row["measured"]]
    lost = [row for row in measured if row["mentions"] == 0]

    # Rank cited domains by how many distinct prompts they appear on: a source
    # winning five of six answers matters more than one winning a single answer.
    frequency: dict[str, int] = {}
    for row in measured:
        for domain in row["cited_domains"]:
            frequency[domain] = frequency.get(domain, 0) + 1
    competitors = sorted(frequency.items(), key=lambda kv: (-kv[1], kv[0]))

    return {
        "prompts": rows,
        "prompts_total": len(rows),
        "prompts_measured": len(measured),
        "prompts_lost": len(lost),
        "competitors": [{"domain": d, "prompts": n} for d, n in competitors[:8]],
    }


def _opportunities(brand: str, industry: str, findings: dict) -> list[str]:
    """Short, specific next steps, grounded strictly in what was measured.

    The prompt carries only observed facts, and the model is told to work from
    them. It writes the recommendation; it does not supply the evidence.
    """
    if not findings["prompts_measured"]:
        return []

    from core.llm.client import ask_llm

    lost = [row["prompt"] for row in findings["prompts"] if row["measured"] and not row["mentions"]]
    rivals = ", ".join(c["domain"] for c in findings["competitors"][:5]) or "no recurring sources"

    prompt = (
        f"Brand: {brand}\n"
        f"Industry: {industry or 'unknown'}\n"
        f"Buyer prompts where the brand is absent from AI answers:\n"
        + "\n".join(f"- {p}" for p in lost[:6])
        + f"\nSources AI engines cite instead: {rivals}\n\n"
        f"Write {_MAX_OPPORTUNITIES} specific actions this brand should take to get cited "
        "for those prompts. Use only the facts above; invent no metrics, traffic numbers "
        "or claims about the brand. One sentence each, plain text, one per line, no "
        "numbering and no preamble."
    )

    try:
        text = ask_llm(prompt, tier="cheap", purpose="Outreach Benchmark Opportunities", max_tokens=500)
    except Exception:
        logger.warning("outreach: opportunity generation failed", exc_info=True)
        return []

    lines = [line.strip(" -*\t") for line in (text or "").splitlines()]
    return [line for line in lines if len(line) > 20][:_MAX_OPPORTUNITIES]


def run_outreach_benchmark(run_id: int) -> None:
    """Build the outreach benchmark for ``run_id``. Never raises."""
    from ..pipeline.crawler import crawl_site
    from ..tasks import progress

    try:
        run = AnalysisRun.objects.get(pk=run_id)
    except AnalysisRun.DoesNotExist:
        logger.error("outreach: run %s not found", run_id)
        return

    try:
        progress._update_status(run, AnalysisRun.Status.CRAWLING, 5, "Reading the homepage")
        crawl, _site_map, _extra = crawl_site(run.url, max_pages=OUTREACH_MAX_PAGES)
        if not crawl.ok:
            run.status = AnalysisRun.Status.FAILED
            run.error_message = crawl.error or "Could not read that site."
            run.save(update_fields=["status", "error_message", "updated_at"])
            return

        brand, industry = _brand_and_industry(crawl, run)
        run.brand_name = run.brand_name or brand

        progress._update_status(run, AnalysisRun.Status.ANALYZING, 15, "Writing buyer prompts")
        prompts = _prompts_for(run, crawl, brand, industry)
        if not prompts:
            run.status = AnalysisRun.Status.FAILED
            run.error_message = "Could not work out what buyers ask about this site."
            run.save(update_fields=["status", "error_message", "updated_at"])
            return

        _measure(run, prompts, brand)

        progress._update_status(run, AnalysisRun.Status.SCORING, 88, "Writing the summary")
        findings = _findings(run)
        findings["brand"] = brand
        findings["industry"] = industry
        findings["url"] = run.url
        findings["opportunities"] = _opportunities(brand, industry, findings)

        run.outreach_report = findings
        run.status = AnalysisRun.Status.COMPLETE
        run.progress = 100
        run.phase = ""
        run.error_message = ""
        run.save()
        logger.info(
            "outreach benchmark complete for run %s: %s/%s prompts measured, %s lost",
            run_id,
            findings["prompts_measured"],
            findings["prompts_total"],
            findings["prompts_lost"],
        )
    except Exception as exc:
        logger.exception("outreach benchmark failed for run %s", run_id)
        run.status = AnalysisRun.Status.FAILED
        run.error_message = str(exc)[:500]
        run.save(update_fields=["status", "error_message", "updated_at"])
