from django.contrib import admin
from django.urls import path, include
from apps.analyzer.views import HealthCheckView

urlpatterns = [
    path('', HealthCheckView.as_view(), name='root-health-check'),
    path('admin/', admin.site.urls),
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/', include('apps.organizations.urls')),
    path('api/analyzer/', include('apps.analyzer.urls')),
    path('api/integrations/', include('apps.integrations.urls')),
    path('api/visibility/', include('apps.visibility.urls')),
]
