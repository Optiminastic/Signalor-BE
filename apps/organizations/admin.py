from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from .models import BrandCorpusChunk, BrandProfile, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "owner_email", "url", "platform", "slug", "created_at")
    search_fields = ("name", "owner_email", "url", "slug")
    list_filter = ("platform",)
    readonly_fields = ("slug", "normalized_url", "created_at", "updated_at")


@admin.register(BrandProfile)
class BrandProfileAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "status",
        "confidence",
        "last_verified_at",
        "source_run",
        "updated_at",
    )
    list_filter = ("status",)
    search_fields = ("organization__name", "organization__owner_email")
    readonly_fields = ("confidence", "source_run", "sources", "created_at", "updated_at")
    actions = ["approve_profiles", "reject_profiles"]
    fieldsets = (
        (None, {"fields": ("organization", "status", "confidence", "last_verified_at", "source_run")}),
        ("Interpretive (editable)", {"fields": ("identity", "positioning", "audience", "voice")}),
        ("Verified anchors", {"fields": ("canonical_facts", "competitors", "sources")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.action(description="Approve selected brand profiles")
    def approve_profiles(self, request, queryset):
        with transaction.atomic():
            n = queryset.update(status=BrandProfile.Status.APPROVED, last_verified_at=timezone.now())
        self.message_user(request, f"Approved {n} brand profile(s).", messages.SUCCESS)

    @admin.action(description="Reject selected brand profiles")
    def reject_profiles(self, request, queryset):
        with transaction.atomic():
            n = queryset.update(status=BrandProfile.Status.REJECTED, last_verified_at=timezone.now())
        self.message_user(request, f"Rejected {n} brand profile(s).", messages.SUCCESS)


@admin.register(BrandCorpusChunk)
class BrandCorpusChunkAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "source_url",
        "version",
        "is_current",
        "embedding_model",
        "updated_at",
    )
    list_filter = ("is_current", "embedding_model")
    search_fields = ("organization__name", "organization__owner_email", "source_url")
    # Content is machine-generated from crawls; expose it read-only so the admin is
    # for inspection/debugging, not hand-editing embeddings or hashes.
    readonly_fields = (
        "organization",
        "source_run",
        "source_url",
        "heading_path",
        "text",
        "metadata",
        "content_hash",
        "embedding_model",
        "version",
        "created_at",
        "updated_at",
    )
    exclude = ("embedding",)  # 768-float vector — never useful to render in the admin
