"""Analyzer background work, split by concern.

Was a single 1,607-line module. Everything is re-exported here - including
the original import header - so ``apps.analyzer.tasks.<name>`` resolves as
before. Cross-module calls inside the package are module-qualified so a
``patch.object`` on the owning module is honoured.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone

from core.llm.client import get_collected_logs, start_log_collection

from ..models import (
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    Competitor,
    PageScore,
    PromptTrack,
    Recommendation,
)
from ..pipeline.aggregator import compute_composite, compute_static_composite, detect_industry
from ..pipeline.ai_visibility import score_ai_visibility
from ..pipeline.brand_naming import visibility_brand_label
from ..pipeline.brand_visibility import run_brand_visibility
from ..pipeline.competitors import discover_competitors
from ..pipeline.content import score_content
from ..pipeline.crawler import CrawlResult, crawl_page
from ..pipeline.eeat import score_eeat
from ..pipeline.entity import score_entity
from ..pipeline.rec_aggregate import build_run_recommendations
from ..pipeline.recommendations import generate_recommendations
from ..pipeline.satisfaction import PageSignals
from ..pipeline.schema import score_schema
from ..pipeline.technical import score_technical
from ..services.geo_tasks import sync_geo_signal_tasks
from ..services.satisfaction_ledger import apply_gate
from ..services.task_enrichment import enrich_recommendations
from .accounting import (  # noqa: F401
    _add_background_spend,
    _budget_status,
    _finalize_accounting,
    _log_run_cost,
    _record_run_spend,
    _record_spend,
)
from .analysis import (  # noqa: F401
    _kickoff_sitemap_audit,
    _run_partial_analysis,
    _save_probes_and_tracks,
    run_single_page_analysis,
    start_analysis_task,
)
from .competitive import (  # noqa: F401
    _domain_label,
    _fire_competitive_prompt_fast,
    _generate_and_fire_competitive_prompts,
    _score_competitor_static,
)
from .crawling import (  # noqa: F401
    _crawl_result_from_html,
    _crawl_via_integration,
    _crawl_via_nextjs_snapshot,
    _robots_txt_for,
)
from .progress import (  # noqa: F401
    _end_trace,
    _start_trace,
    _update_status,
    _update_sub_progress,
)
