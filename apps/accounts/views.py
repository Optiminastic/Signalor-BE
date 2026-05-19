import hashlib
import hmac
import json
import logging
import os
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from dodopayments import AuthenticationError, PermissionDeniedError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.partners.services import get_active_attribution
from apps.referrals.models import Referral
from core.throttling import PollingThrottle

from .dodo_env import (
    dodo_live_mode_enabled,
    dodo_mode_public,
    normalized_dodo_api_key,
)
from .dodo_invoice import (
    extract_payment_id_from_webhook,
    fetch_payment_invoice_pdf,
    list_payments_for_subscription,
)
from .models import PLAN_LIMITS, Subscription
from .subscription_utils import is_internal_email


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
        # Customer country / currency hints — used to pre-fill the billing
        # address so Dodo defaults to the right currency at checkout (instead
        # of the United States / USD). Currency only takes effect on products
        # that have Adaptive Pricing enabled in the Dodo dashboard.
        country_raw = (request.data.get("country") or "").strip().upper()
        currency_raw = (request.data.get("currency") or "").strip().upper()
        # Affiliate code captured client-side in localStorage. An existing user
        # who clicked a creator link won't have a PartnerAttribution row yet
        # (those are only minted at signup) — record one now so the discount
        # auto-applies below and the webhook can credit the partner.
        partner_code = (request.data.get("partner_code") or "").strip().upper()

        if not email:
            return Response({"error": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if plan not in ("starter", "pro", "business"):
            return Response({"error": "Invalid plan."}, status=status.HTTP_400_BAD_REQUEST)

        dodo = _get_dodo()
        if not dodo:
            return Response(
                {"error": "Payment system not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        product_map = {
            "starter": os.getenv("DODO_PRODUCT_ID_STARTER", os.getenv("DODO_PRODUCT_ID", "")).strip(),
            "pro": os.getenv("DODO_PRODUCT_ID_PRO", "").strip(),
            "business": os.getenv("DODO_PRODUCT_ID_BUSINESS", "").strip(),
        }
        product_id = product_map.get(plan, "")
        if not product_id:
            return Response(
                {"error": f"Product not configured for {plan} plan."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        sub, _ = Subscription.objects.get_or_create(email=email)

        # Auto-apply the 10%-off discount. Two paths can supply it:
        #   1) Active affiliate attribution (creator program) — wins by last-click
        #   2) Pending Referral (user-to-user) — only if no affiliate attribution
        # Both paths use the same Dodo discount (DODO_REFEREE_DISCOUNT_CODE).
        # Note: checkout_sessions.create takes the human discount_code (e.g.
        # "VSV4K3RN2DD"), NOT the dsc_... ID. The ID is for subscriptions.update.
        discount_code_to_apply = ""
        discount_source = ""
        referee_code_env = os.getenv("DODO_REFEREE_DISCOUNT_CODE", "").strip()

        try:
            attribution = get_active_attribution(email)
            # If the buyer just clicked an affiliate link but never went
            # through signup, attribution will be empty. Mint one from the
            # `partner_code` carried up from the client.
            if not attribution and partner_code:
                from apps.partners.services import set_attribution

                attribution = set_attribution(email, partner_code, landing_path="checkout")
            if attribution and referee_code_env:
                discount_code_to_apply = referee_code_env
                discount_source = f"affiliate partner={attribution.partner.code}"
        except Exception:
            logger.exception("partners: attribution lookup failed at checkout email=%s", email)

        if not discount_code_to_apply:
            try:
                referral = Referral.objects.filter(
                    referee_email=email,
                    status=Referral.Status.PENDING,
                ).first()
                if referral and referee_code_env:
                    discount_code_to_apply = referee_code_env
                    discount_source = "referral"
            except Exception:
                logger.exception("referrals: lookup failed at checkout for email=%s", email)

        try:
            # Current Dodo Python SDK: checkout_sessions (not subscriptions.create with payment_link).
            checkout_kwargs = {
                "product_cart": [{"product_id": product_id, "quantity": 1}],
                "return_url": f"{FRONTEND_URL}/payments/success",
                "customer": {
                    "email": email,
                    "name": email.split("@")[0].replace(".", " ").title() or "Customer",
                },
                "metadata": {"email": email, "plan": plan},
            }
            # Pre-fill billing country so Dodo doesn't default to United States.
            # Only `country` is required; other address fields stay optional and
            # the customer fills them at checkout. Currency hint is also passed —
            # Dodo honours it only when the product has Adaptive Pricing enabled.
            if len(country_raw) == 2:
                checkout_kwargs["billing_address"] = {"country": country_raw}
            if len(currency_raw) == 3:
                checkout_kwargs["billing_currency"] = currency_raw
            if discount_code_to_apply:
                checkout_kwargs["discount_code"] = discount_code_to_apply
                logger.info(
                    "checkout: applying discount_code=%s source=%s email=%s",
                    discount_code_to_apply,
                    discount_source,
                    email,
                )
            checkout_session = dodo.checkout_sessions.create(**checkout_kwargs)
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
                        "Restart Django after editing ranking-be/.env." + diag
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
    # Polled every 2s by /payments/success and on every dashboard mount —
    # the default 100/hour anon ceiling locks legit users out within seconds.
    throttle_classes = [PollingThrottle]

    def get(self, request):
        starter = PLAN_LIMITS["starter"]
        business = PLAN_LIMITS["business"]

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        # @optiminastic.com → free unlimited access (business tier)
        if is_internal_email(email):
            return Response(
                {
                    "is_active": True,
                    "status": "active",
                    "current_period_end": None,
                    "currency": "gbp",
                    "plan": "business",
                    "plan_label": "Max (Internal)",
                    "limits": business,
                    "invoice_available": False,
                }
            )

        try:
            sub = Subscription.objects.get(email=email)
            return Response(
                {
                    "is_active": sub.is_active,
                    "status": sub.status,
                    "current_period_end": sub.current_period_end.isoformat()
                    if sub.current_period_end
                    else None,
                    "currency": sub.currency,
                    "plan": sub.plan,
                    "plan_label": sub.limits["label"],
                    "limits": sub.limits,
                    "invoice_available": bool(sub.last_invoice_payment_id),
                }
            )
        except Subscription.DoesNotExist:
            return Response(
                {
                    "is_active": False,
                    "status": "none",
                    "current_period_end": None,
                    "currency": "gbp",
                    "plan": "starter",
                    "plan_label": starter["label"],
                    "limits": starter,
                    "invoice_available": False,
                }
            )


class DownloadInvoiceView(APIView):
    """GET /api/payments/invoice/?email=&payment_id= — Invoice PDF for a Dodo payment.

    If ``payment_id`` is omitted, falls back to the subscription's last recorded
    payment (set by webhook). Passing ``payment_id`` explicitly lets the
    billing page download any past invoice from the InvoiceListView table.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        payment_id = (request.query_params.get("payment_id") or "").strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        if is_internal_email(email):
            return Response({"error": "No invoice for internal accounts."}, status=status.HTTP_404_NOT_FOUND)

        try:
            sub = Subscription.objects.get(email=email)
        except Subscription.DoesNotExist:
            return Response({"error": "No subscription found."}, status=status.HTTP_404_NOT_FOUND)

        if not payment_id:
            payment_id = sub.last_invoice_payment_id

        if not payment_id:
            return Response(
                {"error": "No invoice yet. It appears after your first successful charge."},
                status=status.HTTP_404_NOT_FOUND,
            )

        pdf, err = fetch_payment_invoice_pdf(payment_id)
        if not pdf:
            logger.warning("Invoice download failed for %s payment_id=%s: %s", email, payment_id, err)
            return Response(
                {"error": "Could not retrieve invoice from payment provider."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        safe_name = payment_id.replace("/", "_")[:80]
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="signalor-invoice-{safe_name}.pdf"'
        return response


class InvoiceListView(APIView):
    """GET /api/payments/invoices/?email= — list every Dodo payment for the
    user's subscription, newest first. Source of truth is Dodo; we don't
    cache the list locally because amounts/statuses can change (refunds).
    """

    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        if is_internal_email(email):
            return Response({"items": []})

        try:
            sub = Subscription.objects.get(email=email)
        except Subscription.DoesNotExist:
            return Response({"items": []})

        if not sub.payment_subscription_id:
            # Subscription was never linked to a Dodo subscription id (e.g. legacy
            # rows). Fall back to the single known payment if available so the UI
            # still shows something.
            if sub.last_invoice_payment_id:
                return Response(
                    {
                        "items": [
                            {
                                "payment_id": sub.last_invoice_payment_id,
                                "created_at": None,
                                "amount": None,
                                "currency": None,
                                "status": None,
                            }
                        ],
                    }
                )
            return Response({"items": []})

        items, err = list_payments_for_subscription(sub.payment_subscription_id)
        if items is None:
            logger.warning("Invoice list failed for %s: %s", email, err)
            return Response({"items": [], "error": "upstream"}, status=status.HTTP_200_OK)

        # Reshape each Dodo payment into the minimal fields the table needs.
        # Dodo's `total_amount` is in minor units (cents/paise/etc.) — divide
        # by 100 for display. Status comes through as `status` / `payment_status`.
        out: list[dict] = []
        for p in items:
            amount_minor = p.get("total_amount") or p.get("amount") or 0
            try:
                amount = float(amount_minor) / 100.0 if amount_minor else None
            except (TypeError, ValueError):
                amount = None
            out.append(
                {
                    "payment_id": p.get("payment_id") or p.get("id") or "",
                    "created_at": p.get("created_at") or p.get("timestamp"),
                    "amount": amount,
                    "currency": p.get("currency") or p.get("settlement_currency") or "",
                    "status": (p.get("status") or p.get("payment_status") or "").lower() or None,
                }
            )
        # Newest first — Dodo usually orders this way but normalize defensively.
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return Response({"items": out})


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

        # Standard Webhooks verification: HMAC-SHA256 of "{msg_id}.{timestamp}.{body}".
        # Per the spec, the secret arrives as "whsec_<base64>"; strip the prefix
        # before decoding — leaving it in makes base64 fail with a "1 more than
        # a multiple of 4" length error because `_` isn't in the standard
        # base64 alphabet.
        try:
            import base64

            secret_material = webhook_secret
            if secret_material.startswith("whsec_"):
                secret_material = secret_material[len("whsec_") :]
            secret_bytes = base64.b64decode(secret_material)
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
        elif event_type in ("payment.refunded", "refund.succeeded", "refund.created"):
            # Dodo's exact refund event name has varied across SDK versions;
            # cover the three observed forms. Body is best-effort.
            self._handle_payment_refunded(data)

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

        sub.save(
            update_fields=[
                "payment_subscription_id",
                "payment_customer_id",
                "status",
                "current_period_end",
                "plan",
            ]
        )
        self._store_latest_payment_id(data, sub)
        logger.info("Subscription activated for %s (plan=%s)", email, sub.plan)

        # Referral hook (referee side): if THIS email was referred, mark the
        # Referral as PAID and queue a 20%-off reward for the referrer. The
        # actual refund happens later, on the referrer's renewal webhook.
        try:
            from apps.referrals.services import on_referee_first_payment

            on_referee_first_payment(email)
        except Exception:
            logger.exception("referrals: on_referee_first_payment failed for %s", email)

        # Partner-program hook: if this email is attributed to an affiliate,
        # create a PENDING commission row. Idempotent on payment_id.
        try:
            self._record_partner_commission(email, data)
        except Exception:
            logger.exception("partners: record_commission failed for %s", email)

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

        # Referral hook: a renewal landed — if this email is a referrer with
        # any queued PENDING ReferralReward, issue a 20% partial refund on the
        # just-charged renewal payment.
        try:
            from apps.referrals.services import on_referrer_renewal

            on_referrer_renewal(sub.email, webhook_data=data)
        except Exception:
            logger.exception("referrals: on_referrer_renewal failed for %s", sub.email)

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

        # Referral hook: revoke the referrer's pending reward if their referee
        # cancelled before the renewal fired (no churn protection).
        try:
            from apps.referrals.services import on_referee_cancelled

            on_referee_cancelled(sub.email)
        except Exception:
            logger.exception("referrals: on_referee_cancelled failed for %s", sub.email)

    def _handle_payment_refunded(self, data):
        """Payment refunded — cancel any partner commission tied to that payment.

        Dodo refund payloads can carry either ``original_payment_id`` (newer
        SDKs) or just ``payment_id``. Try both so we cancel the right row even
        if Dodo's field naming drifts. PAID commissions are never reversed —
        once the creator has been wired the money we eat that refund.
        """
        # Prefer original_payment_id when present (refund payloads). Fall back
        # to the recursive helper, which finds any nested payment_id.
        original_payment_id = ""
        if isinstance(data, dict):
            for key in ("original_payment_id", "originalPaymentId"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    original_payment_id = v.strip()
                    break
            if not original_payment_id:
                obj = data.get("object")
                if isinstance(obj, dict):
                    for key in ("original_payment_id", "originalPaymentId"):
                        v = obj.get(key)
                        if isinstance(v, str) and v.strip():
                            original_payment_id = v.strip()
                            break
        payment_id = original_payment_id or extract_payment_id_from_webhook(data)
        if not payment_id:
            logger.info("Dodo refund webhook: no payment_id resolvable; skipping")
            return
        try:
            from apps.partners.services import cancel_commission_for_refund

            cancel_commission_for_refund(payment_id)
        except Exception:
            logger.exception("partners: cancel_commission_for_refund failed payment=%s", payment_id)

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

    def _record_partner_commission(self, email, data):
        """Look up affiliate attribution and stage a PENDING commission row.

        Best-effort on amount extraction — Dodo's subscription.active payload
        carries ``recurring_pre_tax_amount``. We use it as both gross and
        post-discount; if a discount was applied the discrepancy is small and
        the admin can adjust the row manually before payout.
        """
        from decimal import Decimal

        from apps.partners.services import record_commission

        payment_id = extract_payment_id_from_webhook(data) or data.get("subscription_id", "")
        if not payment_id:
            logger.info("partners: no payment_id in webhook — skipping commission")
            return

        amount_raw = (
            data.get("recurring_pre_tax_amount") or data.get("amount") or data.get("total_amount") or 0
        )
        try:
            amount = (
                Decimal(str(amount_raw)) / Decimal(100)
                if isinstance(amount_raw, int)
                else Decimal(str(amount_raw))
            )
        except Exception:
            amount = Decimal("0")

        currency = (data.get("currency") or "USD").upper()
        record_commission(
            referee_email=email,
            payment_id=payment_id,
            gross_amount=amount,
            post_discount_amount=amount,
            currency=currency,
        )


class UsageView(APIView):
    """GET /api/payments/usage/?email= — current usage vs plan limits."""

    permission_classes = [AllowAny]
    # Read endpoint used by onboarding, billing, and gate cards — must not
    # share the strict anon ceiling.
    throttle_classes = [PollingThrottle]

    def get(self, request):
        from apps.analyzer.models import AnalysisRun, PromptTrack
        from apps.organizations.models import Organization

        from .subscription_utils import get_plan_limits, is_internal_email

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

        return Response(
            {
                "plan": "business"
                if is_internal
                else (_get_sub_plan(email) if not is_internal else "business"),
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
            }
        )


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
            plans.append(
                {
                    "id": key,
                    "label": cfg["label"],
                    "price_gbp": cfg["price_gbp"],
                    "max_projects": cfg["max_projects"],
                    "max_prompts": cfg["max_prompts"],
                    "engines": cfg["engines"],
                }
            )
        return Response(plans)


class PlanPricesView(APIView):
    """GET /api/payments/plan-prices/ — live prices fetched from Dodo.

    Replaces the frontend's EUR-rate math. Returns whatever the Dodo SDK
    reports for each product so the pricing page always matches the real
    checkout amount. Cached briefly to avoid hammering Dodo on every load.

    Response shape:
      {
        "starter": { "currency": "USD", "amount_minor": 1999, "amount": 19.99,
                     "interval": "Month", "interval_count": 1 },
        "pro":     { ... },
        "business":{ ... },
        "source":  "dodo"  | "fallback"
      }

    Each plan entry can be null if its product is not configured.
    """

    permission_classes = [AllowAny]
    # Loaded by the landing pricing teaser + /pricing — gets hit on every
    # public visit. Cache absorbs cost; throttle protects against the rare
    # loop without locking out legitimate viewers.
    throttle_classes = [PollingThrottle]
    _CACHE_KEY = "dodo_plan_prices_v1"
    _CACHE_TTL = 600  # 10 minutes

    def get(self, request):
        from django.core.cache import cache

        cached = cache.get(self._CACHE_KEY)
        if cached is not None:
            return Response(cached)

        product_map = {
            "starter": os.getenv("DODO_PRODUCT_ID_STARTER", os.getenv("DODO_PRODUCT_ID", "")).strip(),
            "pro": os.getenv("DODO_PRODUCT_ID_PRO", "").strip(),
            "business": os.getenv("DODO_PRODUCT_ID_BUSINESS", "").strip(),
        }

        dodo = _get_dodo()
        if not dodo:
            return Response(
                {k: None for k in product_map} | {"source": "fallback"},
                status=status.HTTP_200_OK,
            )

        result: dict = {"source": "dodo"}
        # FX rates loaded lazily from the first product's base currency.
        fx_rates: dict[str, float] | None = None
        for plan_key, product_id in product_map.items():
            if not product_id:
                result[plan_key] = None
                continue
            try:
                product = dodo.products.retrieve(product_id)
                if fx_rates is None:
                    base_ccy = getattr(getattr(product, "price", None), "currency", None) or "USD"
                    fx_rates = _fetch_fx_rates(base_ccy)
                result[plan_key] = _serialize_dodo_price(product, fx_rates)
            except Exception as e:
                logger.warning("plan-prices: dodo retrieve failed for %s (%s): %s", plan_key, product_id, e)
                result[plan_key] = None

        cache.set(self._CACHE_KEY, result, self._CACHE_TTL)
        return Response(result)


def _serialize_dodo_price(product, fx_rates: dict[str, float] | None = None) -> dict | None:
    """Pull the current recurring price out of a Dodo Product object.

    Dodo's SDK returns the product with a ``price`` object whose shape varies
    between recurring_price (subscriptions) and one_time_price. We surface
    what the pricing page needs: the base currency + amount, plus a
    ``prices_by_currency`` map computed via FX rates so non-base-currency
    visitors see an approximate localized total. Dodo applies the real FX
    at checkout — these page-side numbers are within ~1% of that.
    """
    price = getattr(product, "price", None)
    if price is None and isinstance(product, dict):
        price = product.get("price")
    if price is None:
        return None

    def _g(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    currency = (_g(price, "currency") or "USD").upper()
    amount_minor = _g(price, "price")
    if amount_minor is None:
        amount_minor = _g(price, "amount")
    try:
        amount_minor = int(amount_minor) if amount_minor is not None else None
    except (TypeError, ValueError):
        amount_minor = None
    if amount_minor is None:
        return None

    interval = _g(price, "payment_frequency_interval") or "Month"
    interval_count = _g(price, "payment_frequency_count") or 1

    base_amount = amount_minor / 100
    prices_by_currency: dict[str, float] = {currency: round(base_amount, 2)}
    if fx_rates:
        for code, rate in fx_rates.items():
            if code == currency or not isinstance(rate, (int, float)) or rate <= 0:
                continue
            prices_by_currency[code] = round(base_amount * rate, 2)

    return {
        "currency": currency,
        "amount_minor": amount_minor,
        "amount": round(base_amount, 2),
        "interval": interval,
        "interval_count": interval_count,
        "prices_by_currency": prices_by_currency,
    }


def _fetch_fx_rates(base_currency: str) -> dict[str, float]:
    """Pull spot FX rates with the given base. Cached for 24h. Empty on failure
    — callers degrade gracefully (single-currency display)."""
    import requests
    from django.core.cache import cache

    base_currency = base_currency.upper()
    cache_key = f"fx_rates_v1_{base_currency}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rates: dict[str, float] = {}
    try:
        resp = requests.get(
            f"https://open.er-api.com/v6/latest/{base_currency}",
            timeout=4,
        )
        data = resp.json() if resp.ok else {}
        if data.get("result") == "success":
            raw = data.get("rates") or {}
            # Keep only the currencies we display on the page.
            for code in ("USD", "EUR", "INR", "GBP", "AUD", "CAD", "SGD", "AED", "JPY"):
                if code in raw:
                    rates[code] = float(raw[code])
    except Exception as e:
        logger.warning("plan-prices: FX fetch failed (%s): %s", base_currency, e)

    cache.set(cache_key, rates, 24 * 3600)
    return rates


class TerminateAccountView(APIView):
    """POST /api/account/terminate/ — soft delete, deactivates in 24h."""

    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response({"error": "Email required."}, status=status.HTTP_400_BAD_REQUEST)

        sub, _ = Subscription.objects.get_or_create(email=email)

        if sub.deactivated_at:
            return Response(
                {
                    "message": "Account already scheduled for deactivation.",
                    "deactivated_at": sub.deactivated_at.isoformat(),
                }
            )

        sub.deactivated_at = timezone.now() + timedelta(hours=24)
        sub.save(update_fields=["deactivated_at"])
        logger.info("Account termination scheduled for %s at %s", email, sub.deactivated_at)

        return Response(
            {
                "message": "Account scheduled for deactivation in 24 hours.",
                "deactivated_at": sub.deactivated_at.isoformat(),
            }
        )


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

        from apps.analyzer.models import AnalysisRun
        from apps.organizations.models import Organization

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

        return Response(
            {
                "message": "Account permanently deleted.",
                "deleted": deleted_counts,
            }
        )
