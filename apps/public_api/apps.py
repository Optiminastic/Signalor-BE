from django.apps import AppConfig


class PublicApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.public_api"
    verbose_name = "Public API"

    def ready(self):
        from core.ports import snapshot

        # signals: imported for side-effects (post_save handlers on AnalysisRun).
        # snapshot_provider: answers core.ports.snapshot. The NextJsDeployment
        # table is ours, so the query belongs here and analyzer asks through the
        # port instead of importing up a layer.
        from . import (
            signals,  # noqa: F401
            snapshot_provider,
        )

        snapshot.register(snapshot_provider)
