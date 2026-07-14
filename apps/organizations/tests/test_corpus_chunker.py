"""Tests for the pure HTML->chunk segmenter (Epic 3). No DB, no network."""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.organizations.services import corpus_chunker as ck

_PAGE = """
<html><head><title>Acme - Home</title></head><body>
  <nav>home about pricing</nav>
  <h1>Acme Widgets</h1>
  <p>Acme builds durable widgets for industrial teams worldwide today.</p>
  <h2>Pricing</h2>
  <p>Plans start at forty nine dollars per month billed annually upfront.</p>
  <h3>Enterprise</h3>
  <p>Enterprise plans include SSO, audit logs, and a dedicated success manager.</p>
  <script>var tracking = 1;</script>
  <footer>copyright acme 2026 all rights reserved</footer>
</body></html>
"""


class ChunkerTests(SimpleTestCase):
    def test_heading_path_is_hierarchical(self):
        drafts = ck.chunk_page(_PAGE, url="https://acme.com")
        paths = [d.heading_path for d in drafts]
        self.assertIn(["Acme Widgets"], paths)
        self.assertIn(["Acme Widgets", "Pricing"], paths)
        self.assertIn(["Acme Widgets", "Pricing", "Enterprise"], paths)

    def test_strips_script_nav_footer(self):
        blob = " ".join(d.text for d in ck.chunk_page(_PAGE, url="https://acme.com"))
        self.assertNotIn("tracking", blob)
        self.assertNotIn("copyright acme", blob)
        self.assertNotIn("home about pricing", blob)

    def test_page_title_captured_in_metadata(self):
        drafts = ck.chunk_page(_PAGE, url="https://acme.com")
        self.assertTrue(all(d.metadata["page_title"] == "Acme - Home" for d in drafts))

    def test_content_hash_is_deterministic(self):
        a = ck.chunk_page(_PAGE, url="https://acme.com")
        b = ck.chunk_page(_PAGE, url="https://acme.com")
        self.assertEqual([d.content_hash for d in a], [d.content_hash for d in b])
        self.assertTrue(all(len(d.content_hash) == 64 for d in a))

    @patch.object(ck, "CHUNK_MIN_CHARS", 1)
    def test_tiny_fragments_dropped_below_min(self):
        # With the real 40-char floor, a 5-char paragraph must not produce a chunk.
        html = "<body><h1>Hi</h1><p>tiny</p></body>"
        with patch.object(ck, "CHUNK_MIN_CHARS", 40):
            self.assertEqual(ck.chunk_page(html, url="https://x.com"), [])

    @patch.object(ck, "CHUNK_MIN_CHARS", 1)
    @patch.object(ck, "CHUNK_OVERLAP_CHARS", 20)
    @patch.object(ck, "CHUNK_MAX_CHARS", 100)
    def test_long_section_splits_into_overlapping_windows(self):
        body = "word " * 60  # ~300 chars in one section
        html = f"<body><h1>Doc</h1><p>{body}</p></body>"
        drafts = ck.chunk_page(html, url="https://x.com")
        self.assertGreater(len(drafts), 1)
        self.assertTrue(all(len(d.text) <= 100 for d in drafts))
        # Overlap means the tail of window 0 reappears at the head of window 1.
        self.assertTrue(drafts[0].text[-10:] in drafts[1].text or drafts[1].text[:10] in drafts[0].text)
