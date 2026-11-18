from django.apps import AppConfig


class GrowthAgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.growth_agent"
    verbose_name = "Growth Agent"

    def ready(self) -> None:
        from . import signals  # noqa: F401 - registers the brand-erasure receiver
