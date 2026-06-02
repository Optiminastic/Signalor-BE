"""Pull a Next.js site's own rendered HTML via the @signalor/nextjs snapshot route.

When a site runs the SDK and mounts the snapshot route, the analyzer fetches the
site's server-rendered HTML directly from its deployment origin (e.g. *.vercel.app)
instead of crawling the public URL — bypassing Cloudflare / Turnstile entirely.

Auth shares a secret without ever transmitting the API-key plaintext: the SDK has
the plaintext key and computes sha256(key); we store exactly that hash as
``ApiKey.key_hash`` (captured on the deploy call into
``NextJsDeployment.signing_key_hash``). Both sides compute
``HMAC(key_hash, "<path>\\n<timestamp>")``, so every request is signed and
time-bounded — no static bearer to replay long-term.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from django.utils import timezone

from apps.integrations._http import request_with_retry

logger = logging.getLogger("apps")

# Path the host app mounts the SDK snapshot route at (app/api/signalor/snapshot).
SNAPSHOT_PATH = "/api/signalor/snapshot"
TIMEOUT_SECONDS = 30
# Homepage + key routes. Keep parity with crawl_site's per-page budget so we
# don't over-pull (each route is one render on the customer's app).
MAX_ROUTES = 12


def get_config(run) -> dict | None:
    """Latest snapshot-capable deployment for the run's org, or None.

    Used for both deploy-triggered runs and ad-hoc re-analysis (we just look up
    the most recent deployment that advertised the snapshot route).
    """
    if not getattr(run, "organization_id", None):
        return None
    from apps.public_api.models import NextJsDeployment

    dep = (
        NextJsDeployment.objects.filter(
            organization_id=run.organization_id,
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


def is_available(run) -> bool:
    """True when the run's org has a snapshot-capable Next.js deployment."""
    return get_config(run) is not None


def routes_for_run(run) -> list[str]:
    """Homepage + advertised key routes, deduped and capped. ``/`` is always first."""
    config = get_config(run)
    raw = (config or {}).get("routes") or []
    ordered = ["/"]
    for path in raw:
        p = (path or "").strip()
        if not p:
            continue
        if not p.startswith("/"):
            p = f"/{p}"
        if p not in ordered:
            ordered.append(p)
    return ordered[:MAX_ROUTES]


def _sign(key_hash: str, path: str, timestamp: str) -> str:
    msg = f"{path}\n{timestamp}".encode()
    return hmac.new(key_hash.encode(), msg, hashlib.sha256).hexdigest()


def fetch_snapshot(
    origin: str,
    path: str,
    key_hash: str,
    *,
    timeout: float = TIMEOUT_SECONDS,
) -> tuple[int, str]:
    """Fetch the rendered HTML of ``path`` via the site's snapshot route.

    Returns ``(target_status, html)`` where ``target_status`` is the status the
    route reports for the underlying page. Raises on transport error / non-200
    from the route itself / unparseable body.
    """
    ts = str(int(timezone.now().timestamp()))
    token = _sign(key_hash, path, ts)
    url = f"{origin}{SNAPSHOT_PATH}"
    resp = request_with_retry(
        "GET",
        url,
        params={"path": path},
        headers={
            "X-Signalor-Timestamp": ts,
            "X-Signalor-Snapshot-Token": token,
            "Accept": "application/json",
        },
        timeout=timeout,
        max_retries=1,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"snapshot route returned {resp.status_code} for {path}")
    data = resp.json()
    return int(data.get("status", 0) or 0), (data.get("html") or "")
