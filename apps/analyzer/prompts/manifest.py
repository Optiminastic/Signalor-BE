"""Prompt version manifest (Epic 5).

Maps each registered prompt to its current version. ``render(name)`` uses the current
version unless a caller pins one with ``render(name, version=...)``. Bump the value here
(and add a new ``<name>/<vN>.j2`` file) to roll a prompt forward while keeping the old
version reproducible for evaluation.
"""

MANIFEST: dict[str, str] = {
    # Core generation (Epic 1-4 standardized path)
    "brand_prompts": "v1",
    "brand_synthesis": "v1",
    "brand_synthesis_system": "v1",
    # Auto-fix (apps/analyzer/auto_fix.py)
    "auto_fix_content": "v1",
    "auto_fix_meta": "v1",
    "auto_fix_llms_txt": "v1",
    # Structured data — ONE generator shared by auto_fix + geo_improvement (Epic 8
    # replaced the separate auto_fix_jsonld / geo_jsonld prompts with this).
    "jsonld": "v1",
    # GEO improvement (apps/analyzer/pipeline/geo_improvement.py)
    "geo_meta": "v1",
    "geo_product_desc": "v1",
    # Evaluation (Epic 6): the LLM-as-judge prompt
    "judge_eval": "v1",
    # Task enrichment (apps/analyzer/services/task_enrichment.py) — drafts concrete,
    # page-specific fix content for the top-ranked tasks.
    "task_enrich_faq": "v1",
    "task_enrich_citations": "v1",
    "task_enrich_rewrite": "v1",
    # Fallback drafter for the ~73 findings with no specialised template. Without
    # it those tasks ship the same static sentence to every customer.
    "task_enrich_generic": "v1",
    # Open-ended site audit (pipeline/site_findings.py). Produces findings the
    # 83 fixed rules cannot express, including cross-page inconsistency.
    "site_findings": "v1",
    # Paste-ready passage that makes one page answer one tracked prompt
    # (services/answer_block.py).
    "answer_block": "v1",
}
