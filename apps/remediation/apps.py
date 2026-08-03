from django.apps import AppConfig


class RemediationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.remediation"
    verbose_name = "Remediation"

    def ready(self):
        """Answer core.ports.code_fix.

        Which findings are worth attempting is remediation's knowledge, not any
        one provider's - github_agent registered this only because the logic used
        to live there. Unregistered answers False, so a deployment with no
        remediation app offers no in-repo fixes.
        """
        from core.ports import code_fix

        from .services import fixable

        code_fix.register(fixable)
