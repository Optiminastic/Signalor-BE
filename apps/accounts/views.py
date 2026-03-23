import json
import logging
import os

import stripe
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Subscription

logger = logging.getLogger("apps")

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")


def _get_stripe():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key or key.startswith("sk_test_placeholder"):
        return None
    stripe.api_key = key
    return stripe


class CreateCheckoutSessionView(APIView):
    """POST /api/payments/create-checkout/"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        currency = request.data.get("currency", "usd").lower().strip()

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        s = _get_stripe()
        if not s:
            return Response({"error": "Stripe is not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Get or create subscription record
        sub, _ = Subscription.objects.get_or_create(email=email, defaults={"currency": currency})

        # Get or create Stripe customer
        if not sub.stripe_customer_id:
            customer = s.Customer.create(email=email)
            sub.stripe_customer_id = customer.id
            sub.save(update_fields=["stripe_customer_id"])
        else:
            customer_id = sub.stripe_customer_id

        # Pick price based on currency
        if currency == "inr":
            price_id = os.getenv("STRIPE_PRICE_ID_INR", "")
        else:
            price_id = os.getenv("STRIPE_PRICE_ID_USD", "")

        if not price_id or price_id.startswith("price_placeholder"):
            return Response({"error": "Stripe prices not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            session = s.checkout.Session.create(
                customer=sub.stripe_customer_id,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=f"{FRONTEND_URL}/payments/success?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{FRONTEND_URL}/pricing",
                metadata={"email": email},
            )
            return Response({"checkout_url": session.url})
        except Exception as e:
            logger.exception("Stripe checkout error")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SubscriptionStatusView(APIView):
    """GET /api/payments/status/?email="""
    permission_classes = [AllowAny]

    def get(self, request):
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
class StripeWebhookView(APIView):
    """POST /api/payments/webhook/"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        s = _get_stripe()
        if not s:
            return HttpResponse(status=400)

        payload = request.body
        sig = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

        try:
            event = s.Webhook.construct_event(payload, sig, webhook_secret)
        except (ValueError, s.error.SignatureVerificationError) as e:
            logger.warning("Stripe webhook signature failed: %s", e)
            return HttpResponse(status=400)

        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            self._handle_checkout_completed(data)
        elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
            self._handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            self._handle_subscription_deleted(data)
        elif event_type == "invoice.paid":
            self._handle_invoice_paid(data)

        return HttpResponse(status=200)

    def _handle_checkout_completed(self, session):
        email = session.get("metadata", {}).get("email", "").lower()
        customer_id = session.get("customer", "")
        subscription_id = session.get("subscription", "")

        if email:
            sub, _ = Subscription.objects.get_or_create(email=email)
            sub.stripe_customer_id = customer_id
            sub.stripe_subscription_id = subscription_id
            sub.status = "active"
            sub.save(update_fields=["stripe_customer_id", "stripe_subscription_id", "status"])
            logger.info("Checkout completed for %s", email)

    def _handle_subscription_updated(self, subscription):
        sub_id = subscription.get("id", "")
        try:
            sub = Subscription.objects.get(stripe_subscription_id=sub_id)
        except Subscription.DoesNotExist:
            # Try by customer ID
            cust_id = subscription.get("customer", "")
            try:
                sub = Subscription.objects.get(stripe_customer_id=cust_id)
                sub.stripe_subscription_id = sub_id
            except Subscription.DoesNotExist:
                return

        sub.status = subscription.get("status", sub.status)
        period_end = subscription.get("current_period_end")
        if period_end:
            from datetime import datetime
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
        sub.save(update_fields=["status", "current_period_end", "stripe_subscription_id"])

    def _handle_subscription_deleted(self, subscription):
        sub_id = subscription.get("id", "")
        try:
            sub = Subscription.objects.get(stripe_subscription_id=sub_id)
            sub.status = "canceled"
            sub.save(update_fields=["status"])
        except Subscription.DoesNotExist:
            pass

    def _handle_invoice_paid(self, invoice):
        sub_id = invoice.get("subscription", "")
        if sub_id:
            try:
                sub = Subscription.objects.get(stripe_subscription_id=sub_id)
                sub.status = "active"
                sub.save(update_fields=["status"])
            except Subscription.DoesNotExist:
                pass
