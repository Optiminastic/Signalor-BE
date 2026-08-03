"""Shopify: OAuth, billing, uninstall webhook, sync, data."""

import hmac
import os
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.subscription_utils import (
    integration_connect_allowed_for_email,
)
from apps.organizations.models import Organization
from core.permissions.throttling import ExpensiveThrottle

from ..models import (
    Integration,
)
from ..serializers import (
    IntegrationSerializer,
    ShopifyConnectSerializer,
    ShopifyDataSnapshotSerializer,
)
from ._shared import (
    _append_query_params,
    _deactivate_other_store_integration,
    _get_org_or_400,
    _resolve_org,
    _resolve_shopify_redirect_uri,
    _sign_state,
    _verify_state,
    logger,
)


class ShopifyConnectView(APIView):
    """POST /api/integrations/shopify/connect/"""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ShopifyConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, err = _get_org_or_400(data["email"])
        if err:
            return err

        allowed, sub_err = integration_connect_allowed_for_email(data["email"])
        if not allowed:
            return Response(
                {"error": sub_err},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Validate against Shopify API
        from ..services.shopify import validate_shopify_connection

        try:
            shop_info = validate_shopify_connection(data["shop_domain"], data["access_token"])
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or update integration
        integration, _ = Integration.objects.update_or_create(
            organization=org,
            provider=Integration.Provider.SHOPIFY,
            defaults={"is_active": True},
        )
        integration.set_access_token(data["access_token"])
        integration.metadata = {
            "shop_domain": data["shop_domain"],
            "shop_name": shop_info.get("name", data["shop_domain"]),
        }
        integration.save()
        _deactivate_other_store_integration(org, Integration.Provider.SHOPIFY)

        return Response(
            {
                "message": "Shopify connected successfully.",
                "integration": IntegrationSerializer(integration).data,
            }
        )

class ShopifyAuthURLView(APIView):
    """GET /api/integrations/shopify/auth-url/?email=&shop=&org_id=&return_to="""

    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        shop = request.query_params.get("shop", "").strip()
        return_to = request.query_params.get("return_to", "").strip() or "/settings/integrations"
        frontend_base = request.query_params.get("frontend_base", "").strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None
        storefront_password = request.query_params.get("storefront_password", "").strip()

        if not email or not shop:
            return Response(
                {"error": "Both email and shop parameters are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        allowed, sub_err = integration_connect_allowed_for_email(email)
        if not allowed:
            return Response(
                {"error": sub_err},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Prefer the frontend origin supplied by the caller so local/prod
        # environments redirect back to the same place the flow started.
        parsed_frontend = urlparse(frontend_base) if frontend_base else None
        if parsed_frontend and parsed_frontend.scheme in ("http", "https") and parsed_frontend.netloc:
            resolved_frontend_base = f"{parsed_frontend.scheme}://{parsed_frontend.netloc}"
        else:
            resolved_frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")

        from ..services.shopify import (
            build_shopify_admin_install_custom_app_url,
            build_shopify_oauth_url,
            normalize_shop_domain,
        )

        shop_domain = normalize_shop_domain(shop)
        nonce = secrets.token_urlsafe(24)
        payload = {
            "org_id": org.id,
            "email": email,
            "shop_domain": shop_domain,
            "nonce": nonce,
            "return_to": return_to,
            "frontend_base": resolved_frontend_base,
            "storefront_password": storefront_password,
        }
        cache.set(f"shopify_oauth_state:{nonce}", payload, timeout=15 * 60)
        state = _sign_state(payload)

        client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
        redirect_uri = _resolve_shopify_redirect_uri(request)
        scopes = os.getenv("SHOPIFY_SCOPES", "read_products,read_orders,read_customers")

        if not client_id:
            return Response(
                {"error": "Shopify OAuth env is not configured."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Custom apps: Shopify Admin gives a one-off install URL
        # (admin.shopify.com/oauth/install_custom_app?...&signature=...).
        # Signatures expire and are tied to the store — paste a fresh URL from
        # Shopify → Settings → Apps → Develop apps → your app → Install.
        # We append `state` so /api/integrations/shopify/callback/ can still
        # validate (Shopify forwards it on redirect when supported).
        custom_install = os.getenv("SHOPIFY_CUSTOM_APP_INSTALL_URL", "").strip()
        if custom_install:
            auth_url = _append_query_params(custom_install, {"state": state})
            return Response({"auth_url": auth_url})

        scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
        use_install_custom = os.getenv("SHOPIFY_OAUTH_USE_INSTALL_CUSTOM_APP", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if use_install_custom:
            auth_url = build_shopify_admin_install_custom_app_url(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                scopes=scope_list,
            )
            return Response({"auth_url": auth_url})

        auth_url = build_shopify_oauth_url(
            shop_domain=shop_domain,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            scopes=scope_list,
        )
        logger.info(
            "Shopify OAuth auth-url: shop=%s redirect_uri=%s",
            shop_domain,
            redirect_uri,
        )
        return Response(
            {
                "auth_url": auth_url,
                "oauth_redirect_uri": redirect_uri,
            }
        )

class ShopifyCallbackView(APIView):
    """GET /api/integrations/shopify/callback/"""

    permission_classes = [AllowAny]

    def get(self, request):
        from ..services.shopify import (
            exchange_shopify_oauth_code,
            normalize_shop_domain,
            register_app_uninstalled_webhook,
            validate_shopify_connection,
            verify_shopify_oauth_hmac,
        )

        query_string = request.META.get("QUERY_STRING", "")
        shop = request.query_params.get("shop", "").strip()
        code = request.query_params.get("code", "").strip()
        state_str = request.query_params.get("state", "").strip()

        frontend_base = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
        default_return_to = "/settings/integrations"

        def _shopify_redirect(ok: bool, reason: str = "", return_to: str = default_return_to):
            target = (
                return_to
                if return_to.startswith("/") and not return_to.startswith("//")
                else default_return_to
            )
            status_q = "connected" if ok else "error"
            # Replace any existing shopify/reason keys instead of appending —
            # the FE-supplied return_to may already carry `shopify=installed`
            # (the in-progress install flag), and naive appending produces
            # ?shopify=installed&shopify=connected which the FE treats as a
            # bad URL.
            parts = urlparse(target)
            pairs = [
                (k, v)
                for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k not in ("shopify", "reason")
            ]
            pairs.append(("shopify", status_q))
            pairs.append(("reason", reason))
            target_rewritten = urlunparse(
                (parts.scheme, parts.netloc, parts.path, parts.params, urlencode(pairs), parts.fragment)
            )
            return HttpResponseRedirect(f"{frontend_base}{target_rewritten}")

        if not shop or not code or not state_str:
            return _shopify_redirect(False, "missing_params")

        payload = _verify_state(state_str)
        if not payload:
            return _shopify_redirect(False, "invalid_state")

        frontend_base = (payload.get("frontend_base") or frontend_base).rstrip("/")

        return_to = payload.get("return_to", default_return_to)
        nonce = payload.get("nonce", "")
        cached_payload = cache.get(f"shopify_oauth_state:{nonce}") if nonce else None
        if not nonce or not cached_payload:
            return _shopify_redirect(False, "expired_state", return_to=return_to)
        cache.delete(f"shopify_oauth_state:{nonce}")

        client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
        client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
        if not client_id or not client_secret:
            return _shopify_redirect(False, "oauth_not_configured", return_to=return_to)

        if not verify_shopify_oauth_hmac(query_string, client_secret):
            return _shopify_redirect(False, "invalid_hmac", return_to=return_to)

        shop_domain = normalize_shop_domain(shop)
        # Skip shop mismatch check — Shopify may redirect through a different
        # myshopify.com subdomain than the one the user entered (e.g. custom
        # domains or admin-generated handles like ayx0fj-ze vs arkit-4).

        org_id = payload.get("org_id")
        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return _shopify_redirect(False, "org_not_found", return_to=return_to)

        oauth_email = (cached_payload.get("email") or "").lower().strip()
        allowed, _ = integration_connect_allowed_for_email(oauth_email)
        if not allowed:
            return _shopify_redirect(
                False,
                "subscription_required",
                return_to=return_to,
            )

        try:
            tokens = exchange_shopify_oauth_code(
                shop_domain=shop_domain,
                client_id=client_id,
                client_secret=client_secret,
                code=code,
            )
            access_token = tokens.get("access_token", "")
            if not access_token:
                return _shopify_redirect(False, "missing_access_token", return_to=return_to)

            shop_info = validate_shopify_connection(shop_domain, access_token)

            integration, _ = Integration.objects.update_or_create(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                defaults={"is_active": True},
            )
            integration.set_access_token(access_token)
            # Auto-link Shopify app — use shared app secret for HMAC auth
            shopify_app_url = os.getenv("SIGNALOR_SHOPIFY_APP_URL", "").strip()
            integration.metadata = {
                "shop_domain": shop_domain,
                "shop_name": shop_info.get("name", shop_domain),
                "scope": tokens.get("scope", ""),
                "signalor_app_url": shopify_app_url,
                "signalor_hmac_secret": os.getenv("SHOPIFY_CLIENT_SECRET", ""),
                "storefront_password": payload.get("storefront_password", ""),
            }
            integration.save()
            _deactivate_other_store_integration(org, Integration.Provider.SHOPIFY)

            # Sync session to the Shopify Remix app so it can execute fixes
            if shopify_app_url:
                try:
                    import hashlib as _hashlib
                    import hmac as _hmac
                    import json as _json

                    sync_payload = {
                        "shop": shop_domain,
                        "accessToken": access_token,
                        "scope": tokens.get("scope", ""),
                    }
                    sync_body = _json.dumps(sync_payload).encode("utf-8")
                    hmac_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "")
                    sync_sig = _hmac.new(hmac_secret.encode(), sync_body, _hashlib.sha256).hexdigest()

                    requests.post(
                        f"{shopify_app_url}/api/sync-session",
                        headers={
                            "X-Signalor-Signature": sync_sig,
                            "X-Signalor-Shop": shop_domain,
                            "Content-Type": "application/json",
                        },
                        data=sync_body,
                        timeout=10,
                    )
                    logger.info("Session synced to Shopify app for %s", shop_domain)
                except Exception as sync_exc:
                    logger.warning("Session sync to Shopify app failed (non-fatal): %s", sync_exc)

            # Keep org URL in sync for GEO analysis auto-start
            primary_domain = shop_info.get("domain") or shop_info.get("myshopify_domain") or shop_domain
            store_url = (
                primary_domain if str(primary_domain).startswith("http") else f"https://{primary_domain}"
            )
            if org.url != store_url:
                org.url = store_url
                org.save(update_fields=["url"])

            # Do not fail OAuth if webhook registration fails (network, wrong URL, dev vs prod).
            webhook_url = os.getenv("SHOPIFY_APP_UNINSTALLED_WEBHOOK_URL", "").strip()
            if webhook_url:
                try:
                    register_app_uninstalled_webhook(shop_domain, access_token, webhook_url)
                except Exception as webhook_exc:
                    logger.warning(
                        "Shopify app/uninstalled webhook skipped (non-fatal): %s",
                        webhook_exc,
                    )

        except ValueError as exc:
            # exchange_shopify_oauth_code / validate_shopify_connection
            err = str(exc).lower()
            if "token exchange" in err or "failed token exchange" in err:
                reason = "token_exchange_failed"
            elif "shopify_shop_frozen" in err:
                reason = "shop_frozen"
            else:
                reason = "shopify_api_error"
            logger.warning("Shopify OAuth validation: %s", exc)
            return _shopify_redirect(False, reason, return_to=return_to)

        except Exception:
            logger.exception("Shopify callback failed")
            return _shopify_redirect(False, "callback_failed", return_to=return_to)

        return _shopify_redirect(True, return_to=return_to)

class ShopifyBillingUpdateView(APIView):
    """POST /api/integrations/shopify/billing-update/

    Receives merchant subscription state changes from the Signalor Shopify
    app (which subscribes to Shopify's `app_subscriptions/update` webhook).
    Updates the merchant's Signalor-side Subscription record so feature
    access stays in sync with their Shopify-billed plan.

    Auth: shared secret in the `X-Signalor-Webhook-Secret` header. Set
    `SHOPIFY_APP_WEBHOOK_SECRET` on both this backend and the Shopify app's
    deploy env to the same value.

    Body (JSON):
      {"shop": "example.myshopify.com", "plan": "Pro", "status": "ACTIVE"}

    plan      Starter | Pro | Max
    status    ACTIVE | PENDING | ACCEPTED | DECLINED | EXPIRED | FROZEN | CANCELLED
    """

    permission_classes = [AllowAny]

    # Shopify plan id (sent by the Shopify app) → Signalor Subscription.Plan value
    PLAN_MAP = {
        "Starter": "starter",
        "Pro": "pro",
        "Max": "business",
    }

    # Shopify subscription state → Signalor Subscription.Status value
    STATUS_MAP = {
        "ACTIVE": "active",
        "ACCEPTED": "active",
        "PENDING": "unpaid",
        "DECLINED": "unpaid",
        "EXPIRED": "unpaid",
        "FROZEN": "past_due",
        "CANCELLED": "canceled",
    }

    def post(self, request):
        from apps.accounts.models import Subscription

        from ..services.shopify import normalize_shop_domain

        expected_secret = os.getenv("SHOPIFY_APP_WEBHOOK_SECRET", "").strip()
        provided_secret = request.headers.get("X-Signalor-Webhook-Secret", "").strip()
        if not expected_secret or not hmac.compare_digest(expected_secret, provided_secret):
            return Response({"error": "Unauthorized."}, status=status.HTTP_401_UNAUTHORIZED)

        shop_raw = (request.data.get("shop") or "").strip()
        plan_raw = (request.data.get("plan") or "").strip()
        status_raw = (request.data.get("status") or "").strip().upper()

        if not shop_raw or not status_raw:
            return Response(
                {"error": "shop and status are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        signalor_status = self.STATUS_MAP.get(status_raw, "unpaid")
        signalor_plan = self.PLAN_MAP.get(plan_raw) if plan_raw else None

        # Find the Signalor account by matching the connected Shopify integration.
        shop_domain = normalize_shop_domain(shop_raw)
        integration = (
            Integration.objects.filter(
                provider=Integration.Provider.SHOPIFY,
                metadata__shop_domain=shop_domain,
            )
            .select_related("organization")
            .first()
        )

        if not integration:
            # The merchant approved billing on Shopify but hasn't connected the
            # Shopify integration to a Signalor workspace yet. We can't resolve
            # them to an email. ACK so Shopify doesn't retry — the next time the
            # merchant connects/syncs, the entitlement will be reconciled.
            logger.warning(
                "shopify-billing-update: no integration for shop=%s status=%s plan=%s",
                shop_domain,
                status_raw,
                plan_raw,
            )
            return Response({"ok": True, "matched": False})

        owner_email = (integration.organization.owner_email or "").lower().strip()
        if not owner_email:
            logger.error(
                "shopify-billing-update: integration org has no owner_email shop=%s org=%s",
                shop_domain,
                integration.organization_id,
            )
            return Response({"ok": True, "matched": False})

        sub, created = Subscription.objects.get_or_create(email=owner_email)

        sub.status = signalor_status
        if signalor_plan:
            sub.plan = signalor_plan
        # Tag the source so future Dodo webhooks don't fight Shopify-side state.
        # `payment_subscription_id` is normally Dodo's; we prefix to disambiguate.
        sub.payment_customer_id = f"shopify:{shop_domain}"
        sub.currency = "usd"  # Shopify billing config is USD; can be widened later
        sub.save(
            update_fields=[
                "plan",
                "status",
                "payment_customer_id",
                "currency",
                "updated_at",
            ]
        )

        logger.info(
            "shopify-billing-update: shop=%s email=%s plan=%s status=%s (was_new=%s)",
            shop_domain,
            owner_email,
            sub.plan,
            sub.status,
            created,
        )
        return Response({"ok": True, "email": owner_email, "plan": sub.plan, "status": sub.status})

class ShopifyAppUninstalledWebhookView(APIView):
    """POST /api/integrations/shopify/webhooks/app-uninstalled/"""

    permission_classes = [AllowAny]

    def post(self, request):
        from ..services.shopify import normalize_shop_domain, verify_shopify_webhook_hmac

        secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
        hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")
        shop_header = request.headers.get("X-Shopify-Shop-Domain", "")
        if not secret or not hmac_header or not shop_header:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if not verify_shopify_webhook_hmac(request.body, hmac_header, secret):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        shop_domain = normalize_shop_domain(shop_header)
        integration = Integration.objects.filter(
            provider=Integration.Provider.SHOPIFY,
            metadata__shop_domain=shop_domain,
        ).first()

        if integration:
            integration.shopify_snapshots.all().delete()
            integration.delete()

        return Response({"message": "Processed."}, status=status.HTTP_200_OK)

class ShopifyDisconnectView(APIView):
    """DELETE /api/integrations/shopify/disconnect/?email=&org_id="""

    permission_classes = [AllowAny]

    def delete(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify integration not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        integration.shopify_snapshots.all().delete()
        integration.delete()

        return Response({"message": "Shopify disconnected."})

class ShopifySyncView(APIView):
    """POST /api/integrations/shopify/sync/?email=&org_id="""

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request):
        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..tasks import start_shopify_sync

        start_shopify_sync(integration.id)

        return Response({"message": "Sync started."}, status=status.HTTP_202_ACCEPTED)

class ShopifyDataView(APIView):
    """GET /api/integrations/shopify/data/?email=&org_id="""

    permission_classes = [AllowAny]

    def get(self, request):
        from datetime import timedelta

        from django.utils import timezone

        email = request.query_params.get("email", "").lower().strip()
        org_id = request.query_params.get("org_id")
        org_id = int(org_id) if org_id and org_id.isdigit() else None

        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        org, err = _resolve_org(email, org_id)
        if err:
            return err

        try:
            integration = Integration.objects.get(
                organization=org,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": "Shopify not connected."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Cleanup old snapshots
        cutoff = timezone.now() - timedelta(days=90)
        integration.shopify_snapshots.filter(created_at__lt=cutoff).delete()

        snapshot = integration.shopify_snapshots.first()
        if not snapshot:
            return Response(
                {"error": "No data available. Trigger a sync first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Clear any dead "syncing" row (killed worker) so it can't wedge auto-sync.
        from ..sync_health import reap_stale_syncing

        reap_stale_syncing(integration.shopify_snapshots)

        # Auto-sync if stale
        stale_threshold = timezone.now() - timedelta(hours=24)
        if (
            snapshot.created_at < stale_threshold
            and snapshot.sync_status == "complete"
            and not integration.shopify_snapshots.filter(sync_status="syncing").exists()
        ):
            from ..tasks import start_shopify_sync

            start_shopify_sync(integration.id)

        serializer = ShopifyDataSnapshotSerializer(snapshot)
        return Response(serializer.data)

class ShopifyLinkAppView(APIView):
    """POST /api/integrations/shopify/link-app/ — Link the Signalor Shopify app to backend.

    Called by the Shopify Remix app after install. Exchanges HMAC secret and stores
    the app URL so the backend can send fix instructions to the app.

    Body: { "shop_domain": "store.myshopify.com", "app_url": "https://...", "hmac_secret": "..." }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        shop_domain = request.data.get("shop_domain", "").strip()
        app_url = request.data.get("app_url", "").strip()
        hmac_secret = request.data.get("hmac_secret", "").strip()

        if not shop_domain or not app_url or not hmac_secret:
            return Response(
                {"error": "shop_domain, app_url, and hmac_secret are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find the Shopify integration for this shop
        try:
            integration = Integration.objects.get(
                metadata__shop_domain=shop_domain,
                provider=Integration.Provider.SHOPIFY,
                is_active=True,
            )
        except Integration.DoesNotExist:
            return Response(
                {"error": f"No active Shopify integration found for {shop_domain}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Store the app URL and HMAC secret in metadata
        meta = integration.metadata or {}
        meta["signalor_app_url"] = app_url.rstrip("/")
        meta["signalor_hmac_secret"] = hmac_secret
        integration.metadata = meta
        integration.save(update_fields=["metadata"])

        return Response(
            {
                "status": "linked",
                "shop_domain": shop_domain,
                "message": "Shopify app linked. Fix instructions will now be routed through the app.",
            }
        )

