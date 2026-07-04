from django.contrib import admin

from .models import GithubFixJob, GithubInstallation


@admin.register(GithubInstallation)
class GithubInstallationAdmin(admin.ModelAdmin):
    list_display = (
        "installation_id",
        "repo_full_name",
        "account_login",
        "organization",
        "is_active",
        "created_at",
    )
    search_fields = ("installation_id", "repo_full_name", "account_login")
    list_filter = ("is_active", "account_type")


@admin.register(GithubFixJob)
class GithubFixJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "pr_number",
        "installation",
        "analysis_run",
        "score_before",
        "score_after",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("pr_url", "branch_name")
    readonly_fields = ("created_at", "updated_at")
