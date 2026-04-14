"""Fetch payment invoice PDFs from Dodo Payments API."""

from __future__ import annotations

import logging

import requests

from .dodo_env import dodo_live_mode_enabled, normalized_dodo_api_key

logger = logging.getLogger("apps")


def dodo_api_base() -> str:
    return (
        "https://live.dodopayments.com"
        if dodo_live_mode_enabled()
        else "https://test.dodopayments.com"
    )


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
