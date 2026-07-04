from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from apps.analyzer.views import HealthCheckView

urlpatterns = [
    path("", HealthCheckView.as_view(), name="root-health-check"),
    path("admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health-check"),
    path("api/", include("apps.organizations.urls")),
    path("api/analyzer/", include("apps.analyzer.urls")),
    path("api/drip/", include("apps.drip.urls")),
    path("api/integrations/", include("apps.integrations.urls")),
    path("api/github/", include("apps.github_agent.urls")),
    path("api/integrations/nextjs/", include("apps.public_api.nextjs_dashboard_urls")),
    path("api/v1/public/", include("apps.public_api.urls")),
    path("api/keys/", include("apps.public_api.dashboard_urls")),
    path("api/webhooks/", include("apps.public_api.webhook_urls")),
    path("api/visibility/", include("apps.visibility.urls")),
    path("api/referrals/", include("apps.referrals.urls")),
    path("api/partners/", include("apps.partners.urls")),
    path("api/", include("apps.accounts.urls")),
    # Serve user-uploaded media (e.g. profile photos saved via the local
    # filesystem fallback when B2 is not configured). Prod uses B2 and never
    # hits this route.
    re_path(
        r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"),
        serve,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
