"""Cache-invalidation signals for organizations (Epic 7).

The brand card is cached per org, so any BrandProfile change must drop it. Hooking
``post_save``/``post_delete`` covers every path that goes through the ORM's save()
(API review + PATCH, bootstrap upsert, admin single-object edits, shell).

NOTE: ``QuerySet.update()`` does NOT emit these signals -- the admin's bulk
approve/reject actions therefore invalidate explicitly (see admin.py).
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import BrandProfile


@receiver(post_save, sender=BrandProfile)
@receiver(post_delete, sender=BrandProfile)
def _invalidate_brand_card(sender, instance, **kwargs):
    from apps.analyzer._cache import invalidate_brand_card

    invalidate_brand_card(getattr(instance, "organization_id", None))
