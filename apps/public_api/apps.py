from django.apps import AppConfig


class PublicApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.public_api"
    verbose_name = "Public API"

    def ready(self):
        # Import for side-effects: registers post_save handlers on AnalysisRun.
        from . import signals  # noqa: F401
