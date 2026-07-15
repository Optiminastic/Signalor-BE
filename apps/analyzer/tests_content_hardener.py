"""
Tests for the GEO Content Hardener. The safety core (validate_edits) and the
orchestrator (harden, with injected research/propose) are pure — no network, no
DB — so these run as SimpleTestCase, mirroring apps/github_agent/tests.py.
"""

from django.test import SimpleTestCase

from apps.analyzer.services import content_hardener as ch


class _Run:
    def __init__(self, url="https://acme.com/guide", brand_name="Acme"):
        self.url = url
        self.brand_name = brand_name


BODY = (
    "Switching to solar power is a major decision for homeowners. "
    "Many people wonder whether the upfront cost is worth it over time. "
    "Our team has installed panels across the country for over a decade. "
    "We help families compare options, understand payback, and choose the right system "
    "for their home and budget without the usual sales pressure."
)

# A short, distinctive paragraph that exists verbatim in BODY.
PARA = "Many people wonder whether the upfront cost is worth it over time."

EVIDENCE = [
    ch.Evidence(
        kind="statistic",
        statement="Residential solar adoption grew sharply",
        value="34%",
        source_url="https://www.nrel.gov/solar/market-report",
        source_title="NREL Solar Market Report",
    ),
    ch.Evidence(
        kind="statistic",
        statement="Average payback period",
        value="8 years",
        source_url="https://energy.gov/solar-payback",
        source_title="DOE",
    ),
]


class ValidateEditsTests(SimpleTestCase):
    def test_accepts_grounded_additive_edit(self):
        new = (
            PARA + " According to the NREL Solar Market Report, residential solar adoption grew 34% "
            "(https://www.nrel.gov/solar/market-report)."
        )
        accepted, rejected = ch.validate_edits([{"original": PARA, "new": new}], EVIDENCE, BODY)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected, [])
        self.assertIn("nrel.gov/solar/market-report", accepted[0].evidence_urls[0])

    def test_rejects_fabricated_url(self):
        new = PARA + " A study (https://made-up-source.example/report) proves it."
        accepted, rejected = ch.validate_edits([{"original": PARA, "new": new}], EVIDENCE, BODY)
        self.assertEqual(accepted, [])
        self.assertIn("fabricated/unsourced link", rejected[0]["reason"])

    def test_rejects_unsourced_statistic(self):
        # 92% appears nowhere in the evidence.
        new = PARA + " In fact, 92% of homeowners save money (https://energy.gov/solar-payback)."
        accepted, rejected = ch.validate_edits([{"original": PARA, "new": new}], EVIDENCE, BODY)
        self.assertEqual(accepted, [])
        self.assertIn("unsourced statistic", rejected[0]["reason"])

    def test_rejects_non_additive_rewrite(self):
        # Drops the original wording entirely — not additive.
        new = "Solar pays back in 8 years per https://energy.gov/solar-payback."
        accepted, rejected = ch.validate_edits([{"original": PARA, "new": new}], EVIDENCE, BODY)
        self.assertEqual(accepted, [])
        self.assertIn("not additive", rejected[0]["reason"])

    def test_rejects_original_not_on_page(self):
        ghost = "This sentence is not anywhere in the page body at all."
        new = ghost + " https://energy.gov/solar-payback"
        accepted, rejected = ch.validate_edits([{"original": ghost, "new": new}], EVIDENCE, BODY)
        self.assertEqual(accepted, [])
        self.assertIn("not found", rejected[0]["reason"])

    def test_rejects_no_op_insertion(self):
        # Additive and sourced-of-nothing: adds words but no link and no figure.
        new = PARA + " This is widely known to be true and beneficial for everyone."
        accepted, rejected = ch.validate_edits([{"original": PARA, "new": new}], EVIDENCE, BODY)
        self.assertEqual(accepted, [])
        self.assertIn("cites no source", rejected[0]["reason"])

    def test_caps_at_max_edits(self):
        # Build MAX_EDITS+2 valid distinct targets from BODY sentences.
        sentences = [
            "Switching to solar power is a major decision for homeowners.",
            PARA,
            "Our team has installed panels across the country for over a decade.",
        ]
        raw = [
            {"original": s, "new": s + " See https://energy.gov/solar-payback for details."}
            for s in sentences
        ]
        accepted, _ = ch.validate_edits(raw, EVIDENCE, BODY)
        self.assertLessEqual(len(accepted), ch.MAX_EDITS)

    def test_dedupes_same_target(self):
        raw = [
            {"original": PARA, "new": PARA + " https://energy.gov/solar-payback"},
            {"original": PARA, "new": PARA + " https://www.nrel.gov/solar/market-report"},
        ]
        accepted, rejected = ch.validate_edits(raw, EVIDENCE, BODY)
        self.assertEqual(len(accepted), 1)
        self.assertTrue(any("duplicate" in r["reason"] for r in rejected))

    def test_commas_in_numbers_normalised(self):
        ev = [ch.Evidence("statistic", "installs", "1,200", "https://energy.gov/solar-payback")]
        new = PARA + " Over 1,200 installs (https://energy.gov/solar-payback)."
        accepted, _ = ch.validate_edits([{"original": PARA, "new": new}], ev, BODY)
        self.assertEqual(len(accepted), 1)


class HardenOrchestratorTests(SimpleTestCase):
    def _research_ok(self, topic, kind, *, run=None):
        return EVIDENCE

    def _propose_ok(self, body, evidence, kind, *, run=None):
        return [
            {
                "original": PARA,
                "new": PARA
                + " According to NREL, adoption grew 34% (https://www.nrel.gov/solar/market-report).",
            }
        ]

    def test_unsupported_finding(self):
        out = ch.harden(run=_Run(), finding_code="no_sitemap", page_url="https://acme.com/g", body_text=BODY)
        self.assertFalse(out.ok)
        self.assertIn("not a content-hardening", out.note)

    def test_thin_body_kept_manual(self):
        out = ch.harden(
            run=_Run(),
            finding_code="no_statistics",
            page_url="https://acme.com/g",
            body_text="too short",
            research=self._research_ok,
            propose=self._propose_ok,
        )
        self.assertFalse(out.ok)
        self.assertIn("Not enough page text", out.note)

    def test_no_evidence_kept_manual(self):
        out = ch.harden(
            run=_Run(),
            finding_code="no_statistics",
            page_url="https://acme.com/g",
            body_text=BODY,
            research=lambda *a, **k: [],
            propose=self._propose_ok,
        )
        self.assertFalse(out.ok)
        self.assertIn("No citable sources", out.note)

    def test_happy_path_produces_edit(self):
        out = ch.harden(
            run=_Run(),
            finding_code="no_statistics",
            page_url="https://acme.com/g",
            body_text=BODY,
            research=self._research_ok,
            propose=self._propose_ok,
        )
        self.assertTrue(out.ok)
        self.assertEqual(len(out.edits), 1)
        self.assertEqual(out.edits[0].as_content_edit()["kind"], "text")
        self.assertIn(PARA, out.edits[0].new)

    def test_propose_exception_is_caught(self):
        def _boom(*a, **k):
            raise RuntimeError("llm down")

        out = ch.harden(
            run=_Run(),
            finding_code="no_citations",
            page_url="https://acme.com/g",
            body_text=BODY,
            research=self._research_ok,
            propose=_boom,
        )
        self.assertFalse(out.ok)
        self.assertTrue(out.note)
