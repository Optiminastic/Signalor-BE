"""Adapter: answers ``core.ports.snapshot`` from the NextJsDeployment table.

The query used to live in ``analyzer.services.nextjs_snapshot``, which meant the
analyzer imported ``public_api.models``. That was the whole
``analyzer <-> public_api`` cycle, and it pointed the wrong way: ``public_api``
is the outermost layer, so nothing below it may import it.

The deployment table is this app's, so the query belongs here. Registered from
``PublicApiConfig.ready()``.
"""

from __future__ import annotations

from .models import NextJsDeployment


def get_config(run) -> dict | None:
    """Latest snapshot-capable deployment for the run's org, or None.

    Serves both deploy-triggered runs and ad-hoc re-analysis: take the most
    recent deployment that advertised a snapshot route and carries the signing
    key hash needed to authenticate against it.
    """
    org_id = getattr(run, "organization_id", None)
    if not org_id:
        return None

    dep = (
        NextJsDeployment.objects.filter(
            organization_id=org_id,
            snapshot_supported=True,
        )
        .exclude(snapshot_origin="")
        .exclude(signing_key_hash="")
        .order_by("-created_at")
        .first()
    )
    if dep is None:
        return None
    return {
        "origin": dep.snapshot_origin.rstrip("/"),
        "routes": list(dep.snapshot_routes or []),
        "key_hash": dep.signing_key_hash,
    }
