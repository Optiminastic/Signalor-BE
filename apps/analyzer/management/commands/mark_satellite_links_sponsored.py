"""
Add rel="sponsored noopener" to client links in posts already on the satellite sites.

Posts published by the old daily batch link to the client with plain links.
This rewrites only links pointing at each post's own client (``brand_url``);
everything else in the post is left as is.

Dry run by default (reports what would change). Write with --apply:
    python manage.py mark_satellite_links_sponsored            # report only
    python manage.py mark_satellite_links_sponsored --apply    # rewrite posts
"""

from django.core.management.base import BaseCommand, CommandError

from apps.analyzer import blog_store
from apps.analyzer.services.satellite_publish import SITES, sponsor_client_links


class Command(BaseCommand):
    help = 'Mark client links in existing satellite posts as rel="sponsored".'

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Rewrite the posts (default: report only).")

    def handle(self, *args, **options):
        if not blog_store.is_configured():
            raise CommandError("The satellite blog store isn't configured (BACKLINKS_BLOG_AWS_*).")
        checked = changed = 0
        for site in SITES:
            for summary in blog_store.list_index(site):
                checked += 1
                if self._fix(site, summary.get("slug", ""), options["apply"]):
                    changed += 1
        verb = "Rewrote" if options["apply"] else "Would rewrite"
        self.stdout.write(self.style.SUCCESS(f"{verb} {changed} of {checked} post(s)."))

    def _fix(self, site: str, slug: str, apply: bool) -> bool:
        post = blog_store.get_post(site, slug) if slug else None
        if not post:
            return False
        html = post.get("content_html") or ""
        fixed = sponsor_client_links(html, post.get("brand_url") or "")
        if fixed == html:
            return False
        self.stdout.write(f"{site}/{slug}")
        if apply:
            blog_store.update_post(site, slug, {"content_html": fixed})
        return True
