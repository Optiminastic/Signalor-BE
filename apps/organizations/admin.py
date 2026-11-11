from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

from .models import BrandCorpusChunk, BrandProfile, Organization


def _invalidate_cards(org_ids) -> None:
    """Bulk actions use QuerySet.update(), which skips post_save -- so the cached
    brand cards must be dropped by hand here (Epic 7)."""
    from core.cache.keys import invalidate_brand_card

    for org_id in org_ids:
        invalidate_brand_card(org_id)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "owner_email", "url", "platform", "slug", "created_at")
    search_fields = ("name", "owner_email", "url", "slug")
    list_filter = ("platform",)
    readonly_fields = ("slug", "normalized_url", "created_at", "updated_at")

    # Ownership decides who may edit, delete and approve work for a brand, so it
    # is not an editable admin field (SEC-1a). A superuser assigns the owner
    # when adding a brand on someone's behalf. Changing the owner of an existing
    # brand has no admin path yet: it needs a database administrator under a
    # written change record until the audited transfer view (SEC-1b) exists.
    OWNER_FIELD = "owner_email"

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields if obj is None else (*fields, self.OWNER_FIELD)

    def has_add_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)


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
        org_ids = list(queryset.values_list("organization_id", flat=True))
        with transaction.atomic():
            n = queryset.update(status=BrandProfile.Status.APPROVED, last_verified_at=timezone.now())
        _invalidate_cards(org_ids)
        self.message_user(request, f"Approved {n} brand profile(s).", messages.SUCCESS)

    @admin.action(description="Reject selected brand profiles")
    def reject_profiles(self, request, queryset):
        org_ids = list(queryset.values_list("organization_id", flat=True))
        with transaction.atomic():
            n = queryset.update(status=BrandProfile.Status.REJECTED, last_verified_at=timezone.now())
        _invalidate_cards(org_ids)
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
