"""Render a Signalor-branded PDF invoice for $0 (fully discounted) payments.

Dodo Payments does not generate an invoice PDF when ``total_amount == 0``,
which is correct upstream behavior but leaves customers without a receipt
for promo/comp/100%-off subscriptions. This module fills the gap by
producing a PDF locally with the same line-item breakdown a normal Dodo
invoice would show.
"""

from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO

from django.template.loader import render_to_string

logger = logging.getLogger("apps")

# Minor-unit (cents/paise/pence) → major-unit divisor. All Dodo amounts come
# in minor units across every currency.
_MINOR_UNITS = 100

# xhtml2pdf's default Helvetica face doesn't render £/€/₹ glyphs (they show
# as tofu boxes). Letter codes are unambiguous, render with any font, and
# match the convention on the Stripe / Razorpay invoices most B2B buyers
# expect to see.
_CURRENCY_SYMBOLS = {
    "GBP": "GBP ",
    "USD": "USD ",
    "EUR": "EUR ",
    "INR": "INR ",
}

_PLAN_DESCRIPTIONS = {
    "starter": "Monthly subscription — Self-Serve Brand plan",
    "pro": "Monthly subscription — Managed Growth Brand plan",
    "business": "Monthly subscription — Max plan",
}


def _fmt_money(minor: int | float | None) -> str:
    if minor in (None, ""):
        return "0.00"
    try:
        return f"{float(minor) / _MINOR_UNITS:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _currency_symbol(code: str | None) -> str:
    if not code:
        return ""
    return _CURRENCY_SYMBOLS.get(code.upper(), f"{code.upper()} ")


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        # Dodo timestamps are ISO 8601 with trailing Z.
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except (TypeError, ValueError):
        return iso


def _shorten_payment_id(pid: str) -> str:
    """Drop the ``pay_`` prefix and uppercase the rest for a clean invoice number."""
    if not pid:
        return ""
    return pid.removeprefix("pay_").upper()[:16]


def _primary_discount(payment: dict) -> dict | None:
    """Return the first applied discount, or None.

    Dodo's payment object exposes a ``discounts`` list; an item looks like
    ``{"name": "100 OFF", "amount": 10000, "type": "percentage", ...}`` where
    ``amount`` for percentage discounts is basis points (10000 = 100%).
    """
    discounts = payment.get("discounts") or []
    if not isinstance(discounts, list) or not discounts:
        return None
    first = discounts[0]
    return first if isinstance(first, dict) else None


def _build_context(payment: dict, product: dict | None) -> dict:
    """Project Dodo's payment + product objects into the template context."""
    customer = payment.get("customer") or {}
    metadata = payment.get("metadata") or {}
    plan_slug = (metadata.get("plan") or "").lower()

    # Currency: payment-level (settlement currency, e.g. INR) trumps product
    # currency for the invoice header, since that's what the user actually saw
    # at checkout.
    currency = (payment.get("currency") or payment.get("settlement_currency") or "").upper()

    # Unit price = product's listed price (in product's currency). If the
    # payment was settled in a different currency via Adaptive Pricing, this
    # field still reflects the listed price the user agreed to.
    product_price = None
    product_currency = currency
    if product:
        price_obj = product.get("price") or {}
        product_price = price_obj.get("price") or price_obj.get("recurring_pre_tax_amount")
        product_currency = (price_obj.get("currency") or currency).upper()

    # Prefer the product's currency (e.g. GBP) for the invoice header — that's
    # the "real" listed price. Fall back to payment currency.
    display_currency = product_currency or currency or "USD"

    subtotal_minor = product_price if product_price else payment.get("total_amount") or 0
    total_minor = payment.get("total_amount") or 0
    tax_minor = payment.get("tax") or 0

    discount = _primary_discount(payment)
    discount_amount_minor = 0
    discount_name = ""
    discount_percent = 0
    if discount:
        discount_name = discount.get("name") or discount.get("code") or "Discount"
        # Percentage discounts: amount is basis points (10000 = 100%).
        # Fixed discounts: amount is minor units of the product currency.
        if (discount.get("type") or "").lower() == "percentage":
            try:
                discount_percent = int(discount.get("amount") or 0) // 100
            except (TypeError, ValueError):
                discount_percent = 0
            if subtotal_minor:
                discount_amount_minor = int(subtotal_minor) * discount_percent // 100
        else:
            discount_amount_minor = int(discount.get("amount") or 0)

    plan_name = (product or {}).get("name") or f"Signalor {plan_slug.title() or 'Plan'}"
    plan_description = _PLAN_DESCRIPTIONS.get(plan_slug, "Signalor subscription")

    payment_method = (payment.get("payment_method") or "").upper() or "—"
    status_raw = (payment.get("status") or "").lower()
    status_label = {"succeeded": "Paid", "active": "Paid"}.get(status_raw, status_raw.title() or "Paid")

    return {
        "invoice_number": _shorten_payment_id(payment.get("payment_id", "")),
        "payment_id": payment.get("payment_id", ""),
        "subscription_id": payment.get("subscription_id") or "",
        "issue_date": _fmt_date(payment.get("created_at")),
        "customer_name": customer.get("name") or "Customer",
        "customer_email": customer.get("email") or "",
        "plan_name": plan_name,
        "plan_description": plan_description,
        "currency_symbol": _currency_symbol(display_currency),
        "subtotal": _fmt_money(subtotal_minor),
        "discount_name": discount_name,
        "discount_amount": _fmt_money(discount_amount_minor) if discount_amount_minor else "",
        "discount_percent": discount_percent,
        "tax_amount": _fmt_money(tax_minor),
        "total": _fmt_money(total_minor),
        "status_label": status_label,
        "payment_method": payment_method,
    }


def render_zero_invoice_pdf(payment: dict, product: dict | None = None) -> bytes | None:
    """Render a PDF invoice for a $0 payment. Returns PDF bytes or None on failure."""
    try:
        from xhtml2pdf import pisa
    except Exception:
        logger.exception("xhtml2pdf not available; cannot render zero-amount invoice")
        return None

    if not isinstance(payment, dict):
        return None

    context = _build_context(payment, product if isinstance(product, dict) else None)
    try:
        html = render_to_string("billing/zero_invoice.html", context)
    except Exception:
        logger.exception("zero_invoice: template render failed")
        return None

    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if pisa_status.err:
        logger.warning("zero_invoice: pisa errored for payment_id=%s", payment.get("payment_id"))
        return None
    buffer.seek(0)
    return buffer.read()
