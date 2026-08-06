"""Task-queue adapters.

``dispatch`` sends work by task NAME so an app never imports a worker module.
"""

from .dispatch import (  # noqa: F401
    ANALYSIS_RUN,
    ANALYSIS_SCHEDULED,
    OUTREACH_BENCHMARK,
    SITEMAP_AUDIT,
    is_eager,
    send,
)
