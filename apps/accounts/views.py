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

from .models import Subscription

logger = logging.getLogger("apps")

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def _get_dodo():
    """Initialize Dodo Payments client."""
    from dodopayments import DodoPayments
    key = os.getenv("DODO_API_KEY", "")
    if not key:
        return None
    is_live = os.getenv("DODO_LIVE_MODE", "false").lower() in ("true", "1", "yes")
    return DodoPayments(bearer_token=key, live_mode=is_live)


class CreateCheckoutSessionView(APIView):
    """POST /api/payments/create-checkout/"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        dodo = _get_dodo()
        if not dodo:
            return Response({"error": "Payment system not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        product_id = os.getenv("DODO_PRODUCT_ID", "")
        if not product_id:
            return Response({"error": "Payment product not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Get or create subscription record
        sub, _ = Subscription.objects.get_or_create(email=email)

        try:
            # Dodo handles currency automatically based on user location
            subscription = dodo.subscriptions.create(
                customer={
                    "email": email,
                    "name": email.split("@")[0],
                },
                product_id=product_id,
                return_url=f"{FRONTEND_URL}/payments/success",
                metadata={
                    "email": email,
                },
            )

            # Store Dodo IDs
            if hasattr(subscription, "customer") and subscription.customer:
                sub.payment_customer_id = getattr(subscription.customer, "customer_id", "") or ""
            sub.payment_subscription_id = subscription.subscription_id or ""
            sub.save(update_fields=["payment_customer_id", "payment_subscription_id"])

            return Response({"checkout_url": subscription.payment_link})
        except Exception as e:
            logger.exception("Dodo checkout error")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SubscriptionStatusView(APIView):
    """GET /api/payments/status/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
        if os.getenv("DISABLE_PAYMENT", "").lower() in ("true", "1", "yes"):
            return Response({
                "is_active": True,
                "status": "active",
                "current_period_end": None,
                "currency": "usd",
            })

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sub = Subscription.objects.get(email=email)
            return Response({
                "is_active": sub.is_active,
                "status": sub.status,
                "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                "currency": sub.currency,
            })
        except Subscription.DoesNotExist:
            return Response({
                "is_active": False,
                "status": "none",
                "current_period_end": None,
                "currency": "usd",
            })


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

        next_billing = data.get("next_billing_date")
        if next_billing:
            from dateutil.parser import parse as parse_date
            try:
                sub.current_period_end = parse_date(next_billing)
            except (ValueError, TypeError):
                pass

        sub.save(update_fields=["payment_subscription_id", "payment_customer_id", "status", "current_period_end"])
        logger.info("Subscription activated for %s", email)

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

        orgs = Organization.objects.filter(email=email)
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
