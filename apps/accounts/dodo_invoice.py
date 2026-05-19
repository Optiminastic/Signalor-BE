"""Fetch payment invoice PDFs from Dodo Payments API."""

from __future__ import annotations

import logging

import requests

from .dodo_env import dodo_live_mode_enabled, normalized_dodo_api_key

logger = logging.getLogger("apps")


def dodo_api_base() -> str:
    return "https://live.dodopayments.com" if dodo_live_mode_enabled() else "https://test.dodopayments.com"


def _scan_payment_id_dict(data: dict) -> str:
    for key in ("payment_id", "paymentId"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    nested = data.get("payment")
    if isinstance(nested, dict):
        for key in ("payment_id", "paymentId", "id"):
            v = nested.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    v = data.get("id")
    if isinstance(v, str) and v.strip().startswith("pay_"):
        return v.strip()
    return ""


def extract_payment_id_from_webhook(data: dict) -> str:
    """Best-effort payment id from Dodo webhook payloads (supports nested `object`)."""
    if not isinstance(data, dict):
        return ""
    pid = _scan_payment_id_dict(data)
    if pid:
        return pid
    obj = data.get("object")
    if isinstance(obj, dict):
        pid = _scan_payment_id_dict(obj)
        if pid:
            return pid
    return ""


def fetch_payment_invoice_pdf(payment_id: str) -> tuple[bytes | None, str | None]:
    """
    GET /invoices/payments/{payment_id} → application/pdf
    Returns (pdf_bytes, error_tag).
    """
    key = normalized_dodo_api_key()
    if not key or not payment_id:
        return None, "not_configured"
    base = dodo_api_base().rstrip("/")
    url = f"{base}/invoices/payments/{payment_id}"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/pdf",
            },
            timeout=60,
        )
        if r.status_code != 200:
            logger.warning(
                "Dodo invoice HTTP %s for payment_id=%s body=%s",
                r.status_code,
                payment_id,
                (r.text or "")[:300],
            )
            return None, f"upstream_{r.status_code}"
        if not r.content or len(r.content) < 100:
            logger.warning("Dodo invoice empty/short response for payment_id=%s", payment_id)
            return None, "empty_pdf"
        return r.content, None
    except requests.RequestException as e:
        logger.warning("Dodo invoice request failed: %s", e)
        return None, "network_error"


def retrieve_payment(payment_id: str) -> tuple[dict | None, str | None]:
    """GET /payments/{payment_id} → single payment object.

    Used by the invoice list view when we have a payment_id but no subscription_id
    (legacy rows or one-off charges) — we still want to surface real date/amount/
    status to the UI instead of nulls.
    """
    key = normalized_dodo_api_key()
    if not key or not payment_id:
        return None, "not_configured"
    base = dodo_api_base().rstrip("/")
    url = f"{base}/payments/{payment_id}"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning(
                "Dodo retrieve_payment HTTP %s for payment_id=%s body=%s",
                r.status_code,
                payment_id,
                (r.text or "")[:300],
            )
            return None, f"upstream_{r.status_code}"
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Dodo retrieve_payment failed: %s", e)
        return None, "network_error"
    if not isinstance(body, dict):
        return None, "bad_shape"
    return body, None


def list_payments_for_subscription(subscription_id: str) -> tuple[list[dict] | None, str | None]:
    """
    GET /payments?subscription_id=… → list of payment objects.

    Returns (items, error_tag). Each item is a Dodo payment dict; we don't
    re-shape here so the caller can pick the fields it wants.
    """
    key = normalized_dodo_api_key()
    if not key or not subscription_id:
        return None, "not_configured"
    base = dodo_api_base().rstrip("/")
    url = f"{base}/payments"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            params={"subscription_id": subscription_id, "page_size": 100},
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning(
                "Dodo list_payments HTTP %s for subscription_id=%s body=%s",
                r.status_code,
                subscription_id,
                (r.text or "")[:300],
            )
            return None, f"upstream_{r.status_code}"
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Dodo list_payments failed: %s", e)
        return None, "network_error"

    # Dodo paginated responses come back as { items: [...], total_count, ... };
    # older shapes may also return a bare list. Handle both defensively.
    if isinstance(body, dict):
        items = body.get("items") or body.get("data") or []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    return [it for it in items if isinstance(it, dict)], None
