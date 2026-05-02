"""
Citation Authority service.

For a given prompt, gathers every domain cited across its results, plus the
brand's own domain, and enriches each with backlink metrics from DataForSEO
(referring domains, total backlinks, domain rank). Snapshots are cached
(``BacklinkSnapshot``) for 7 days so the panel is cheap on repeat opens.

This module owns the orchestration. It does NOT know about HTTP — views in
``apps/analyzer/views.py`` delegate to ``BacklinkAuthorityService`` and
translate the result.

SOLID notes:
  * SRP — one job: aggregate citations + enrich with backlink metrics.
  * Dependency Inversion — the view depends on this service's small API,
    not on the DataForSEO client directly. Swap providers later by changing
    just this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.utils import timezone

from apps.analyzer.models import (
    AnalysisRun,
    BacklinkSnapshot,
    PromptCitation,
    PromptTrack,
)
from apps.integrations.services.dataforseo import (
    DataForSEOError,
    DataForSEONotConfigured,
    fetch_domain_metrics,
)

logger = logging.getLogger("apps")

SNAPSHOT_TTL = timedelta(days=7)


class ProviderNotConfigured(RuntimeError):
    """Raised when DataForSEO credentials are missing — view returns 503."""


@dataclass
class BacklinkAuthorityService:
    """
    Build the Citation Authority panel payload for one prompt.

    Constructor takes the resolved ``PromptTrack`` and exposes ``build()`` as
    its single useful operation.
    """

    track: PromptTrack
    _domain_stats: dict[str, dict] = field(default_factory=dict, init=False)
    _brand_domain: str = field(default="", init=False)

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self) -> dict:
        """
        Return the panel payload. Shape::

            {
              "brand_domain": "example.com",
              "rows": [{"domain": ..., "referring_domains": ..., ...}, ...],
              "api_used": bool,
              "api_error": str | None,
              "fetched_at": iso_string,
            }

        Raises ``ProviderNotConfigured`` if DataForSEO credentials are absent
        AND we have no cached snapshots to fall back on.
        """
        self._brand_domain = _domain_from_url(self.track.analysis_run.url)
        self._collect_citation_domains()
        self._ensure_brand_row()

        all_domains = list(self._domain_stats.keys())
        if not all_domains:
            return {
                "brand_domain": self._brand_domain,
                "rows": [],
                "api_used": False,
                "api_error": None,
                "fetched_at": timezone.now().isoformat(),
            }

        snapshots = self._load_cached_snapshots(all_domains)
        api_used, api_error = self._refresh_stale_snapshots(snapshots, all_domains)

        return {
            "brand_domain": self._brand_domain,
            "rows": self._compose_rows(snapshots),
            "api_used": api_used,
            "api_error": api_error,
            "fetched_at": timezone.now().isoformat(),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _collect_citation_domains(self) -> None:
        citations = (
            PromptCitation.objects
            .filter(prompt_result__prompt_track=self.track)
            .exclude(domain="")
            .values("domain", "is_brand", "is_competitor")
        )
        for c in citations:
            d = (c["domain"] or "").lower().strip()
            if d.startswith("www."):
                d = d[4:]
            if not d:
                continue
            entry = self._domain_stats.setdefault(d, {
                "domain": d,
                "citation_count": 0,
                "is_brand": False,
                "is_competitor": False,
            })
            entry["citation_count"] += 1
            if c["is_brand"]:
                entry["is_brand"] = True
            if c["is_competitor"]:
                entry["is_competitor"] = True

    def _ensure_brand_row(self) -> None:
        """Pin the brand's own domain into the result so 'vs. You' always renders."""
        if not self._brand_domain:
            return
        entry = self._domain_stats.setdefault(self._brand_domain, {
            "domain": self._brand_domain,
            "citation_count": 0,
            "is_brand": True,
            "is_competitor": False,
        })
        entry["is_brand"] = True

    def _load_cached_snapshots(self, domains: list[str]) -> dict[str, BacklinkSnapshot]:
        return {
            s.domain: s
            for s in BacklinkSnapshot.objects.filter(domain__in=domains)
        }

    def _refresh_stale_snapshots(
        self,
        snapshots: dict[str, BacklinkSnapshot],
        all_domains: list[str],
    ) -> tuple[bool, str | None]:
        """
        Find domains with no snapshot or a stale one, fetch them, write them back
        into ``snapshots`` in place. Returns (api_used, api_error_string).
        """
        cutoff = timezone.now() - SNAPSHOT_TTL
        missing = [
            d for d in all_domains
            if d not in snapshots or snapshots[d].fetched_at < cutoff
        ]
        if not missing:
            return False, None

        try:
            metrics = fetch_domain_metrics(missing)
        except DataForSEONotConfigured as exc:
            raise ProviderNotConfigured(str(exc)) from exc
        except (DataForSEOError, requests.RequestException) as exc:
            logger.warning(
                "DataForSEO fetch failed for prompt %d: %s", self.track.pk, exc,
            )
            return False, str(exc)

        for domain, m in metrics.items():
            snap, _ = BacklinkSnapshot.objects.update_or_create(
                domain=domain,
                defaults={
                    "referring_domains": m["referring_domains"],
                    "backlinks": m["backlinks"],
                    "rank": m["rank"],
                },
            )
            snapshots[domain] = snap
        return True, None

    def _compose_rows(self, snapshots: dict[str, BacklinkSnapshot]) -> list[dict]:
        rows = []
        for domain, stats in self._domain_stats.items():
            snap = snapshots.get(domain)
            rows.append({
                "domain": domain,
                "is_brand": stats["is_brand"],
                "is_competitor": stats["is_competitor"],
                "citation_count": stats["citation_count"],
                "referring_domains": snap.referring_domains if snap else 0,
                "backlinks": snap.backlinks if snap else 0,
                "rank": snap.rank if snap else 0,
                "has_data": snap is not None,
            })
        # Authority (referring domains) first, then citation frequency, then alphabetical.
        rows.sort(key=lambda r: (-r["referring_domains"], -r["citation_count"], r["domain"]))
        return rows


def _domain_from_url(url: str) -> str:
    """Strip scheme + www., lowercase. Empty-string-safe."""
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    netloc = (parsed.netloc or "").lower().strip()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc
