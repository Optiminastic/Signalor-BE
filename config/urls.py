from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('apps.organizations.urls')),
    path('api/analyzer/', include('apps.analyzer.urls')),
    path('api/integrations/', include('apps.integrations.urls')),
    path('api/visibility/', include('apps.visibility.urls')),
]