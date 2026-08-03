"""Analyzer models, split by domain.

Was a single 2,051-line module with 44 models (ARCHITECTURE.md §5.2).
Everything is re-exported here, so ``from apps.analyzer.models import X``
keeps working and the app registry still discovers every model.

No migration accompanies this: ``app_label`` comes from the AppConfig and
every ``db_table`` keeps its default, so the database is untouched.
"""

from .backlinks import (  # noqa: F401
    BacklinkOpportunity,
    BacklinkOrder,
    BacklinkProduct,
    BacklinkProvider,
    BacklinkSchedule,
    BacklinkSnapshot,
    BlogAutomationConfig,
    BlogAutomationJob,
    BlogPost,
)
from .brand import (  # noqa: F401
    BrandKit,
    EntityResolutionReport,
    DomainAnalyticsSnapshot,
    OverviewInsightReport,
)
from .citations import (  # noqa: F401
    CitationOutreach,
)
from .commerce import (  # noqa: F401
    ShopifyProduct,
)
from .competitors import (  # noqa: F401
    Competitor,
)
from .crawl import (  # noqa: F401
    CrawlerHit,
    GeoImprovement,
    SchemaWatch,
    SchemaWatchPage,
    SitemapAudit,
    SitemapAuditPage,
)
from .infra import (  # noqa: F401
    ChatMessage,
    LLMResponseCache,
)
from .prompts import (  # noqa: F401
    PromptCitation,
    PromptEvalLog,
    PromptResult,
    PromptSchemaArtifact,
    PromptTrack,
    PromptWikipediaDraft,
)
from .rank import (  # noqa: F401
    RankAudit,
    RankQuery,
    RankResult,
)
from .run import (  # noqa: F401
    AgentLogEntry,
    AIVisibilityProbe,
    AnalysisRun,
    BrandVisibility,
    PageScore,
    ScheduledAnalysis,
    _generate_slug,
)
from .tasks import (  # noqa: F401
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
    AutoFixJob,
    ContentSuggestion,
    Recommendation,
    TaskSatisfaction,
    UserAction,
    UserGamification,
)

__all__ = [
    "ACHIEVEMENTS_INFO",
    "ACTION_TEMPLATES",
    "AIVisibilityProbe",
    "AgentLogEntry",
    "AnalysisRun",
    "AutoFixJob",
    "BacklinkOpportunity",
    "BacklinkOrder",
    "BacklinkProduct",
    "BacklinkProvider",
    "BacklinkSchedule",
    "BacklinkSnapshot",
    "BlogAutomationConfig",
    "BlogAutomationJob",
    "BlogPost",
    "BrandKit",
    "EntityResolutionReport",
    "BrandVisibility",
    "ChatMessage",
    "CitationOutreach",
    "Competitor",
    "ContentSuggestion",
    "CrawlerHit",
    "DomainAnalyticsSnapshot",
    "GeoImprovement",
    "LLMResponseCache",
    "OverviewInsightReport",
    "PageScore",
    "PromptCitation",
    "PromptEvalLog",
    "PromptResult",
    "PromptSchemaArtifact",
    "PromptTrack",
    "PromptWikipediaDraft",
    "RankAudit",
    "RankQuery",
    "RankResult",
    "Recommendation",
    "ScheduledAnalysis",
    "SchemaWatch",
    "SchemaWatchPage",
    "ShopifyProduct",
    "SitemapAudit",
    "SitemapAuditPage",
    "TaskSatisfaction",
    "UserAction",
    "UserGamification",
    "_generate_slug",
]
