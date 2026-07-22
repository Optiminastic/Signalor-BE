"""Self-heal for integration sync snapshots orphaned by a dead worker.

A sync creates a snapshot row with ``sync_status="syncing"`` and fills it in as it
runs. If the process dies mid-sync (redeploy, OOM, restart), the ``except`` that
would mark it ``failed`` never runs, so the row stays ``"syncing"`` forever — and
because every auto-sync trigger is gated on "no syncing row exists", that dead row
**permanently blocks all future syncs** and the UI shows a perpetual spinner.

``reap_stale_syncing`` flips such rows to ``failed`` once they are older than the
timeout, so the guard clears and the next request can start a fresh sync. This was
already done inline for GSC-index snapshots; this centralizes it for every type.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Manager
from django.utils import timezone

# A worker refreshes a syncing snapshot as it progresses, so silence past this
# window means the worker is gone. Kept short — a stuck row blocks the feature.
STALE_SYNC_TIMEOUT = timedelta(minutes=10)


def reap_stale_syncing(snapshots: Manager, timeout: timedelta = STALE_SYNC_TIMEOUT) -> int:
    """Fail any ``"syncing"`` snapshot older than ``timeout``. Returns the count.

    Idempotent — safe to call on every status/refresh request before the
    "is a sync already running?" guard.
    """
    cutoff = timezone.now() - timeout
    return snapshots.filter(sync_status="syncing", created_at__lt=cutoff).update(
        sync_status="failed",
        error_message="Sync timed out or was interrupted. Please try again.",
    )
