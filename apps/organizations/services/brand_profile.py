"""
Bootstrap a persistent, org-scoped BrandProfile from existing analyzer signals (Epic 2).

Interpretive sections (identity/positioning/audience/voice) are LLM-synthesized in ONE
``ask_structured`` round-trip; factual sections (canonical_facts/competitors/sources) are
mapped deterministically. The whole thing is fail-soft: it never raises into the orchestrator,
never re-crawls (market profile is passed in), and never clobbers a human-APPROVED profile.

Analyzer imports are function-local (like ``apps/analyzer/services/brand_kit.py``) to keep
``analyzer -> organizations`` the only hard dependency direction.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("apps")

_SYNTH_SYSTEM = (
    "You are a brand analyst. Synthesize a structured brand profile ONLY from the data "
    "provided. Never invent facts (no fabricated founders, dates, revenue, or claims). "
    "Leave any unknown field as an empty string or empty list."
)

_MAX_COMPETITORS = 8


def bootstrap_from_run(run, *, market_profile: dict | None = None, crawl=None, force: bool = False):
    """Build/refresh a PENDING BrandProfile for ``run.organization``. Returns the
    profile, or ``None`` for org-less (anonymous) runs. Never raises."""
    try:
        return _bootstrap(run, market_profile=market_profile, crawl=crawl, force=force)
    except Exception:
        logger.warning("brand_profile bootstrap failed for run=%s", getattr(run, "pk", "?"), exc_info=True)
        return None


def _bootstrap(run, *, market_profile, crawl, force):
    from apps.organizations.models import BrandProfile

    org = getattr(run, "organization", None)
    if org is None:
        return None

    existing = BrandProfile.objects.filter(organization=org).first()
    if existing and existing.status == BrandProfile.Status.APPROVED and not force:
        return existing  # never clobber human approval

    kit = _gather_brand_kit(run)
    market = market_profile if market_profile is not None else _gather_market(crawl)
    competitors = _gather_competitors(run)

    synth = _synthesize(run, kit, market, competitors)  # BrandSynthesis | None
    identity, positioning, audience, voice = _sections_from_synth(synth, kit, run)

    profile, _created = BrandProfile.objects.update_or_create(
        organization=org,
        defaults={
            "status": BrandProfile.Status.PENDING,
            "source_run": run,
            "confidence": _confidence(kit, market, competitors, synth),
            "identity": identity,
            "positioning": positioning,
            "audience": audience,
            "voice": voice,
            "canonical_facts": _map_canonical_facts(run, kit, market),
            "competitors": _map_competitors(competitors),
            "sources": _build_sources(kit, market, competitors),
        },
    )
    return profile


# ── Gather (each fail-soft) ───────────────────────────────────────────────


def _gather_brand_kit(run) -> dict:
    try:
        from apps.analyzer.services import brand_kit

        return brand_kit.get_or_generate(run) or {}
    except Exception:
        logger.info("brand_profile: brand_kit unavailable", exc_info=True)
        return {}


def _gather_market(crawl) -> dict:
    if crawl is None:
        return {}
    try:
        from apps.analyzer.pipeline.market_profiler import build_brand_market_profile

        return build_brand_market_profile(crawl) or {}
    except Exception:
        logger.info("brand_profile: market profile unavailable", exc_info=True)
        return {}


def _gather_competitors(run) -> list:
    try:
        comps = list(run.competitors.all())
    except Exception:
        return []
    comps.sort(key=lambda c: getattr(c, "relevance_score", None) or 0, reverse=True)
    return comps[:_MAX_COMPETITORS]


# ── LLM synthesis ─────────────────────────────────────────────────────────


def _synthesize(run, kit, market, competitors):
    from apps.analyzer.pipeline.structured import ask_structured
    from apps.organizations.schemas import BrandSynthesis

    return ask_structured(
        _build_synth_prompt(run, kit, market, competitors),
        BrandSynthesis,
        tier="medium",
        system=_SYNTH_SYSTEM,
        max_tokens=1200,
        temperature=0.2,
        purpose=f"brand_profile:org={getattr(run, 'organization_id', '?')}",
    )


def _build_synth_prompt(run, kit, market, competitors) -> str:
    comp_names = ", ".join(getattr(c, "name", "") for c in competitors if getattr(c, "name", ""))
    return (
        "Synthesize a structured brand profile for the brand below.\n\n"
        f"=== Brand submission kit ===\n{_fmt_kit(kit, run)}\n\n"
        f"=== Market signals ===\n{_fmt_market(market)}\n\n"
        f"=== Known competitors ===\n{comp_names or '(none found)'}\n\n"
        "Produce identity, positioning, audience, and voice. Ground target_markets and "
        "model_type in the market signals above. Keep copy concise and factual."
    )


def _sections_from_synth(synth, kit, run):
    if synth is not None:
        identity = synth.identity.model_dump()
        positioning = synth.positioning.model_dump()
        audience = synth.audience.model_dump()
        voice = synth.voice.model_dump()
    else:
        # Fail-soft: seed identity verbatim from the kit; leave the rest empty.
        identity = {
            "name": kit.get("name", ""),
            "legal_name": "",
            "tagline": kit.get("tagline", ""),
            "short_description": kit.get("short_description", ""),
            "long_description": kit.get("long_description", ""),
            "industry": "",
            "hq_location": kit.get("location", ""),
        }
        positioning, audience, voice = {}, {}, {}

    # url is deterministic -- never let the model guess it.
    identity["url"] = kit.get("url", "") or getattr(run, "url", "") or ""
    if not identity.get("name"):
        identity["name"] = kit.get("name", "") or getattr(run, "brand_name", "") or ""
    return identity, positioning, audience, voice


# ── Deterministic maps ────────────────────────────────────────────────────


def _map_canonical_facts(run, kit, market) -> dict:
    signals = (market or {}).get("signals") or {}
    return {
        "country": getattr(run, "country", "") or (market or {}).get("top_market") or "",
        "currencies": _as_str_list(signals.get("currencies")),
        "addresses": _as_str_list(signals.get("addresses")),
        "contact_email": kit.get("contact_email", "") or getattr(run, "email", "") or "",
        "payment_methods": _as_str_list(signals.get("payment_methods")),
        "shipping": signals.get("shipping") if isinstance(signals.get("shipping"), dict) else {},
        "language_hints": _as_str_list(signals.get("language_hints")),
        # v1: perception facts are not re-fired during bootstrap (keeps it to one LLM call).
        "perception_facts": [],
    }


def _map_competitors(competitors) -> list:
    out = []
    for c in competitors:
        out.append(
            {
                "name": getattr(c, "name", "") or "",
                "url": getattr(c, "url", "") or "",
                "tier": getattr(c, "tier", "") or "",
                "target_market": getattr(c, "target_market", "") or "",
                "geography": getattr(c, "geography", "") or "",
                "pricing_model": getattr(c, "pricing_model", "") or "",
                "positioning": getattr(c, "positioning", "") or "",
                "relevance_score": getattr(c, "relevance_score", None),
            }
        )
    return out


def _confidence(kit, market, competitors, synth) -> float:
    score = 0.0
    if kit:
        score += 0.3
    if market and (market.get("top_market_confidence") or 0) >= 0.35:
        score += 0.25
    if len(competitors) >= 3:
        score += 0.2
    if synth is not None:
        score += 0.25
    return round(min(score, 1.0), 3)


def _build_sources(kit, market, competitors) -> dict:
    from django.utils import timezone

    return {
        "brand_kit": bool(kit),
        "market_profile": bool(market),
        "competitors_count": len(competitors),
        "ai_perception": False,
        "generated_at": timezone.now().isoformat(),
        "bootstrap_version": "1",
        "model_tier": "medium",
    }


# ── Formatting helpers ────────────────────────────────────────────────────


def _as_str_list(value) -> list:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v is not None][:20]


def _fmt_kit(kit, run) -> str:
    if not kit:
        return (
            f"name: {getattr(run, 'brand_name', '')}\n"
            f"url: {getattr(run, 'url', '')}\n"
            f"country: {getattr(run, 'country', '')}"
        )
    lines = []
    for key in ("name", "tagline", "short_description", "long_description", "location"):
        val = kit.get(key)
        if val:
            lines.append(f"{key}: {val}")
    if kit.get("categories"):
        lines.append("categories: " + ", ".join(str(x) for x in kit["categories"]))
    if kit.get("keywords"):
        lines.append("keywords: " + ", ".join(str(x) for x in kit["keywords"]))
    return "\n".join(lines) or "(no kit data)"


def _fmt_market(market) -> str:
    if not market:
        return "(no market data)"
    lines = []
    if market.get("top_market"):
        lines.append(f"top_market: {market['top_market']} (confidence {market.get('top_market_confidence')})")
    if market.get("model_type"):
        lines.append(f"model_type: {market['model_type']}")
    signals = market.get("signals") or {}
    for key in ("currencies", "payment_methods", "language_hints", "addresses"):
        val = signals.get(key)
        if isinstance(val, list) and val:
            lines.append(f"{key}: " + ", ".join(str(x) for x in val[:6]))
    if isinstance(signals.get("shipping"), dict) and signals["shipping"]:
        lines.append(f"shipping: {signals['shipping']}")
    return "\n".join(lines) or "(no market data)"
