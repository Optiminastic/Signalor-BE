"""
Satellite articles: one article on one of signalor.ai's own sites, mentioning
the client, written from a planned topic (``planning/satellite.py``).

- ``prepare``: write the article (grounded in the client's own facts, checked by
  ``safety.check_article``), fix its slug on the chosen site, and leave it for
  the owner's approval. Nothing is published.
- ``execute`` (only after approval): publish exactly the approved article, then
  tell Bing about the new page when an IndexNow key is set for the satellite
  sites.

The client link is added here, never by the model: the article text is
HTML-escaped by ``render_html`` and the single link to the client carries
``rel="sponsored"``, as Google asks for placed links.
"""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlparse

from django.conf import settings

from apps.analyzer import blog_store
from apps.analyzer.services import indexnow, satellite_publish

from ...models import ActionVersion, GrowthAction, GrowthProgram, GrowthTask
from .article import ArticleDraft, render_html, render_markdown, slugify
from .brand import display_name
from .channel_base import ChannelOutcome, Prepared
from .safety import check_article
from .writer import gather_knowledge, write_article

logger = logging.getLogger("apps")
CONNECTOR = "satellite"
SOURCE = "growth_agent"
NOT_SET_UP = "signalor.ai's satellite sites aren't set up for publishing yet."
UNKNOWN_SITE = "This task doesn't point at one of signalor.ai's sites."
SLUG_TAKEN = "That address on the site is already used by another post."


def site_key(target_url: str) -> str:
    """The satellite site key for a site address, or ""."""
    host = urlparse(target_url or "").netloc.lower()
    for key in satellite_publish.SITES:
        if host and urlparse(satellite_publish.site_domain(key)).netloc.lower() == host:
            return key
    return ""


def brand_ref(task: GrowthTask) -> str:
    return f"org:{task.cycle.program.organization_id}"


def sponsored_mention(brand: str, brand_url: str) -> str:
    """The one client link in the article, marked as a placed (sponsored) link."""
    name, url = html.escape(brand), html.escape(brand_url, quote=True)
    return (
        f'<p><em>Featured partner: <a href="{url}" rel="sponsored noopener">{name}</a>. '
        "signalor.ai features partner brands in its articles.</em></p>"
    )


def article_html(article: ArticleDraft, brand: str, brand_url: str) -> str:
    return render_html(article) + ("\n" + sponsored_mention(brand, brand_url) if brand_url else "")


def _notify_bing(url: str, site: str) -> dict:
    """Send the new page to Bing. Needs the satellite sites' own IndexNow key."""
    key = getattr(settings, "SATELLITE_INDEXNOW_KEY", "")
    if not key:
        return {"bing": "not_sent", "bing_message": "No IndexNow key is set up on the satellite sites yet."}
    result = indexnow.submit(satellite_publish.site_domain(site), [url], key=key)
    return {"bing": "sent" if result.ok else "failed", "bing_message": result.message}


REFRESH_TACTIC = "satellite_refresh"
NOT_OURS = "That article isn't this brand's article on the site any more."
CURRENT_TEXT_CHARS = 4000


def _post_ref(url: str) -> tuple[str, str]:
    """(site key, slug) of a published satellite article URL."""
    return site_key(url), urlparse(url or "").path.strip("/")


def _plain(content_html: str) -> str:
    return re.sub(r"<[^>]+>", " ", content_html or "")


class SatelliteContentChannel:
    """New articles (tactic ``satellite_article``) and refreshes of published ones
    (``satellite_refresh``, created by the weekly ranking check for lagging articles)."""

    def readiness(self, program: GrowthProgram) -> str:
        return "" if blog_store.is_configured() else NOT_SET_UP

    def _write(self, task: GrowthTask, knowledge: str) -> ArticleDraft | ChannelOutcome:
        article = write_article(task, knowledge)
        if article is None:
            return ChannelOutcome(
                False, "The writer model did not return an article.", external_call_made=False
            )
        verdict = check_article(article, display_name(task.cycle.program.organization), knowledge)
        if not verdict.passed:
            payload = {"issues": list(verdict.issues)}
            return ChannelOutcome(
                False, "Blocked by brand-safety check.", payload=payload, external_call_made=False
            )
        return article

    def _prepared(self, task: GrowthTask, article: ArticleDraft, site: str, slug: str, mode: str) -> Prepared:
        payload = {
            "article": article.model_dump(),
            "slug": slug,
            "site": site,
            "brand_url": task.cycle.program.organization.url or "",
            "mode": mode,
        }
        return Prepared(
            kind=GrowthAction.Kind.PUBLISH_ARTICLE,
            connector=CONNECTOR,
            payload=payload,
            destination={
                "platform": CONNECTOR,
                "site": site,
                "site_url": satellite_publish.site_domain(site),
            },
            preview={"title": article.title, "slug": slug, "markdown": render_markdown(article, {})},
        )

    def prepare(self, task: GrowthTask) -> Prepared | ChannelOutcome:
        if task.tactic == REFRESH_TACTIC:
            return self._prepare_refresh(task)
        site = site_key(task.target_url)
        if not site:
            return ChannelOutcome(False, UNKNOWN_SITE, retryable=False, external_call_made=False)
        article = self._write(task, gather_knowledge(task))
        if isinstance(article, ChannelOutcome):
            return article
        return self._prepared(
            task, article, site, satellite_publish.unique_slug(site, slugify(article.title)), "new"
        )

    def _prepare_refresh(self, task: GrowthTask) -> Prepared | ChannelOutcome:
        site, slug = _post_ref(task.target_url)
        current = blog_store.get_post(site, slug) if site and slug else None
        if not current or current.get("brand_ref") != brand_ref(task):
            return ChannelOutcome(False, NOT_OURS, retryable=False, external_call_made=False)
        # The rewrite sees the live text; the safety check sees the same knowledge.
        knowledge = (
            gather_knowledge(task)
            + "\n\nCURRENT ARTICLE:\n"
            + _plain(current.get("content_html", ""))[:CURRENT_TEXT_CHARS]
        )
        article = self._write(task, knowledge)
        if isinstance(article, ChannelOutcome):
            return article
        return self._prepared(task, article, site, slug, "refresh")

    def execute(self, task: GrowthTask, version: ActionVersion) -> ChannelOutcome:
        if not blog_store.is_configured():
            return ChannelOutcome(False, NOT_SET_UP, external_call_made=False)
        payload = version.payload
        site, slug = payload.get("site", ""), payload.get("slug", "")
        if site != version.destination.get("site") or site not in satellite_publish.SITES:
            return ChannelOutcome(False, UNKNOWN_SITE, retryable=False, external_call_made=False)
        existing = blog_store.get_post(site, slug)
        if existing and existing.get("brand_ref") != brand_ref(task):
            return ChannelOutcome(False, SLUG_TAKEN, retryable=False, external_call_made=False)
        if payload.get("mode") == "refresh":
            if not existing:
                return ChannelOutcome(False, NOT_OURS, retryable=False, external_call_made=False)
            return self._refresh(task, payload, site, slug)
        url = satellite_publish.live_url(site, slug)
        if existing:  # published by an earlier attempt of this same action
            return ChannelOutcome(True, "Already published.", evidence_url=url, payload={"site": site})
        return self._publish(task, payload, site, slug)

    def _refresh(self, task: GrowthTask, payload: dict, site: str, slug: str) -> ChannelOutcome:
        """Same address, new text. Re-running it writes the same approved text again."""
        article = ArticleDraft.model_validate(payload["article"])
        brand = display_name(task.cycle.program.organization)
        post = satellite_publish.refresh_post(
            site=site,
            slug=slug,
            title=article.title,
            content_html=article_html(article, brand, payload.get("brand_url", "")),
            brand_url=payload.get("brand_url", ""),
            description=article.meta_description,
        )
        evidence = {"site": site, "refreshed": True, **_notify_bing(post["url"], site)}
        return ChannelOutcome(True, "Refreshed.", evidence_url=post["url"], payload=evidence)

    def _publish(self, task: GrowthTask, payload: dict, site: str, slug: str) -> ChannelOutcome:
        article = ArticleDraft.model_validate(payload["article"])
        brand = display_name(task.cycle.program.organization)
        post = satellite_publish.publish_post(
            site=site,
            slug=slug,
            title=article.title,
            content_html=article_html(article, brand, payload.get("brand_url", "")),
            brand_url=payload.get("brand_url", ""),
            brand_ref=brand_ref(task),
            description=article.meta_description,
            source=SOURCE,
        )
        evidence = {"site": site, **_notify_bing(post["url"], site)}
        return ChannelOutcome(True, "Published.", evidence_url=post["url"], payload=evidence)
