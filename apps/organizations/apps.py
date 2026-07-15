from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"
    verbose_name = "Organizations"

    def ready(self):
        # Registers the brand-card cache invalidation receivers (Epic 7).
        from . import signals  # noqa: F401
