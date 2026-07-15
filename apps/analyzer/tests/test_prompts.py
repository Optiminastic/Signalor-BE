"""Tests for the prompt registry (Epic 5).

Guards the "one source of truth" contract: every registered prompt renders with a
representative context, the manifest and template files stay in sync, versions can be
pinned, and a missing variable fails loudly (StrictUndefined) rather than silently.
"""

from django.test import SimpleTestCase
from jinja2 import TemplateNotFound
from jinja2.exceptions import UndefinedError

from apps.analyzer.prompts import current_version, list_prompts, render
from apps.analyzer.prompts.manifest import MANIFEST
from apps.analyzer.prompts.registry import _TEMPLATES_DIR

# Representative context for every registered prompt (keys must match template variables).
SAMPLES = {
    "brand_prompts": dict(count=10, context="CTX", brand_name="Acme"),
    "brand_synthesis": dict(kit_block="K", market_block="M", competitors="(none found)"),
    "brand_synthesis_system": dict(),
    "auto_fix_content": dict(
        title="T", description="D", action="A", brand="Acme", url="u", page_content="PC"
    ),
    "auto_fix_jsonld": dict(brand="Acme", url="u", page_content="PC"),
    "auto_fix_meta": dict(brand="Acme", url="u", title="T", action="A", page_content="PC"),
    "auto_fix_llms_txt": dict(brand="Acme", url="u", action="A"),
    "auto_fix_robots": dict(brand="Acme", url="u"),
    "geo_meta": dict(brand_name="Acme", site_url="u", current_title="(not set)", current_desc="(not set)"),
    "geo_jsonld": dict(brand_name="Acme", site_url="u", description="A business website"),
    "geo_product_desc": dict(product_title="P", brand_name="Acme"),
    "judge_eval": dict(task="T", output="O", context="", reference="", format_spec=""),
    "geo_llms_txt": dict(brand_name="Acme", site_url="u"),
}


class PromptRegistryTests(SimpleTestCase):
    def test_samples_cover_manifest(self):
        # Guards this test file itself: a new prompt must get a sample here.
        self.assertEqual(set(SAMPLES), set(MANIFEST))

    def test_every_prompt_renders_nonempty(self):
        for name in list_prompts():
            with self.subTest(prompt=name):
                out = render(name, **SAMPLES[name])
                self.assertTrue(out.strip(), f"{name} rendered empty")

    def test_manifest_matches_template_dirs(self):
        dirs = {p.name for p in _TEMPLATES_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")}
        self.assertEqual(dirs, set(MANIFEST), "template dirs and MANIFEST disagree")
        for name, ver in MANIFEST.items():
            self.assertTrue(
                (_TEMPLATES_DIR / name / f"{ver}.j2").is_file(),
                f"missing template file for {name}/{ver}",
            )

    def test_version_can_be_pinned(self):
        self.assertEqual(current_version("brand_prompts"), "v1")
        pinned = render("brand_prompts", version="v1", **SAMPLES["brand_prompts"])
        self.assertEqual(pinned, render("brand_prompts", **SAMPLES["brand_prompts"]))

    def test_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            current_version("does_not_exist")
        with self.assertRaises(KeyError):
            render("does_not_exist")

    def test_unknown_version_raises(self):
        with self.assertRaises(TemplateNotFound):
            render("brand_prompts", version="v99", **SAMPLES["brand_prompts"])

    def test_missing_variable_fails_loudly(self):
        with self.assertRaises(UndefinedError):
            render("auto_fix_robots")  # brand/url not provided -> StrictUndefined

    def test_brand_prompts_faithful_substitution(self):
        out = render("brand_prompts", count=7, context="MY-CONTEXT", brand_name="Zephyr")
        self.assertIn(
            "generate 7 prompts that real people would type into ChatGPT, Gemini, Perplexity, or Claude.",
            out,
        )
        self.assertIn("CONTEXT:\nMY-CONTEXT", out)
        self.assertIn('NEVER mention "Zephyr" in any prompt', out)
        self.assertIn("Return ONLY a JSON array of 7 strings.", out)

    def test_auto_fix_meta_keeps_literal_json_braces(self):
        out = render("auto_fix_meta", **SAMPLES["auto_fix_meta"])
        self.assertIn('{"seo_title": "...", "seo_description": "..."}', out)
