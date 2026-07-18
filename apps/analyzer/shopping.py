"""Per-product AI-shopping readiness scoring for connected Shopify stores.

Pure functions over the Shopify Admin API product payload — no I/O here, so
the checks are trivially testable and reusable by future ingest paths (the
Remix app's catalog-sync webhook posts the same shape).
"""

import re

# Deducted from 100. Mirrors the shopping recommendation weights in
# pipeline/recommendations.py (thin description and price visibility are the
# checks AI shopping assistants punish hardest).
ISSUE_WEIGHTS = {
    "thin_description": 30,
    "no_images": 20,
    "no_price": 20,
    "images_missing_alt": 10,
    "no_product_type": 10,
    "no_tags": 10,
}

ISSUE_LABELS = {
    "thin_description": "Thin description (under 200 characters)",
    "no_images": "No product images",
    "no_price": "No visible variant price",
    "images_missing_alt": "Images missing alt text",
    "no_product_type": "No product type set",
    "no_tags": "No tags set",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(html: str) -> str:
    return _TAG_RE.sub(" ", html or "").strip()


def _has_price(variants: list) -> bool:
    for v in variants:
        price = str(v.get("price") or "").strip()
        if price and price not in ("0", "0.0", "0.00"):
            return True
    return False


def analyze_product(product: dict) -> dict:
    """Readiness score (0-100) + issue codes for one Shopify product payload."""
    issues = []
    body = _plain_text(product.get("body_html") or "")
    if len(body) < 200:
        issues.append("thin_description")

    images = product.get("images") or []
    missing_alt = sum(1 for i in images if not (i.get("alt") or "").strip())
    if not images:
        issues.append("no_images")
    elif missing_alt:
        issues.append("images_missing_alt")

    if not _has_price(product.get("variants") or []):
        issues.append("no_price")
    if not str(product.get("product_type") or "").strip():
        issues.append("no_product_type")
    if not str(product.get("tags") or "").strip():
        issues.append("no_tags")

    return {
        "description_chars": len(body),
        "images_total": len(images),
        "images_missing_alt": missing_alt,
        "issues": issues,
        "readiness": max(0, 100 - sum(ISSUE_WEIGHTS[i] for i in issues)),
    }
