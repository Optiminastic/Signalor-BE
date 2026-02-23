from django.contrib import admin

from .models import GADataSnapshot, Integration


class GADataSnapshotInline(admin.TabularInline):
    model = GADataSnapshot
    extra = 0
    readonly_fields = [
        "date_start", "date_end", "sessions", "organic_sessions",
        "bounce_rate", "sync_status", "created_at",
    ]


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ["organization", "provider", "is_active", "created_at"]
    list_filter = ["provider", "is_active"]
    search_fields = ["organization__name", "organization__owner_email"]
    inlines = [GADataSnapshotInline]


@admin.register(GADataSnapshot)
class GADataSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "integration", "date_start", "date_end",
        "sessions", "organic_sessions", "sync_status", "created_at",
    ]
    list_filter = ["sync_status"]
