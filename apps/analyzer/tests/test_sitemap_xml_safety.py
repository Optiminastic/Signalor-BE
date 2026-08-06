"""The sitemap parser must refuse an entity bomb.

Sitemaps are fetched from whatever domain a user submits for analysis, so the
XML is attacker-controlled. ``xml.etree.ElementTree`` expands internal entities,
which turns a few hundred bytes into gigabytes and OOMs the shared analysis
worker. ``core.xml_safe`` removes the mechanism by refusing any DTD; this pins
that the pipeline actually goes through it.
"""

from django.test import SimpleTestCase

from apps.analyzer.pipeline.sitemap_audit import _parse_sitemap_xml

BOMB = (
    b'<!DOCTYPE r [<!ENTITY a "AAAAAAAAAA">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><r>&c;</r>'
)

SITEMAP = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.com/a</loc></url>"
    b"<url><loc>https://example.com/b</loc></url>"
    b"</urlset>"
)


class SitemapXmlSafetyTests(SimpleTestCase):
    def test_an_entity_bomb_yields_nothing_instead_of_expanding(self):
        self.assertEqual(_parse_sitemap_xml(BOMB), ([], []))

    def test_a_real_sitemap_is_unaffected(self):
        _children, urls = _parse_sitemap_xml(SITEMAP)

        self.assertEqual(urls, ["https://example.com/a", "https://example.com/b"])
