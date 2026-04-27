import json
import logging
import re
from datetime import datetime, timezone

from .crawler import CrawlResult
from .utils import safe_score

logger = logging.getLogger("apps")

# Required properties per schema type — if these are missing, schema is hollow
SCHEMA_REQUIRED_PROPS = {
    "FAQPage": {"mainEntity"},
    "Article": {"headline", "author", "datePublished"},
    "NewsArticle": {"headline", "author", "datePublished"},
    "BlogPosting": {"headline", "author", "datePublished"},
    "Organization": {"name", "url"},
    "LocalBusiness": {"name", "address"},
    "Product": {"name"},
    "HowTo": {"name", "step"},
    "BreadcrumbList": {"itemListElement"},
    "WebSite": {"name", "url"},
    "WebPage": {"name"},
    "VideoObject": {"name", "uploadDate"},
    "Event": {"name", "startDate"},
    "Review": {"itemReviewed", "reviewRating"},
    "AggregateRating": {"ratingValue", "reviewCount"},
    "SoftwareApplication": {"name"},
    "Service": {"name"},
    "SpeakableSpecification": {"cssSelector"},
    "Person": {"name"},
    "ItemList": {"itemListElement"},
}

# Recommended (optional but valuable) properties per type
SCHEMA_RECOMMENDED_PROPS = {
    "FAQPage": set(),
    "Article": {"image", "publisher", "dateModified", "description"},
    "NewsArticle": {"image", "publisher", "dateModified", "description"},
    "BlogPosting": {"image", "publisher", "dateModified", "description"},
    "Organization": {"logo", "sameAs", "description", "contactPoint", "address"},
    "LocalBusiness": {"telephone", "openingHours", "geo"},
    "Product": {"description", "image", "offers", "brand", "review", "aggregateRating"},
    "HowTo": {"description", "image", "totalTime"},
    "BreadcrumbList": set(),
    "WebSite": {"potentialAction", "description"},
    "WebPage": {"description", "datePublished"},
    "VideoObject": {"description", "thumbnailUrl", "duration"},
    "Event": {"location", "description", "endDate"},
    "Review": {"author", "datePublished"},
    "AggregateRating": {"bestRating"},
    "SoftwareApplication": {"applicationCategory", "offers", "operatingSystem"},
    "Service": {"description", "provider", "areaServed"},
    "SpeakableSpecification": {"xpath"},
    "Person": {"jobTitle", "url", "sameAs", "affiliation"},
    "ItemList": set(),
}


def _extract_jsonld(soup) -> list[dict]:
    schemas = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                schemas.extend(data)
            elif isinstance(data, dict):
                schemas.append(data)
        except (json.JSONDecodeError, TypeError):
            continue
    return schemas


def _get_all_objects(schema: dict) -> list[dict]:
    """Flatten schema into individual typed objects (handles @graph)."""
    objects = []
    if "@type" in schema:
        objects.append(schema)
    for item in schema.get("@graph", []):
        if isinstance(item, dict):
            objects.extend(_get_all_objects(item))
    return objects


def _get_types(schema: dict) -> set[str]:
    types = set()
    t = schema.get("@type", "")
    if isinstance(t, list):
        types.update(t)
    elif isinstance(t, str):
        types.add(t)
    for item in schema.get("@graph", []):
        if isinstance(item, dict):
            types.update(_get_types(item))
    return types


def _compute_completeness(obj: dict, schema_type: str) -> tuple[float, dict]:
    """Score a single schema object on property completeness. Returns (0.0-1.0, report)."""
    required = SCHEMA_REQUIRED_PROPS.get(schema_type, set())
    recommended = SCHEMA_RECOMMENDED_PROPS.get(schema_type, set())
    report = {"required_present": [], "required_missing": [], "recommended_present": [], "recommended_missing": []}

    for prop in required:
        val = obj.get(prop)
        if val is not None and val != "" and val != [] and val != {}:
            report["required_present"].append(prop)
        else:
            report["required_missing"].append(prop)

    for prop in recommended:
        val = obj.get(prop)
        if val is not None and val != "" and val != [] and val != {}:
            report["recommended_present"].append(prop)
        else:
            report["recommended_missing"].append(prop)

    req_total = len(required)
    rec_total = len(recommended)
    req_score = len(report["required_present"]) / req_total if req_total else 1.0
    rec_score = len(report["recommended_present"]) / rec_total if rec_total else 1.0

    completeness = req_score * 0.7 + rec_score * 0.3
    return completeness, report


def score_schema(crawl: CrawlResult) -> tuple[float, dict]:
    """
    Schema scoring — rewards what's present and well-implemented.
    Does NOT penalize for missing page-specific types (Article, FAQPage, etc.)
    since we only crawl one page and those may exist elsewhere on the site.

    Scoring (100 pts):
      20 pts — JSON-LD present + valid structure
      15 pts — Organization/LocalBusiness schema (identity)
      50 pts — quality & completeness of all schemas found (proportional)
      15 pts — schema variety bonus (more types = better)
    """
    if not crawl.ok:
        return 0.0, {"error": crawl.error}

    soup = crawl.soup
    details = {"checks": {}, "findings": [], "types_found": [], "completeness": {}}
    score = 0.0

    schemas = _extract_jsonld(soup)

    # ── JSON-LD present (15 pts) ──────────────────────────────────────────
    if schemas:
        score += 15
        details["checks"]["jsonld_present"] = True
    else:
        details["checks"]["jsonld_present"] = False
        details["findings"].append("no_jsonld")
        details["score"] = 0.0
        return 0.0, details

    # Flatten all schema objects
    all_objects = []
    for s in schemas:
        all_objects.extend(_get_all_objects(s))

    # Collect all types
    all_types = set()
    for s in schemas:
        all_types.update(_get_types(s))

    details["types_found"] = sorted(all_types)

    # ── Valid structure (5 pts) ───────────────────────────────────────────
    valid = True
    for s in schemas:
        if "@context" not in s and "@graph" not in s:
            valid = False
            break
    if valid:
        score += 5
        details["checks"]["valid_structure"] = True
    else:
        details["checks"]["valid_structure"] = False
        details["findings"].append("invalid_jsonld_structure")

    # ── Organization / identity schema (15 pts) ──────────────────────────
    identity_types = {"Organization", "LocalBusiness", "Corporation"}
    has_identity = bool(all_types & identity_types)
    details["checks"]["has_identity_schema"] = has_identity
    if has_identity:
        # Score completeness of identity schema
        for id_type in identity_types:
            if id_type in all_types:
                for obj in all_objects:
                    obj_type = obj.get("@type", "")
                    obj_types = obj_type if isinstance(obj_type, list) else [obj_type]
                    if id_type in obj_types:
                        completeness, report = _compute_completeness(obj, id_type)
                        identity_pts = 15 * completeness
                        score += identity_pts
                        details["completeness"][id_type] = {
                            "completeness": round(completeness * 100),
                            "points": round(identity_pts, 1),
                            "max_points": 15,
                            "required_present": report["required_present"],
                            "required_missing": report["required_missing"],
                            "recommended_present": report["recommended_present"],
                            "recommended_missing": report["recommended_missing"],
                        }
                        break
                break
    else:
        details["findings"].append("no_organization_schema")

    # ── Schema quality & completeness (50 pts) ───────────────────────────
    # Score every schema type found — reward good implementation
    scored_types = set()
    quality_score = 0.0
    quality_entries = 0

    for obj in all_objects:
        obj_type = obj.get("@type", "")
        obj_types = obj_type if isinstance(obj_type, list) else [obj_type]

        for schema_type in obj_types:
            if schema_type in scored_types:
                continue
            if schema_type in identity_types:
                continue  # Already scored above
            if schema_type not in SCHEMA_REQUIRED_PROPS:
                continue  # Unknown type — still counts for variety

            scored_types.add(schema_type)
            completeness, report = _compute_completeness(obj, schema_type)
            quality_entries += 1
            quality_score += completeness

            details["completeness"][schema_type] = {
                "completeness": round(completeness * 100),
                "required_present": report["required_present"],
                "required_missing": report["required_missing"],
                "recommended_present": report["recommended_present"],
                "recommended_missing": report["recommended_missing"],
            }
            details["checks"][f"has_{schema_type}"] = True

            if report["required_missing"]:
                details["findings"].append(f"incomplete_{schema_type.lower()}_schema")

    # dateModified freshness bonus (5 pts) — reward recently updated schemas
    current_year = datetime.now(timezone.utc).year
    for obj in all_objects:
        date_modified = obj.get("dateModified", "") or obj.get("datePublished", "")
        if date_modified:
            try:
                year_match = re.search(r"(\d{4})", str(date_modified))
                if year_match:
                    content_year = int(year_match.group(1))
                    if content_year >= current_year - 1:  # Within last year
                        score += 5
                        details["checks"]["schema_freshness"] = True
                        details["checks"]["schema_date"] = date_modified[:10]
                        break
            except Exception:
                pass

    # Average completeness across found types, scaled to 50 pts
    if quality_entries > 0:
        avg_completeness = quality_score / quality_entries
        # More types found = closer to full 50 pts
        # 1 type = max 60%, 2 types = max 80%, 3+ types = max 100%
        coverage_factor = min(1.0, 0.4 + quality_entries * 0.2)
        quality_pts = 50 * avg_completeness * coverage_factor
        score += quality_pts
        details["checks"]["schema_quality_score"] = round(quality_pts, 1)
    else:
        details["checks"]["schema_quality_score"] = 0

    # ── Schema variety bonus (15 pts) ────────────────────────────────────
    # Reward having diverse schema types — more types = better AI understanding
    type_count = len(all_types)
    if type_count >= 5:
        variety_pts = 15
    elif type_count >= 3:
        variety_pts = 10
    elif type_count >= 2:
        variety_pts = 6
    else:
        variety_pts = 2

    score += variety_pts
    details["checks"]["schema_variety"] = type_count
    details["checks"]["schema_variety_pts"] = variety_pts

    # ── Entity Linking check (bonus/penalty) ──────────────────────────────
    # Does the schema form a connected knowledge graph?
    # author → Person → sameAs, article → author → organization
    entity_links = 0
    entity_checks = []

    for obj in all_objects:
        obj_type = obj.get("@type", "")
        obj_types = obj_type if isinstance(obj_type, list) else [obj_type]

        # Check: author is a Person with sameAs
        author = obj.get("author", {})
        if isinstance(author, dict):
            if author.get("@type") == "Person" and author.get("name"):
                entity_links += 1
                entity_checks.append("author→Person")
                if author.get("sameAs"):
                    entity_links += 1
                    entity_checks.append("author→sameAs")
                if author.get("url"):
                    entity_links += 1
                    entity_checks.append("author→url")

        # Check: publisher is an Organization
        publisher = obj.get("publisher", {})
        if isinstance(publisher, dict) and publisher.get("@type") in ("Organization", "Corporation"):
            entity_links += 1
            entity_checks.append("publisher→Organization")
            if publisher.get("logo"):
                entity_links += 1
                entity_checks.append("publisher→logo")

        # Check: @id references (linked graph)
        if obj.get("@id"):
            entity_links += 1

    details["checks"]["entity_linking"] = entity_checks
    details["checks"]["entity_link_count"] = entity_links

    # Bonus: well-linked graph (up to 5 pts extra, capped by safe_score)
    if entity_links >= 5:
        score += 5
    elif entity_links >= 3:
        score += 3
    elif entity_links >= 1:
        score += 1

    # Penalty: schema exists but completely disconnected (no linking)
    if len(all_objects) >= 2 and entity_links == 0:
        score -= 5
        details["findings"].append("schema_disconnected_graph")

    score = safe_score(score)
    details["score"] = score
    return score, details
