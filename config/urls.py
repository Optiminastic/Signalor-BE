from django.contrib import admin
from django.urls import include, path

from apps.analyzer.views import HealthCheckView

urlpatterns = [
    path("", HealthCheckView.as_view(), name="root-health-check"),
    path("admin/", admin.site.urls),
    path("api/health/", HealthCheckView.as_view(), name="health-check"),
    path("api/", include("apps.organizations.urls")),
    path("api/analyzer/", include("apps.analyzer.urls")),
    path("api/integrations/", include("apps.integrations.urls")),
    path("api/integrations/nextjs/", include("apps.public_api.nextjs_dashboard_urls")),
    path("api/v1/public/", include("apps.public_api.urls")),
    path("api/keys/", include("apps.public_api.dashboard_urls")),
    path("api/webhooks/", include("apps.public_api.webhook_urls")),
    path("api/visibility/", include("apps.visibility.urls")),
    path("api/referrals/", include("apps.referrals.urls")),
    path("api/partners/", include("apps.partners.urls")),
    path("api/", include("apps.accounts.urls")),
]
