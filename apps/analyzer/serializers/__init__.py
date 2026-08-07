"""Analyzer models, split by domain.

Was a single 2,051-line module with 44 models (ARCHITECTURE.md §5.2).
Everything is re-exported here, so ``from apps.analyzer.models import X``
keeps working and the app registry still discovers every model.

No migration accompanies this: ``app_label`` comes from the AppConfig and
every ``db_table`` keeps its default, so the database is untouched.
"""

from .backlinks import (  # noqa: F401
    BlogAutomationConfigSerializer,
    BlogAutomationJobSerializer,
)
from .base import (  # noqa: F401
    AIVisibilityProbeSerializer,
    BrandVisibilitySerializer,
    PageScoreSerializer,
)
from .brand import (  # noqa: F401
    EntityResolutionRequestSerializer,
    IndexNowSubmitSerializer,
    _url_validator,
)
from .citations import (  # noqa: F401
    AiRecommendationEnginePointSerializer,
    AiRecommendationSampleSerializer,
    AiRecommendationSummarySerializer,
    CitationTrendPointSerializer,
    ShareOfVoiceSerializer,
)
from .competitors import (  # noqa: F401
    CompetitorSerializer,
)
from .crawl import (  # noqa: F401
    SchemaWatchPageSerializer,
    SchemaWatchSerializer,
    SitemapAuditPageSerializer,
    SitemapAuditSerializer,
)
from .projection import (  # noqa: F401
    ProjectionCompetitorsSerializer,
    ProjectionMetricSerializer,
    ProjectionPromptsSerializer,
    ProjectionSerializer,
)
from .prompts import (  # noqa: F401
    AddPromptSerializer,
    PromptCitationSerializer,
    PromptResultFullSerializer,
    PromptResultSerializer,
    PromptTrackSerializer,
)
from .rank import (  # noqa: F401
    RankAuditSerializer,
    RankQuerySerializer,
    RankResultSerializer,
)
from .run import (  # noqa: F401
    AgentLogEntrySerializer,
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    ScheduledAnalysisSerializer,
    StartAnalysisSerializer,
)
from .tasks import (  # noqa: F401
    AchievementSerializer,
    ActionStatsSerializer,
    ActionTemplateSerializer,
    CreateUserActionSerializer,
    RecommendationSerializer,
    UpdateUserActionSerializer,
    UserActionSerializer,
    UserGamificationSerializer,
    prompt_track_index,
)

__all__ = [
    "AIVisibilityProbeSerializer",
    "AchievementSerializer",
    "ActionStatsSerializer",
    "ActionTemplateSerializer",
    "AddPromptSerializer",
    "AgentLogEntrySerializer",
    "AiRecommendationEnginePointSerializer",
    "AiRecommendationSampleSerializer",
    "AiRecommendationSummarySerializer",
    "AnalysisRunDetailSerializer",
    "AnalysisRunListSerializer",
    "BlogAutomationConfigSerializer",
    "BlogAutomationJobSerializer",
    "BrandVisibilitySerializer",
    "CitationTrendPointSerializer",
    "CompetitorSerializer",
    "CreateUserActionSerializer",
    "EntityResolutionRequestSerializer",
    "IndexNowSubmitSerializer",
    "PageScoreSerializer",
    "PromptCitationSerializer",
    "ProjectionCompetitorsSerializer",
    "ProjectionMetricSerializer",
    "ProjectionPromptsSerializer",
    "ProjectionSerializer",
    "PromptResultFullSerializer",
    "PromptResultSerializer",
    "PromptTrackSerializer",
    "RankAuditSerializer",
    "RankQuerySerializer",
    "RankResultSerializer",
    "RecommendationSerializer",
    "ScheduledAnalysisSerializer",
    "SchemaWatchPageSerializer",
    "SchemaWatchSerializer",
    "ShareOfVoiceSerializer",
    "SitemapAuditPageSerializer",
    "SitemapAuditSerializer",
    "StartAnalysisSerializer",
    "UpdateUserActionSerializer",
    "UserActionSerializer",
    "prompt_track_index",
    "UserGamificationSerializer",
    "_url_validator",
]
