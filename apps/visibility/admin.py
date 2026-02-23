from django.contrib import admin

from .models import VisibilityCheck


@admin.register(VisibilityCheck)
class VisibilityCheckAdmin(admin.ModelAdmin):
    list_display = ["id", "brand_name", "brand_url", "status", "overall_score", "created_at"]
    list_filter = ["status"]
    search_fields = ["brand_name", "brand_url", "email"]
