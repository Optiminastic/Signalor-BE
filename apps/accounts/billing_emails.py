"""Post-payment email sequence.

When a Dodo ``subscription.active`` (first payment) or ``subscription.renewed``
webhook fires, the user gets three emails spaced 2 minutes apart:

    1. Payment success    (immediately)
    2. Invoice            (T + 2 min, PDF attached)
    3. Welcome            (T + 4 min)

Sequencing runs in a daemon thread so the webhook returns 200 to Dodo
quickly. The interval is configurable via ``BILLING_EMAIL_INTERVAL_SECONDS``
so tests / local can use 0 or a few seconds.

Idempotency: callers should consult ``Subscription.last_billing_emails_payment_id``
and only fire this for a new payment_id. We don't track per-email-sent
state because the failure mode (one email sent, process restarts) is
extremely rare in practice and the recipient already has the receipt
endpoint to fall back on.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections
from django.template.loader import render_to_string

from .invoice_pdf import resolve_invoice_pdf

logger = logging.getLogger("apps")

FRONTEND_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")

# Plan slug → human label shown in subject lines and email bodies. Matches
# the Dodo product names so the receipt and the Dodo dashboard agree.
_PLAN_LABELS = {
    "starter": "Signalor Self-Serve Brand",
    "pro": "Signalor Managed Growth Brand",
    "business": "Signalor Max",
}


def _interval_seconds() -> int:
    """Seconds between consecutive emails. Tunable for local testing."""
    try:
        return max(0, int(os.getenv("BILLING_EMAIL_INTERVAL_SECONDS", "120")))
    except (TypeError, ValueError):
        return 120


def _default_from() -> str:
    return getattr(settings, "DEFAULT_FROM_EMAIL", None) or "Signalor <billing@signalor.ai>"


def _customer_name(email: str) -> str:
    """Best-effort display name when we don't have the user's real name."""
    local = (email or "").split("@", 1)[0]
    if not local:
        return "there"
    return local.replace(".", " ").replace("_", " ").title()


def _plan_label(plan: str) -> str:
    return _PLAN_LABELS.get((plan or "").lower(), "Signalor")


def _short_payment_id(payment_id: str) -> str:
    return (payment_id or "").removeprefix("pay_").upper()[:16]


def _send(
    *,
    to: str,
    subject: str,
    text_template: str,
    html_template: str,
    context: dict,
    attachment: tuple[str, bytes, str] | None = None,
) -> None:
    """Send one HTML+text email, optionally with a PDF attachment."""
    try:
        text_body = render_to_string(text_template, context)
        html_body = render_to_string(html_template, context)
    except Exception:
        logger.exception("billing_emails: failed to render templates %s / %s", text_template, html_template)
        return

    try:
        msg = EmailMultiAlternatives(subject=subject, body=text_body, from_email=_default_from(), to=[to])
        msg.attach_alternative(html_body, "text/html")
        if attachment:
            filename, content, mimetype = attachment
            msg.attach(filename, content, mimetype)
        msg.send(fail_silently=False)
        logger.info("billing_emails: sent %r to %s", subject, to)
    except Exception:
        logger.exception("billing_emails: send failed for %r to %s", subject, to)


def _send_payment_success(*, email: str, plan: str, payment_id: str, is_renewal: bool) -> None:
    context = {
        "customer_name": _customer_name(email),
        "plan_label": _plan_label(plan),
        "payment_id_short": _short_payment_id(payment_id),
        "is_renewal": is_renewal,
        "dashboard_url": f"{FRONTEND_URL}/dashboard",
    }
    subject = "Renewal received — Signalor" if is_renewal else "Payment received — Signalor"
    _send(
        to=email,
        subject=subject,
        text_template="email/payment_success.txt",
        html_template="email/payment_success.html",
        context=context,
    )


def _send_invoice(*, email: str, plan: str, payment_id: str, is_renewal: bool) -> None:
    pdf, err = resolve_invoice_pdf(payment_id)
    if not pdf:
        logger.warning(
            "billing_emails: invoice PDF unavailable for %s payment_id=%s (%s); sending without attachment",
            email,
            payment_id,
            err,
        )

    safe_name = (payment_id or "invoice").replace("/", "_")[:80]
    attachment = (f"signalor-invoice-{safe_name}.pdf", pdf, "application/pdf") if pdf else None

    context = {
        "customer_name": _customer_name(email),
        "plan_label": _plan_label(plan),
        "payment_id": payment_id,
        "is_renewal": is_renewal,
        "issue_date": time.strftime("%d %b %Y"),
        "invoice_url": f"{FRONTEND_URL}/dashboard/settings/billing",
    }
    _send(
        to=email,
        subject="Your Signalor invoice",
        text_template="email/invoice.txt",
        html_template="email/invoice.html",
        context=context,
        attachment=attachment,
    )


def _send_welcome(*, email: str, plan: str, is_renewal: bool) -> None:
    context = {
        "customer_name": _customer_name(email),
        "plan_label": _plan_label(plan),
        "is_renewal": is_renewal,
        "dashboard_url": f"{FRONTEND_URL}/dashboard",
    }
    subject = (
        "Thanks for renewing — your next month with Signalor"
        if is_renewal
        else "Welcome to Signalor — first steps"
    )
    _send(
        to=email,
        subject=subject,
        text_template="email/welcome.txt",
        html_template="email/welcome.html",
        context=context,
    )


def _run_sequence(email: str, plan: str, payment_id: str, is_renewal: bool, interval: int) -> None:
    """Daemon-thread body: send 3 emails ``interval`` seconds apart."""
    try:
        _send_payment_success(email=email, plan=plan, payment_id=payment_id, is_renewal=is_renewal)
        if interval:
            time.sleep(interval)
        _send_invoice(email=email, plan=plan, payment_id=payment_id, is_renewal=is_renewal)
        if interval:
            time.sleep(interval)
        _send_welcome(email=email, plan=plan, is_renewal=is_renewal)
    finally:
        # Background threads keep their own DB connection; release it so
        # we don't accumulate stale handles over time.
        close_old_connections()


def send_billing_emails(
    *,
    email: str,
    plan: str,
    payment_id: str,
    is_renewal: bool = False,
) -> None:
    """Fire-and-forget: schedules the 3-email sequence in a background thread.

    Returns immediately. The thread is daemonised so a process restart
    drops any pending sends; that's acceptable because the customer can
    always re-download the invoice from the billing page.
    """
    if not email or not payment_id:
        logger.warning(
            "billing_emails: missing email/payment_id, skipping (email=%r payment=%r)", email, payment_id
        )
        return

    interval = _interval_seconds()
    thread = threading.Thread(
        target=_run_sequence,
        args=(email, plan, payment_id, is_renewal, interval),
        name=f"billing-emails-{_short_payment_id(payment_id)}",
        daemon=True,
    )
    thread.start()
    logger.info(
        "billing_emails: queued %s sequence for %s payment_id=%s interval=%ss",
        "renewal" if is_renewal else "first-payment",
        email,
        payment_id,
        interval,
    )
