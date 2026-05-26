from django.contrib import admin

from .models import ApiKey, NextJsDeployment, PublicApiUsage, Webhook, WebhookDelivery


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "organization",
        "environment",
        "key_prefix",
        "key_last4",
        "created_at",
        "last_used_at",
        "revoked_at",
    ]
    list_filter = ["environment", "revoked_at"]
    search_fields = ["name", "key_prefix", "organization__name", "created_by_email"]
    readonly_fields = [
        "key_prefix",
        "key_last4",
        "key_hash",
        "created_at",
        "last_used_at",
    ]


@admin.register(PublicApiUsage)
class PublicApiUsageAdmin(admin.ModelAdmin):
    list_display = ["timestamp", "organization", "route", "method", "status_code", "duration_ms"]
    list_filter = ["route", "method", "status_code"]
    search_fields = ["organization__name", "route"]
    readonly_fields = [
        "api_key",
        "organization",
        "route",
        "method",
        "status_code",
        "duration_ms",
        "timestamp",
    ]


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ["url", "organization", "is_active", "created_at", "last_delivered_at"]
    list_filter = ["is_active"]
    search_fields = ["url", "organization__name", "created_by_email"]
    readonly_fields = ["secret_encrypted", "secret_last4", "created_at", "last_delivered_at"]


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "webhook", "event", "status", "response_status", "attempts"]
    list_filter = ["status", "event"]
    search_fields = ["webhook__url", "resource_id"]
    readonly_fields = [
        "webhook",
        "event",
        "resource_id",
        "status",
        "attempts",
        "response_status",
        "response_body_preview",
        "error_message",
        "created_at",
        "delivered_at",
    ]


@admin.register(NextJsDeployment)
class NextJsDeploymentAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "organization",
        "environment",
        "host",
        "commit_sha",
        "status",
        "analysis_run",
    ]
    list_filter = ["status", "environment", "host"]
    search_fields = ["organization__name", "commit_sha", "url"]
    readonly_fields = ["pages_metadata", "build_metadata", "created_at", "deployed_at"]
