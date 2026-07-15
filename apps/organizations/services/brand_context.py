"""
Render an approved BrandProfile into a compact "brand card" for a system= prompt,
and resolve + budget it for a given run/org (Epic 2).

The ONLY-APPROVED guarantee lives here: ``build_context`` feeds prompts a rendered card
only from an APPROVED profile. With no approved profile it falls back to a run-derived
ephemeral card (never a PENDING/unverified profile). Budgeting is a char heuristic
(tokens ~= chars/4) that drops lowest-priority sections from the tail, always keeping
identity + canonical_facts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("apps")

_DEFAULT_MAX_CHARS = 2000
_HEADER = "BRAND CONTEXT (verified brand facts - treat as ground truth; do not contradict):"
_EPH_HEADER = "BRAND CONTEXT (basic, unverified brand info):"


# ── Public API ────────────────────────────────────────────────────────────


def render_brand_card(profile) -> str:
    """Deterministic compact card. Priority order: identity, canonical_facts,
    positioning, audience, voice, competitors."""
    return _join(_ordered_blocks(profile))


def build_context(run_or_org, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Resolve the org's APPROVED BrandProfile and render a budgeted card for use as a
    ``system=`` prompt. Falls back to a run-derived ephemeral card; ``""`` if nothing usable."""
    org = _resolve_org(run_or_org)
    blocks = _approved_blocks_cached(org)
    if blocks:
        body = _budget_blocks(blocks, max_chars - len(_HEADER) - 1)
        return f"{_HEADER}\n{body}" if body else ""

    run = _resolve_run(run_or_org)
    if run is not None:
        body = _budget_text(_ephemeral_card(run), max_chars - len(_EPH_HEADER) - 1)
        return f"{_EPH_HEADER}\n{body}" if body else ""
    return ""


def _approved_blocks_cached(org) -> list[str]:
    """Rendered blocks of the org's APPROVED profile, cached per org (Epic 7).

    Caching the *unbudgeted* blocks (not the final string) keeps one key per org, so
    ``invalidate_brand_card`` is a single delete and any max_chars still works. Only the
    approved path is cached -- the ephemeral fallback is run-specific and stays live, which
    preserves the only-approved guarantee.
    """
    if org is None:
        return []
    from apps.analyzer._cache import BRAND_CARD_TTL, brand_card_key, cached_or_compute

    def _compute() -> list[str]:
        profile = _approved_profile(org)
        # "" (not None) so a no-profile org caches a miss instead of recomputing forever.
        return _ordered_blocks(profile) if profile is not None else []

    blocks = cached_or_compute(brand_card_key(org.pk), BRAND_CARD_TTL, _compute)
    return blocks or []


# ── Resolution ────────────────────────────────────────────────────────────


def _resolve_org(run_or_org):
    from apps.organizations.models import Organization

    if isinstance(run_or_org, Organization):
        return run_or_org
    return getattr(run_or_org, "organization", None)


def _resolve_run(run_or_org):
    from apps.organizations.models import Organization

    return None if isinstance(run_or_org, Organization) else run_or_org


def _approved_profile(org):
    if org is None:
        return None
    try:
        from apps.organizations.models import BrandProfile

        return BrandProfile.objects.filter(organization=org, status=BrandProfile.Status.APPROVED).first()
    except Exception:
        logger.info("build_context: approved-profile lookup failed", exc_info=True)
        return None


# ── Budgeting (char heuristic) ────────────────────────────────────────────


def _budget_blocks(blocks: list[str], max_chars: int) -> str:
    """Drop lowest-priority (tail) sections until it fits; keep at least the first
    (identity), truncating it if even that overflows."""
    kept = list(blocks)
    while len(kept) > 1 and len(_join(kept)) > max_chars:
        kept.pop()
    return _budget_text(_join(kept), max_chars)


def _budget_text(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


# ── Section rendering ─────────────────────────────────────────────────────


def _ordered_blocks(profile) -> list[str]:
    blocks = [
        _fmt_identity(getattr(profile, "identity", {}) or {}),
        _fmt_canonical(getattr(profile, "canonical_facts", {}) or {}),
        _fmt_positioning(getattr(profile, "positioning", {}) or {}),
        _fmt_audience(getattr(profile, "audience", {}) or {}),
        _fmt_voice(getattr(profile, "voice", {}) or {}),
        _fmt_competitors(getattr(profile, "competitors", []) or []),
    ]
    return [b for b in blocks if b]


def _join(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b).strip()


def _kv_block(title: str, pairs: list[tuple[str, object]]) -> str:
    lines = [f"## {title}"]
    for label, value in pairs:
        text = _fmt_value(value)
        if text:
            lines.append(f"- {label}: {text}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _fmt_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items() if v not in (None, "", []))
    return str(value).strip() if value else ""


def _fmt_identity(d: dict) -> str:
    return _kv_block(
        "Identity",
        [
            ("Name", d.get("name")),
            ("Legal name", d.get("legal_name")),
            ("Website", d.get("url")),
            ("Tagline", d.get("tagline")),
            ("Industry", d.get("industry")),
            ("HQ", d.get("hq_location")),
            ("Summary", d.get("short_description")),
        ],
    )


def _fmt_canonical(d: dict) -> str:
    return _kv_block(
        "Verified facts",
        [
            ("Country", d.get("country")),
            ("Currencies", d.get("currencies")),
            ("Payment methods", d.get("payment_methods")),
            ("Languages", d.get("language_hints")),
            ("Addresses", d.get("addresses")),
            ("Shipping", d.get("shipping")),
            ("Contact", d.get("contact_email")),
            ("Known facts", d.get("perception_facts")),
        ],
    )


def _fmt_positioning(d: dict) -> str:
    return _kv_block(
        "Positioning",
        [
            ("Value proposition", d.get("value_proposition")),
            ("Category", d.get("category")),
            ("Differentiators", d.get("differentiators")),
            ("Model", d.get("model_type")),
            ("Price positioning", d.get("price_positioning")),
            ("One-liner", d.get("one_liner")),
        ],
    )


def _fmt_audience(d: dict) -> str:
    return _kv_block(
        "Audience",
        [
            ("Primary segment", d.get("primary_segment")),
            ("Secondary segments", d.get("secondary_segments")),
            ("Target markets", d.get("target_markets")),
            ("Customer segment", d.get("customer_segment")),
            ("Use cases", d.get("use_cases")),
        ],
    )


def _fmt_voice(d: dict) -> str:
    return _kv_block(
        "Voice",
        [
            ("Tone", d.get("tone")),
            ("Style", d.get("style_notes")),
            ("Do", d.get("do")),
            ("Don't", d.get("dont")),
        ],
    )


def _fmt_competitors(items: list) -> str:
    names = [str(c.get("name")) for c in items if isinstance(c, dict) and c.get("name")]
    if not names:
        return ""
    return "## Competitors\n- " + ", ".join(names[:8])


# ── Ephemeral fallback (no approved profile) ──────────────────────────────


def _ephemeral_card(run) -> str:
    lines = []
    name = getattr(run, "brand_name", "") or ""
    url = getattr(run, "url", "") or ""
    country = getattr(run, "country", "") or ""
    if name:
        lines.append(f"Brand: {name}")
    if url:
        lines.append(f"Website: {url}")
    if country:
        lines.append(f"Country: {country}")
    kit = _run_kit(run)
    if kit.get("tagline"):
        lines.append(f"Tagline: {kit['tagline']}")
    if kit.get("short_description"):
        lines.append(f"About: {kit['short_description']}")
    if kit.get("categories"):
        lines.append("Categories: " + ", ".join(str(x) for x in kit["categories"]))
    return "\n".join(lines)


def _run_kit(run) -> dict:
    try:
        bk = getattr(run, "brand_kit", None)
        payload = getattr(bk, "payload", None) if bk is not None else None
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
