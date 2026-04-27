import hashlib
import hmac
import json
import logging
import os

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from datetime import timedelta

from .models import Subscription, PLAN_LIMITS
from .subscription_utils import is_internal_email
from dodopayments import AuthenticationError, PermissionDeniedError

from .dodo_env import (
    dodo_live_mode_enabled,
    dodo_mode_public,
    normalized_dodo_api_key,
)


def _dodo_opposite_mode_hint() -> str:
    """
    If checkout failed with 401 in the configured mode, probe the other Dodo environment
    with the same key (read-only products.list). When that succeeds, tell the user to flip
    DODO_LIVE_MODE — common misconfiguration (test key + live mode).
    """
    from dodopayments import AuthenticationError, DodoPayments

    key = normalized_dodo_api_key()
    if not key:
        return ""
    try:
        if dodo_live_mode_enabled():
            alt = DodoPayments(bearer_token=key, environment="test_mode")
            next(iter(alt.products.list(page_size=1)), None)
            return (
                " This key works in Dodo TEST mode. Set DODO_LIVE_MODE=false and use "
                "product ids from the Test dashboard (or switch to a Live secret key)."
            )
        alt = DodoPayments(bearer_token=key, environment="live_mode")
        next(iter(alt.products.list(page_size=1)), None)
        return (
            " This key works in Dodo LIVE mode. Set DODO_LIVE_MODE=true and use "
            "product ids from the Live dashboard (or switch to a Test secret key)."
        )
    except AuthenticationError:
        return ""
    except Exception:
        return ""
from .dodo_invoice import extract_payment_id_from_webhook, fetch_payment_invoice_pdf

logger = logging.getLogger("apps")

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def _get_dodo():
    """Initialize Dodo Payments client (official SDK uses environment= test_mode | live_mode)."""
    from dodopayments import DodoPayments

    key = normalized_dodo_api_key()
    if not key:
        return None
    environment = "live_mode" if dodo_live_mode_enabled() else "test_mode"
    return DodoPayments(bearer_token=key, environment=environment)


class CreateCheckoutSessionView(APIView):
    """POST /api/payments/create-checkout/"""

    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        plan = request.data.get("plan", "starter").lower().strip()

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if plan not in ("starter", "pro", "business"):
            return Response({"error": "Invalid plan."}, status=status.HTTP_400_BAD_REQUEST)

        dodo = _get_dodo()
        if not dodo:
            return Response({"error": "Payment system not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        product_map = {
            "starter": os.getenv("DODO_PRODUCT_ID_STARTER", os.getenv("DODO_PRODUCT_ID", "")).strip(),
            "pro": os.getenv("DODO_PRODUCT_ID_PRO", "").strip(),
            "business": os.getenv("DODO_PRODUCT_ID_BUSINESS", "").strip(),
        }
        product_id = product_map.get(plan, "")
        if not product_id:
            return Response({"error": f"Product not configured for {plan} plan."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        sub, _ = Subscription.objects.get_or_create(email=email)

        try:
            # Current Dodo Python SDK: checkout_sessions (not subscriptions.create with payment_link).
            checkout_session = dodo.checkout_sessions.create(
                product_cart=[{"product_id": product_id, "quantity": 1}],
                return_url=f"{FRONTEND_URL}/payments/success",
                customer={
                    "email": email,
                    "name": email.split("@")[0].replace(".", " ").title() or "Customer",
                },
                metadata={"email": email, "plan": plan},
            )
            sub.plan = plan
            sub.save(update_fields=["plan"])

            checkout_url = getattr(checkout_session, "checkout_url", None) or getattr(
                checkout_session, "url", None
            )
            if not checkout_url:
                logger.error("Dodo checkout session missing checkout_url: %s", checkout_session)
                return Response(
                    {"error": "Checkout session created but no redirect URL returned."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            return Response({"checkout_url": checkout_url})
        except AuthenticationError:
            mode = dodo_mode_public()
            diag = _dodo_opposite_mode_hint()
            logger.warning(
                "Dodo checkout 401: key/write-access/mode mismatch (dodo_mode=%s)",
                mode,
            )
            return Response(
                {
                    "error": (
                        "Dodo returned 401 (unauthorized). Use a secret API key from the "
                        f"same Dodo dashboard mode as this server ({mode}): "
                        "Developer → API Keys, with write access enabled. "
                        "Set DODO_API_KEY or DODO_PAYMENTS_API_KEY to the raw token only (no 'Bearer '). "
                        "Product ids (DODO_PRODUCT_ID_*) must exist in that same mode. "
                        "Restart Django after editing ranking-be/.env."
                        + diag
                    ),
                    "dodo_mode": mode,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except PermissionDeniedError as e:
            mode = dodo_mode_public()
            logger.warning("Dodo checkout 403: likely read-only API key (%s)", e)
            return Response(
                {
                    "error": (
                        "Dodo returned 403 (forbidden). The API key may be read-only. "
                        "Create a new key in Developer → API Keys with write access enabled, "
                        "then update DODO_API_KEY in ranking-be/.env and restart Django."
                    ),
                    "dodo_mode": mode,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception("Dodo checkout error")
            return Response(
                {
                    "error": str(e),
                    "dodo_mode": dodo_mode_public(),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SubscriptionStatusView(APIView):
    """GET /api/payments/status/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        starter = PLAN_LIMITS["starter"]
        business = PLAN_LIMITS["business"]

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        # @optiminastic.com → free unlimited access (business tier)
        if is_internal_email(email):
            return Response({
                "is_active": True,
                "status": "active",
                "current_period_end": None,
                "currency": "gbp",
                "plan": "business",
                "plan_label": "Max (Internal)",
                "limits": business,
                "invoice_available": False,
            })

        try:
            sub = Subscription.objects.get(email=email)
            return Response({
                "is_active": sub.is_active,
                "status": sub.status,
                "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                "currency": sub.currency,
                "plan": sub.plan,
                "plan_label": sub.limits["label"],
                "limits": sub.limits,
                "invoice_available": bool(sub.last_invoice_payment_id),
            })
        except Subscription.DoesNotExist:
            return Response({
                "is_active": False,
                "status": "none",
                "current_period_end": None,
                "currency": "gbp",
                "plan": "starter",
                "plan_label": starter["label"],
                "limits": starter,
                "invoice_available": False,
            })


class DownloadInvoiceView(APIView):
    """GET /api/payments/invoice/?email= — PDF for latest stored Dodo payment."""

    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        if is_internal_email(email):
            return Response({"error": "No invoice for internal accounts."}, status=status.HTTP_404_NOT_FOUND)

        try:
            sub = Subscription.objects.get(email=email)
        except Subscription.DoesNotExist:
            return Response({"error": "No subscription found."}, status=status.HTTP_404_NOT_FOUND)

        if not sub.last_invoice_payment_id:
            return Response(
                {"error": "No invoice yet. It appears after your first successful charge."},
                status=status.HTTP_404_NOT_FOUND,
            )

        pdf, err = fetch_payment_invoice_pdf(sub.last_invoice_payment_id)
        if not pdf:
            logger.warning("Invoice download failed for %s: %s", email, err)
            return Response(
                {"error": "Could not retrieve invoice from payment provider."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        safe_name = sub.last_invoice_payment_id.replace("/", "_")[:80]
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="signalor-invoice-{safe_name}.pdf"'
        )
        return response


@method_decorator(csrf_exempt, name="dispatch")
class DodoWebhookView(APIView):
    """POST /api/payments/webhook/ — Dodo Payments webhook handler.

    Uses Standard Webhooks spec for signature verification.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        webhook_secret = os.getenv("DODO_WEBHOOK_SECRET", "")
        if not webhook_secret:
            logger.warning("DODO_WEBHOOK_SECRET not configured")
            return HttpResponse(status=400)

        # Verify webhook signature (Standard Webhooks spec)
        payload = request.body
        signature = request.META.get("HTTP_WEBHOOK_SIGNATURE", "")
        timestamp = request.META.get("HTTP_WEBHOOK_TIMESTAMP", "")
        msg_id = request.META.get("HTTP_WEBHOOK_ID", "")

        if not signature or not timestamp:
            logger.warning("Missing webhook signature headers")
            return HttpResponse(status=400)

        # Standard Webhooks verification: HMAC-SHA256 of "{msg_id}.{timestamp}.{body}"
        try:
            import base64
            secret_bytes = base64.b64decode(webhook_secret)
            to_sign = f"{msg_id}.{timestamp}.{payload.decode('utf-8')}"
            expected = base64.b64encode(
                hmac.new(secret_bytes, to_sign.encode("utf-8"), hashlib.sha256).digest()
            ).decode("utf-8")

            # Signature header can have multiple sigs: "v1,<sig1> v1,<sig2>"
            sigs = [s.split(",", 1)[-1] for s in signature.split(" ") if "," in s]
            if not any(hmac.compare_digest(expected, s) for s in sigs):
                logger.warning("Dodo webhook signature mismatch")
                return HttpResponse(status=400)
        except Exception as e:
            logger.warning("Dodo webhook verification error: %s", e)
            return HttpResponse(status=400)

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        event_type = event.get("type", "")
        data = event.get("data", {})

        logger.info("Dodo webhook: %s", event_type)

        if event_type == "subscription.active":
            self._handle_subscription_active(data)
        elif event_type == "subscription.renewed":
            self._handle_subscription_renewed(data)
        elif event_type == "subscription.on_hold":
            self._handle_subscription_on_hold(data)
        elif event_type == "subscription.failed":
            self._handle_subscription_failed(data)
        elif event_type == "subscription.cancelled":
            self._handle_subscription_cancelled(data)
        elif event_type == "payment.succeeded":
            self._handle_payment_succeeded(data)

        return HttpResponse(status=200)

    def _handle_subscription_active(self, data):
        """Subscription activated (first payment successful)."""
        email = self._extract_email(data)
        if not email:
            return

        sub, _ = Subscription.objects.get_or_create(email=email)
        sub.payment_subscription_id = data.get("subscription_id", sub.payment_subscription_id)
        sub.payment_customer_id = data.get("customer", {}).get("customer_id", sub.payment_customer_id)
        sub.status = "active"

        # Set plan from metadata if present
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("plan") in ("starter", "pro", "business"):
            sub.plan = metadata["plan"]

        next_billing = data.get("next_billing_date")
        if next_billing:
            from dateutil.parser import parse as parse_date
            try:
                sub.current_period_end = parse_date(next_billing)
            except (ValueError, TypeError):
                pass

        sub.save(update_fields=["payment_subscription_id", "payment_customer_id", "status", "current_period_end", "plan"])
        self._store_latest_payment_id(data, sub)
        logger.info("Subscription activated for %s (plan=%s)", email, sub.plan)

    def _handle_subscription_renewed(self, data):
        """Subscription renewed for next period."""
        sub = self._find_subscription(data)
        if not sub:
            return

        sub.status = "active"
        next_billing = data.get("next_billing_date")
        if next_billing:
            from dateutil.parser import parse as parse_date
            try:
                sub.current_period_end = parse_date(next_billing)
            except (ValueError, TypeError):
                pass

        sub.save(update_fields=["status", "current_period_end"])
        self._store_latest_payment_id(data, sub)
        logger.info("Subscription renewed for %s", sub.email)

    def _handle_subscription_on_hold(self, data):
        """Renewal payment failed — subscription on hold."""
        sub = self._find_subscription(data)
        if not sub:
            return

        sub.status = "past_due"
        sub.save(update_fields=["status"])
        logger.info("Subscription on hold for %s", sub.email)

    def _handle_subscription_failed(self, data):
        """Subscription creation failed."""
        sub = self._find_subscription(data)
        if not sub:
            return

        sub.status = "unpaid"
        sub.save(update_fields=["status"])
        logger.info("Subscription failed for %s", sub.email)

    def _handle_subscription_cancelled(self, data):
        """Subscription cancelled."""
        sub = self._find_subscription(data)
        if not sub:
            return

        sub.status = "canceled"
        sub.save(update_fields=["status"])
        logger.info("Subscription cancelled for %s", sub.email)

    def _handle_payment_succeeded(self, data):
        """One-time payment succeeded — activate if linked to subscription."""
        email = self._extract_email(data)
        if not email:
            return

        sub = None
        try:
            sub = Subscription.objects.get(email=email)
        except Subscription.DoesNotExist:
            pass

        if sub:
            self._store_latest_payment_id(data, sub)

        if sub and sub.status != "active":
            sub.status = "active"
            sub.save(update_fields=["status"])
            logger.info("Payment succeeded, subscription activated for %s", email)

    def _extract_email(self, data):
        """Extract email from webhook data."""
        # Try customer object first
        customer = data.get("customer", {})
        if isinstance(customer, dict):
            email = customer.get("email", "")
            if email:
                return email.lower().strip()

        # Try metadata
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            email = metadata.get("email", "")
            if email:
                return email.lower().strip()

        return ""

    def _find_subscription(self, data):
        """Find subscription by ID or email."""
        sub_id = data.get("subscription_id", "")
        if sub_id:
            try:
                return Subscription.objects.get(payment_subscription_id=sub_id)
            except Subscription.DoesNotExist:
                pass

        email = self._extract_email(data)
        if email:
            try:
                return Subscription.objects.get(email=email)
            except Subscription.DoesNotExist:
                pass

        return None

    def _store_latest_payment_id(self, data, sub):
        """Remember latest Dodo payment id for invoice PDF download."""
        if not sub:
            return
        pid = extract_payment_id_from_webhook(data)
        if not pid:
            return
        if sub.last_invoice_payment_id == pid:
            return
        sub.last_invoice_payment_id = pid
        sub.save(update_fields=["last_invoice_payment_id"])


class UsageView(APIView):
    """GET /api/payments/usage/?email= — current usage vs plan limits."""
    permission_classes = [AllowAny]

    def get(self, request):
        from apps.organizations.models import Organization
        from apps.analyzer.models import PromptTrack, AnalysisRun
        from .subscription_utils import is_internal_email, get_plan_limits

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        limits = get_plan_limits(email)
        is_internal = is_internal_email(email)

        # Projects (orgs) owned by this email
        projects_used = Organization.objects.filter(owner_email=email).count()

        # Prompts tracked across all runs for this email
        prompts_used = PromptTrack.objects.filter(analysis_run__email=email).count()

        # Analysis runs this month
        from django.utils import timezone as tz
        now = tz.now()
        runs_this_month = AnalysisRun.objects.filter(
            email=email,
            created_at__year=now.year,
            created_at__month=now.month,
        ).count()

        # Engines allowed on current plan
        allowed_engines = limits.get("engines", [])

        return Response({
            "plan": "business" if is_internal else (
                _get_sub_plan(email) if not is_internal else "business"
            ),
            "limits": {
                "max_projects": limits["max_projects"],
                "max_prompts": limits["max_prompts"],
                "engines": allowed_engines,
            },
            "usage": {
                "projects": projects_used,
                "prompts": prompts_used,
                "runs_this_month": runs_this_month,
            },
            "at_limit": {
                "projects": projects_used >= limits["max_projects"],
                "prompts": prompts_used >= limits["max_prompts"],
            },
        })


def _get_sub_plan(email: str) -> str:
    """Return plan key for an email, defaulting to starter."""
    try:
        sub = Subscription.objects.get(email=email)
        return sub.plan if sub.is_active else "starter"
    except Subscription.DoesNotExist:
        return "starter"


class PlanListView(APIView):
    """GET /api/plans/ — list all available plans."""
    permission_classes = [AllowAny]

    def get(self, request):
        plans = []
        for key, cfg in PLAN_LIMITS.items():
            plans.append({
                "id": key,
                "label": cfg["label"],
                "price_gbp": cfg["price_gbp"],
                "max_projects": cfg["max_projects"],
                "max_prompts": cfg["max_prompts"],
                "engines": cfg["engines"],
            })
        return Response(plans)


class TerminateAccountView(APIView):
    """POST /api/account/terminate/ — soft delete, deactivates in 24h."""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        sub, _ = Subscription.objects.get_or_create(email=email)

        if sub.deactivated_at:
            return Response({
                "message": "Account already scheduled for deactivation.",
                "deactivated_at": sub.deactivated_at.isoformat(),
            })

        sub.deactivated_at = timezone.now() + timedelta(hours=24)
        sub.save(update_fields=["deactivated_at"])
        logger.info("Account termination scheduled for %s at %s", email, sub.deactivated_at)

        return Response({
            "message": "Account scheduled for deactivation in 24 hours.",
            "deactivated_at": sub.deactivated_at.isoformat(),
        })


class CancelTerminationView(APIView):
    """POST /api/account/cancel-termination/ — cancel soft delete."""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        sub, _ = Subscription.objects.get_or_create(email=email)

        if not sub.deactivated_at:
            return Response({"message": "No pending termination."})

        sub.deactivated_at = None
        sub.save(update_fields=["deactivated_at"])
        logger.info("Account termination cancelled for %s", email)

        return Response({"message": "Termination cancelled. Your account is active."})


class DeleteAccountView(APIView):
    """POST /api/account/delete/ — hard delete account and all data."""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        confirm = request.data.get("confirm", "").lower().strip()

        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        if confirm != "delete my account":
            return Response(
                {"error": "Please type 'delete my account' to confirm."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.organizations.models import Organization
        from apps.analyzer.models import AnalysisRun

        deleted_counts = {}

        runs = AnalysisRun.objects.filter(email=email)
        deleted_counts["analysis_runs"] = runs.count()
        runs.delete()

        orgs = Organization.objects.filter(owner_email=email)
        deleted_counts["organizations"] = orgs.count()
        orgs.delete()

        try:
            sub = Subscription.objects.get(email=email)
            sub.delete()
            deleted_counts["subscription"] = 1
        except Subscription.DoesNotExist:
            deleted_counts["subscription"] = 0

        logger.info("Account permanently deleted for %s: %s", email, deleted_counts)

        return Response({
            "message": "Account permanently deleted.",
            "deleted": deleted_counts,
        })
