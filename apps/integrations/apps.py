from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integrations"

    def ready(self) -> None:
        # Registers the Google OAuth redirect-URI system checks (deploy-time guard
        # against redirect_uri_mismatch). Import for the @register side effect only.
        from . import checks  # noqa: F401
