from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integrations"

    def ready(self) -> None:
        # Registers the Google OAuth redirect-URI system checks (deploy-time guard
        # against redirect_uri_mismatch). Import for the @register side effect only.
        from . import checks  # noqa: F401

        # Post-analysis notifications (Slack today, more later).
        from . import signals  # noqa: F401

        self._register_remediation_providers()

    @staticmethod
    def _register_remediation_providers() -> None:
        """Declare which providers can apply a fix.

        One line per provider. A Framer or Webflow adapter joins this list without
        touching remediation, its models, or a migration - which is the whole
        point of docs/app-boundaries.md.
        """
        from apps.remediation import providers

        from .github import remediation as github_remediation

        providers.register(github_remediation)
