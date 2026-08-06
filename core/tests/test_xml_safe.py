"""Entity-expansion hardening for remote XML.

Every XML this backend parses is attacker-controlled: sitemaps come from
whatever domain a user submits for analysis. ``xml.etree.ElementTree`` blocks
external entities on this Python, but it expands internal ones, so a few
hundred bytes of nested entity declarations becomes gigabytes in memory. The
analysis workers are shared, so one hostile domain could OOM the pipeline for
every customer.
"""

import xml.etree.ElementTree as ET

from django.test import SimpleTestCase

from core.xml_safe import UnsafeXMLError, fromstring

# Three levels of a billion-laughs bomb. Kept small on purpose - the point is
# that it is refused, not that this process survives expanding it.
BOMB = (
    '<!DOCTYPE r [<!ENTITY a "AAAAAAAAAA">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><r>&c;</r>'
)

XXE = '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/hostname">]><r>&x;</r>'

SITEMAP = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.com/a</loc></url>"
    b"<url><loc>https://example.com/b</loc></url>"
    b"</urlset>"
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class XmlSafeTests(SimpleTestCase):
    def test_entity_expansion_is_refused(self):
        with self.assertRaises(UnsafeXMLError):
            fromstring(BOMB)

    def test_external_entity_document_is_refused(self):
        with self.assertRaises(UnsafeXMLError):
            fromstring(XXE)

    def test_a_utf16_bomb_is_also_refused(self):
        """A naive byte-scan for '<!DOCTYPE' would miss this one."""
        with self.assertRaises(UnsafeXMLError):
            fromstring(BOMB.encode("utf-16"))

    def test_a_real_sitemap_still_parses(self):
        root = fromstring(SITEMAP)
        locs = [el.text for el in root.findall(".//sm:loc", SITEMAP_NS)]
        self.assertEqual(locs, ["https://example.com/a", "https://example.com/b"])

    def test_malformed_xml_still_raises_parse_error(self):
        """Callers already catch ET.ParseError; that contract must hold."""
        with self.assertRaises(ET.ParseError):
            fromstring(b"<not-closed")

    def test_stdlib_would_have_expanded_it(self):
        """Pins the vulnerability this module exists to close.

        If a future Python starts refusing entity expansion on its own, this
        fails and the whole module can be reconsidered.
        """
        expanded = ET.fromstring(BOMB).text or ""
        self.assertEqual(len(expanded), 1000)  # 10^3 from three nested levels
        # The amplification is the attack: each further level multiplies by ten.
        self.assertGreater(len(expanded), len(BOMB) * 5)


# The per-call-site tests live with the apps that own those parsers
# (apps/analyzer/tests/test_sitemap_xml_safety.py and
# apps/integrations/tests/test_gsc_xml_safety.py). core is a kernel and may not
# import an app - see the import-linter contract in pyproject.toml.
