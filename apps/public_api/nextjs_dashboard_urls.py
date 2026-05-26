"""
Dashboard-side endpoints for the Next.js integration. Cookie/email-authed,
mounted under /api/integrations/nextjs/ to live alongside other
integrations (GA4, Shopify, WordPress) in the dashboard mental model.

The Bearer-auth endpoints the SDK calls live separately at
``/api/v1/public/nextjs/`` (see ``apps/public_api/nextjs/urls.py``).
"""

from django.urls import path

from .dashboard_views import NextJsDeploymentListView

app_name = "public_api_nextjs_dashboard"

urlpatterns = [
    path("deployments/", NextJsDeploymentListView.as_view(), name="nextjs-deployments-list"),
]
