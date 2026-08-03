"""Backlink catalog, orders and scheduling."""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
    PromptTrack,
)
from ._shared import (
    _auto_can_add_today,
    _brand_ref_for_run,
    _serialize_order,
    _serialize_product,
    logger,
)


class BacklinkCatalogView(APIView):
    """
    GET /runs/s/<slug>/backlinks/catalog/

    Returns the cached catalog for all enabled providers. Refreshes from each
    provider on first call (when the cache is empty) so the UI never sees an
    empty list.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import BacklinkProduct, BacklinkProvider

        # We don't strictly need the run — catalog is global — but checking the
        # slug exists keeps the URL shape consistent with the rest of the API.
        get_object_or_404(AnalysisRun, slug=slug)

        # Idempotent — adds any newly-defined providers without touching existing rows.
        self._seed_default_providers()
        providers = list(BacklinkProvider.objects.filter(is_enabled=True))

        for provider in providers:
            if not provider.products.exists():
                self._refresh_provider_catalog(provider)

        # Optional filters: ?link_type=guest_post&min_da=70&niche=tech
        qs = BacklinkProduct.objects.select_related("provider").filter(provider__is_enabled=True)
        link_type = request.GET.get("link_type")
        if link_type:
            qs = qs.filter(link_type=link_type)
        try:
            min_da = int(request.GET.get("min_da") or 0)
        except (TypeError, ValueError):
            min_da = 0
        if min_da > 0:
            qs = qs.filter(domain_authority__gte=min_da)
        niche = (request.GET.get("niche") or "").strip().lower()
        if niche:
            qs = qs.filter(niche_tags__contains=[niche])

        return Response(
            {
                "providers": [{"slug": p.slug, "display_name": p.display_name} for p in providers],
                "products": [_serialize_product(p) for p in qs[:200]],
            }
        )

    @staticmethod
    def _seed_default_providers():
        from ..models import BacklinkProvider

        BacklinkProvider.objects.get_or_create(
            slug="fatjoe",
            defaults={
                "display_name": "FATJOE",
                "homepage_url": "https://fatjoe.com",
                "is_enabled": True,
                "notes": "Reseller / white-label backlinks marketplace.",
            },
        )
        BacklinkProvider.objects.get_or_create(
            slug="budget_links",
            defaults={
                "display_name": "BudgetLinks",
                "homepage_url": "",
                "is_enabled": True,
                "notes": "Budget-tier reseller — sub-$50 placements, niche guest posts, profile citations.",
            },
        )

    @staticmethod
    def _refresh_provider_catalog(provider):
        """Pull current catalog from the provider and upsert into BacklinkProduct."""
        from apps.integrations.services.backlink_providers import get_client

        from ..models import BacklinkProduct

        try:
            client = get_client(provider.slug)
            rows = client.list_products()
        except Exception as exc:
            logger.warning(
                "BacklinkCatalogView: failed to refresh %s catalog: %s",
                provider.slug,
                exc,
            )
            return

        for row in rows:
            BacklinkProduct.objects.update_or_create(
                provider=provider,
                sku=row.sku,
                defaults={
                    "domain": row.domain,
                    "title": row.title,
                    "link_type": row.link_type,
                    "domain_authority": row.domain_authority,
                    "domain_rank": row.domain_rank,
                    "monthly_traffic": row.monthly_traffic,
                    "niche_tags": row.niche_tags,
                    "language": row.language,
                    "country": row.country,
                    "do_follow": row.do_follow,
                    "wholesale_price_cents": row.wholesale_price_cents,
                    "retail_price_cents": row.retail_price_cents,
                    "currency": row.currency,
                    "lead_time_days": row.lead_time_days,
                    "extras": row.extras,
                },
            )

class BacklinkOrderListCreateView(APIView):
    """
    GET  /runs/s/<slug>/backlinks/orders/         — list orders for this run
    POST /runs/s/<slug>/backlinks/orders/         — place an order

    POST body:
      {
        "product_id": int,
        "target_url": str,
        "anchor_text": str,
        "track_id": int | null,    // optional, link the order to a prompt
        "notes": str | null,
        "user_email": str
      }
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        qs = (
            BacklinkOrder.objects.filter(analysis_run=run)
            .select_related("provider", "product")
            .order_by("-created_at")
        )
        email = (request.GET.get("user_email") or "").strip().lower()
        if email:
            qs = qs.filter(user_email__iexact=email)
        return Response({"orders": [_serialize_order(o) for o in qs]})

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import BacklinkOrder, BacklinkProduct

        run = get_object_or_404(AnalysisRun, slug=slug)

        product_id = request.data.get("product_id")
        target_url = (request.data.get("target_url") or "").strip()
        anchor_text = (request.data.get("anchor_text") or "").strip()
        user_email = (request.data.get("user_email") or "").strip().lower()
        track_id = request.data.get("track_id")
        notes = (request.data.get("notes") or "").strip()

        missing = [
            field
            for field, value in [
                ("product_id", product_id),
                ("target_url", target_url),
                ("anchor_text", anchor_text),
                ("user_email", user_email),
            ]
            if not value
        ]
        if missing:
            return Response(
                {"detail": f"Missing required fields: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            product = BacklinkProduct.objects.select_related("provider").get(id=int(product_id))
        except (BacklinkProduct.DoesNotExist, TypeError, ValueError):
            return Response(
                {"detail": "Unknown product_id."},
                status=status.HTTP_404_NOT_FOUND,
            )

        prompt_track = None
        if track_id is not None:
            try:
                prompt_track = PromptTrack.objects.get(id=int(track_id), analysis_run=run)
            except (PromptTrack.DoesNotExist, TypeError, ValueError):
                prompt_track = None

        from apps.integrations.services.backlink_providers import get_client

        order = BacklinkOrder.objects.create(
            provider=product.provider,
            product=product,
            user_email=user_email,
            analysis_run=run,
            prompt_track=prompt_track,
            target_url=target_url[:2048],
            anchor_text=anchor_text[:300],
            status=BacklinkOrder.Status.PENDING_PAYMENT,
            price_cents=product.retail_price_cents,
            currency=product.currency,
            notes_for_provider=notes,
        )

        try:
            client = get_client(product.provider.slug)
            result = client.place_order(
                sku=product.sku,
                target_url=target_url,
                anchor_text=anchor_text,
                notes=notes,
            )
            order.status = result.status or BacklinkOrder.Status.QUEUED
            order.provider_order_id = result.provider_order_id or ""
            order.ordered_at = timezone.now()
            order.save(update_fields=["status", "provider_order_id", "ordered_at"])
        except Exception as exc:
            logger.exception("Immediate backlink order placement failed: %s", exc)

        return Response(_serialize_order(order), status=status.HTTP_201_CREATED)

class BacklinkOrderDetailView(APIView):
    """
    GET  /runs/s/<slug>/backlinks/orders/<int:order_id>/  — refresh status
    POST /runs/s/<slug>/backlinks/orders/<int:order_id>/  — manual sync poll
    """

    permission_classes = [AllowAny]

    def _get_order(self, slug, order_id):
        from django.shortcuts import get_object_or_404

        from ..models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        return get_object_or_404(
            BacklinkOrder.objects.select_related("provider", "product"),
            id=order_id,
            analysis_run=run,
        )

    def _poll(self, order):
        from apps.integrations.services.backlink_providers import get_client

        from ..models import BacklinkOrder

        if not order.provider_order_id:
            return order
        try:
            client = get_client(order.provider.slug)
            res = client.get_status(provider_order_id=order.provider_order_id)
        except Exception as exc:
            logger.warning("Backlink order poll failed (%s): %s", order.id, exc)
            return order

        # Map provider statuses to our enum.
        new_status = (res.status or "").lower() or order.status
        if new_status not in BacklinkOrder.Status.values:
            return order

        if new_status != order.status:
            order.status = new_status
            if res.proof_url:
                order.proof_url = res.proof_url[:2048]
            if new_status == BacklinkOrder.Status.DELIVERED and not order.delivered_at:
                order.delivered_at = timezone.now()
            if res.error_message:
                order.error_message = res.error_message[:1000]
            order.save(update_fields=["status", "proof_url", "delivered_at", "error_message"])
        return order

    def get(self, request, slug, order_id):
        order = self._get_order(slug, order_id)
        return Response(_serialize_order(order))

    def post(self, request, slug, order_id):
        order = self._get_order(slug, order_id)
        order = self._poll(order)
        return Response(_serialize_order(order))

    def delete(self, request, slug, order_id):
        """Cancel/remove an order.

        Hard-delete for draft / pending_payment / cancelled / rejected / refunded
        — these never reached the provider or have already settled. For queued /
        in_progress we soft-delete by flipping status to cancelled, since the
        provider may have started work. Delivered orders refuse deletion (the
        user has already received the link).
        """
        from ..models import BacklinkOrder

        order = self._get_order(slug, order_id)
        terminal_safe = {
            BacklinkOrder.Status.DRAFT,
            BacklinkOrder.Status.PENDING_PAYMENT,
            BacklinkOrder.Status.CANCELLED,
            BacklinkOrder.Status.REJECTED,
            BacklinkOrder.Status.REFUNDED,
        }
        if order.status == BacklinkOrder.Status.DELIVERED:
            return Response(
                {"detail": "Delivered orders can't be deleted — they've already been placed."},
                status=400,
            )
        if order.status in terminal_safe:
            order.delete()
            return Response({"deleted": True, "id": order_id})
        # queued / in_progress — soft-cancel.
        order.status = BacklinkOrder.Status.CANCELLED
        order.save(update_fields=["status"])
        return Response(_serialize_order(order))

class BacklinkOrderConfirmPaymentView(APIView):
    """
    POST /runs/s/<slug>/backlinks/orders/<int:order_id>/confirm-payment/

    Finalises a pending_payment order. In production this would be called after
    a successful Stripe payment intent; for now it acts as a mock-checkout
    confirmation that releases the order to the provider.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug, order_id):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.backlink_providers import get_client

        from ..models import BacklinkOrder

        run = get_object_or_404(AnalysisRun, slug=slug)
        order = get_object_or_404(
            BacklinkOrder.objects.select_related("provider", "product"),
            id=order_id,
            analysis_run=run,
        )

        if order.status != BacklinkOrder.Status.PENDING_PAYMENT:
            return Response(
                {"detail": f"Order is already {order.status}; cannot confirm payment."},
                status=status.HTTP_409_CONFLICT,
            )

        payment_intent_id = (request.data.get("payment_intent_id") or "").strip()

        try:
            client = get_client(order.provider.slug)
            result = client.place_order(
                sku=order.product.sku,
                target_url=order.target_url,
                anchor_text=order.anchor_text,
                notes=order.notes_for_provider,
            )
        except Exception as exc:
            logger.exception("Backlink order place_order failed: %s", exc)
            return Response(
                {"detail": f"Provider rejected the order: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        order.status = result.status or BacklinkOrder.Status.QUEUED
        order.provider_order_id = result.provider_order_id or ""
        order.ordered_at = timezone.now()
        if payment_intent_id:
            order.payment_intent_id = payment_intent_id[:120]
        order.save(update_fields=["status", "provider_order_id", "ordered_at", "payment_intent_id"])

        return Response(_serialize_order(order))

class RunBacklinkFreeView(APIView):
    """Site-level free backlink opportunities (no prompt required).

    GET  /runs/s/<slug>/backlinks/free/   — return cached list (generates on
                                            first call if cache empty).
    POST /runs/s/<slug>/backlinks/free/   — force-regenerate via LLM.

    Results are cached in `BrandKit.payload['site_backlink_opportunities']`
    so reload doesn't re-LLM. The LLM call itself is ~5-15 s; users hit POST
    when they want fresh suggestions.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import BrandKit
        from ..pipeline.site_backlink_opportunities import generate_for_run

        run = get_object_or_404(AnalysisRun, slug=slug)
        kit, _ = BrandKit.objects.get_or_create(analysis_run=run, defaults={"payload": {}})
        cached = (kit.payload or {}).get("site_backlink_opportunities")
        if cached:
            return Response({"rows": cached, "has_generated": True})
        rows = generate_for_run(run)
        if rows:
            kit.payload = {**(kit.payload or {}), "site_backlink_opportunities": rows}
            kit.save(update_fields=["payload", "updated_at"])
        return Response({"rows": rows, "has_generated": bool(rows)})

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import BrandKit
        from ..pipeline.site_backlink_opportunities import generate_for_run

        run = get_object_or_404(AnalysisRun, slug=slug)
        rows = generate_for_run(run)
        if not rows:
            return Response(
                {"detail": "Generation failed. Try again in a moment."},
                status=502,
            )
        kit, _ = BrandKit.objects.get_or_create(analysis_run=run, defaults={"payload": {}})
        kit.payload = {**(kit.payload or {}), "site_backlink_opportunities": rows}
        kit.save(update_fields=["payload", "updated_at"])
        return Response({"rows": rows, "has_generated": True})

class DomainRatingFreeView(APIView):
    """POST /api/analyzer/tools/domain-rating/

    Public, no-auth Domain Rating tool for the marketing site. Body: {domain}.
    Validates the domain format, then returns a 0-100 Domain Rating plus the
    domain's global rank, sourced from the free Open PageRank API.

    Results are cached per-domain for 7 days; the endpoint is throttled to keep
    abuse off the upstream free-tier quota.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        from apps.integrations.services.openpagerank import (
            OpenPageRankError,
            OpenPageRankNotConfigured,
        )

        from ..services.domain_rating import InvalidDomain, get_or_generate

        domain = (request.data.get("domain") or "").strip()
        if not domain:
            return Response(
                {"detail": "A domain is required.", "code": "invalid_domain"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            return Response(get_or_generate(domain))
        except InvalidDomain as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_domain"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except OpenPageRankNotConfigured:
            return Response(
                {
                    "detail": "Domain rating is temporarily unavailable.",
                    "code": "openpagerank_not_configured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except OpenPageRankError:
            return Response(
                {
                    "detail": "Couldn't fetch domain rating right now. Try again shortly.",
                    "code": "openpagerank_upstream",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

class OurBacklinksView(APIView):
    """GET /runs/s/<slug>/backlinks/our/ — backlinks created for this brand by
    publishing blogs to the satellite network (read from the shared blog DB)."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.conf import settings as dj_settings
        from django.shortcuts import get_object_or_404

        from . import blog_store

        run = get_object_or_404(AnalysisRun, slug=slug)
        rows = []
        try:
            for p in blog_store.list_for_brand(_brand_ref_for_run(run)):
                domain = (dj_settings.SATELLITE_SITES.get(p.get("site")) or "").rstrip("/")
                rows.append(
                    {
                        "id": p.get("id"),
                        "site": p.get("site"),
                        "category": p.get("site"),
                        "slug": p.get("slug"),
                        "title": p.get("title"),
                        "url": f"{domain}/{p.get('slug')}" if domain else "",
                        "brand_url": p.get("brand_url", ""),
                        "status": p.get("status", "published"),
                        "published_at": p.get("published_at"),
                    }
                )
        except Exception as exc:  # S3 not configured / unreachable → empty list
            logger.warning("our-backlinks: S3 read failed for %s: %s", slug, exc)
        return Response({"rows": rows, "can_add_today": _auto_can_add_today(run)})

class BacklinkScheduleView(APIView):
    """GET/POST /runs/s/<slug>/backlinks/schedule/ — the per-brand DAILY
    auto-backlinks schedule. When active, ``run_backlink_schedules`` publishes a
    fresh 5-site batch every 24h. Keyed per brand (organization, else email)."""

    permission_classes = [AllowAny]

    @staticmethod
    def _lookup(run):
        """Existing BacklinkSchedule for this run's brand, or None."""
        from ..models import BacklinkSchedule

        if run.organization_id:
            return BacklinkSchedule.objects.filter(organization_id=run.organization_id).first()
        email = (run.email or "").strip()
        if email:
            return BacklinkSchedule.objects.filter(organization__isnull=True, email=email).first()
        return None

    @staticmethod
    def _serialize(sched):
        if not sched:
            return {
                "is_active": False,
                "next_run_at": None,
                "last_run_at": None,
                "last_batch_count": 0,
            }
        return {
            "is_active": sched.is_active,
            "next_run_at": sched.next_run_at,
            "last_run_at": sched.last_run_at,
            "last_batch_count": sched.last_batch_count,
        }

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        run = get_object_or_404(AnalysisRun, slug=slug)
        return Response(self._serialize(self._lookup(run)))

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404
        from django.utils import timezone

        from ..models import BacklinkSchedule

        run = get_object_or_404(AnalysisRun, slug=slug)
        is_active = bool(request.data.get("is_active", True))

        sched = self._lookup(run)
        if sched is None:
            sched = BacklinkSchedule(
                organization_id=run.organization_id or None,
                email=(run.email or "").strip(),
            )

        sched.is_active = is_active
        sched.run_slug = run.slug
        if is_active and (sched.next_run_at is None or not sched.pk):
            # Fire on the next cron tick; the daily gate skips it if a batch
            # already published today for this brand.
            sched.next_run_at = timezone.now()
        elif sched.next_run_at is None:
            sched.next_run_at = timezone.now()
        sched.save()

        return Response(self._serialize(sched))
