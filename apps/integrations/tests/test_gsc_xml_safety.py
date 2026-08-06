"""The Search Console sitemap parser must refuse an entity bomb.

The sitemaps GSC reports are fetched unauthenticated from the customer's own
site, so the XML is remote and not ours. ``core.xml_safe`` refuses any DTD,
which removes the entity-expansion amplification rather than trying to bound it.
"""

from django.test import SimpleTestCase

from apps.integrations.services.gsc import _parse_sitemap

BOMB = (
    b'<!DOCTYPE r [<!ENTITY a "AAAAAAAAAA">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><r>&c;</r>'
)

SITEMAP = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.com/a</loc></url>"
    b"</urlset>"
)


class GscXmlSafetyTests(SimpleTestCase):
    def test_an_entity_bomb_yields_nothing_instead_of_expanding(self):
        self.assertEqual(_parse_sitemap(BOMB), ([], []))

    def test_a_real_sitemap_is_unaffected(self):
        _children, urls = _parse_sitemap(SITEMAP)

        self.assertEqual(urls, ["https://example.com/a"])
