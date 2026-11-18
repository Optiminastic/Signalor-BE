"""
Brand deletion: refused while a send is unresolved, and allowed to erase the
append-only records otherwise.

Deleting a brand must erase its agent records, which database triggers
otherwise refuse to delete (migration 0022). This receiver runs inside the
deletion's transaction, before any row is removed, and adds the brand's id to
the transaction-local setting the triggers check. Rows of any other brand stay
protected, and the setting ends with the transaction.
"""

from __future__ import annotations

from django.db import connection
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.organizations.models import Organization

from .services.control import deletion

ERASURE_SETTING = "signalor.control_erasure"


def allow_erasure(org_id: int) -> None:
    """Let this transaction delete ``org_id``'s append-only rows (Postgres only)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, concat_ws(',', NULLIF(current_setting(%s, true), ''), %s), true)",
            [ERASURE_SETTING, ERASURE_SETTING, str(org_id)],
        )


@receiver(pre_delete, sender=Organization, dispatch_uid="growth_agent_erasure_flag")
def mark_brand_erasure(sender, instance: Organization, **kwargs) -> None:
    # Refuses (409 at the endpoints) while a send is in flight or unresolved;
    # otherwise lets this transaction erase the brand's agent records.
    deletion.guard(instance.pk)
    allow_erasure(instance.pk)
