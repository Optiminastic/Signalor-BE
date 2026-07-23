"""Tests for the grounded marginal-impact engine (pipeline/impact.py)."""

from django.test import SimpleTestCase

from apps.analyzer.pipeline import impact
from apps.analyzer.pipeline.impact import (
    FINDING_DIMENSION,
    PILLAR_SUBWEIGHTS,
    estimate_marginal_gain,
    format_impact_estimate,
)


def _details(**checks) -> dict:
    """Wrap sub-dimension scores the way the scorers store them."""
    out: dict[str, dict] = {"content": {"checks": {}}, "eeat": {"checks": {}},
                            "technical": {"checks": {}}, "schema": {"checks": {}}}
    for key, val in checks.items():
        pillar = {
            "intent_score": "content", "coverage_score": "content",
            "density_score": "content", "structure_score": "content",
            "identity_score": "eeat", "evidence_score": "eeat",
            "experience_score": "eeat", "trust_score": "eeat",
            "infra_score": "technical", "perf_score": "technical",
            "crawl_score": "technical", "ai_read_score": "technical",
            "struct_score": "technical",
        }[key]
        out[pillar]["checks"][key] = val
    return out


class SubweightIntegrityTests(SimpleTestCase):
    def test_subweights_sum_to_one_per_pillar(self):
        for pillar, dims in PILLAR_SUBWEIGHTS.items():
            self.assertAlmostEqual(sum(dims.values()), 1.0, msg=pillar)

    def test_finding_dimension_keys_reference_known_subweights(self):
        for code, (pillar, score_key, _share) in FINDING_DIMENSION.items():
            if score_key is None:
                continue  # bucket-model finding (schema/entity)
            self.assertIn(pillar, PILLAR_SUBWEIGHTS, code)
            self.assertIn(score_key, PILLAR_SUBWEIGHTS[pillar], code)


class MarginalMathTests(SimpleTestCase):
    def test_dimension_gain_uses_live_headroom(self):
        # no_faq_section -> content/coverage_score (weight .30), share .25.
        # coverage at 0 -> full headroom; recoverable = min(25, 100) = 25.
        # pillar_gain = 0.25 * 0.30 * 100 = 7.5 ; composite = 7.5 * 0.25 (default content weight)
        gain = estimate_marginal_gain(
            "no_faq_section", _details(coverage_score=0.0), {"content": 0.0}
        )
        self.assertEqual(gain["pillar"], "content")
        self.assertAlmostEqual(gain["pillar_points"], 7.5, places=1)
        self.assertAlmostEqual(gain["composite_points"], 1.88, places=1)
        self.assertEqual(gain["basis"]["mode"], "dimension")

    def test_headroom_clamps_gain_to_zero_when_dimension_full(self):
        gain = estimate_marginal_gain(
            "no_faq_section", _details(coverage_score=100.0), {"content": 90.0}
        )
        self.assertEqual(gain["composite_points"], 0.0)
        self.assertIn("already scores well", format_impact_estimate(gain))

    def test_partial_headroom_is_respected(self):
        # coverage at 90 -> headroom 10; recoverable = min(25, 10) = 10.
        gain = estimate_marginal_gain(
            "no_faq_section", _details(coverage_score=90.0), {"content": 50.0}
        )
        self.assertAlmostEqual(gain["pillar_points"], 3.0, places=1)  # 0.10*0.30*100

    def test_bucket_model_for_schema(self):
        # no_jsonld -> schema bucket, share .35, schema score 0 -> headroom 100.
        # pillar_gain = min(35, 100) = 35 ; composite = 35 * 0.10 (schema weight) = 3.5
        gain = estimate_marginal_gain("no_jsonld", {"schema": {"checks": {}}}, {"schema": 0.0})
        self.assertEqual(gain["basis"]["mode"], "bucket")
        self.assertAlmostEqual(gain["pillar_points"], 35.0, places=1)
        self.assertAlmostEqual(gain["composite_points"], 3.5, places=1)

    def test_missing_subscore_falls_back_to_bucket(self):
        # coverage_score not stored -> bucket path on the content pillar.
        gain = estimate_marginal_gain("no_faq_section", {"content": {"checks": {}}}, {"content": 40.0})
        self.assertEqual(gain["basis"]["mode"], "bucket")
        self.assertGreater(gain["composite_points"], 0.0)

    def test_unmapped_finding_uses_coarse_fallback_no_percentage(self):
        gain = estimate_marginal_gain(
            "totally_unknown_finding", {}, {"content": 30.0},
            rule_pillar="content", fallback_impact=50.0,
        )
        self.assertEqual(gain["basis"]["mode"], "fallback")
        self.assertGreaterEqual(gain["composite_points"], 0.0)
        self.assertNotIn("%", format_impact_estimate(gain))

    def test_industry_weights_change_composite(self):
        d = _details(evidence_score=0.0)
        default = estimate_marginal_gain("no_statistics", d, {"eeat": 0.0}, industry="default")
        health = estimate_marginal_gain("no_statistics", d, {"eeat": 0.0}, industry="health")
        # eeat weight is higher for health (0.30 vs 0.25) -> larger composite gain.
        self.assertGreater(health["composite_points"], default["composite_points"])


class PhrasingTests(SimpleTestCase):
    def test_format_is_grounded_and_ascii(self):
        gain = {"composite_points": 1.88, "pillar_points": 7.5, "pillar": "content"}
        text = format_impact_estimate(gain)
        self.assertIn("points overall", text)
        self.assertIn("content", text)
        self.assertNotIn("%", text)
