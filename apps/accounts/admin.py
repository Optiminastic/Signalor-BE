from django.contrib import admin

from .models import GrowthLead, User

admin.site.register(User)


@admin.register(GrowthLead)
class GrowthLeadAdmin(admin.ModelAdmin):
    list_display = ("company_name", "full_name", "email", "service", "status", "created_at")
    list_filter = ("status", "service", "created_at")
    search_fields = ("company_name", "full_name", "email", "website")
    list_editable = ("status",)
    readonly_fields = (
        "lead_id",
        "full_name",
        "email",
        "company_name",
        "website",
        "service",
        "services",
        "goals",
        "created_at",
        "updated_at",
    )
