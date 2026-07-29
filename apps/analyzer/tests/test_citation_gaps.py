"""Tests for the citation-gap outreach pipeline.

These are the domains engines cited when they answered a tracked prompt and did
not mention the brand. It is the strongest signal in the product because it is
observed rather than inferred: no model opinion about the industry is involved.

Two contracts carry the design:

1. **Ranked by distinct prompts won, not raw citations.** A domain quoted five
   times inside one answer won one prompt, not five.
2. **"Live" is verified, never self-reported.** A user may mark a target pitched
   or dismissed but cannot mark it done - that comes from a presence check, so
   the tracker cannot drift into fiction.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.analyzer.models import (
    AnalysisRun,
    CitationOutreach,
    PromptCitation,
    PromptResult,
    PromptTrack,
)
from apps.analyzer.services import citation_gaps as cg
from apps.organizations.models import Organization


class _Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="o@acme.com")
        self.run = AnalysisRun.objects.create(
            url="https://acme.com", organization=self.org, brand_name="Acme"
        )

    def _prompt(self, text, *, mentioned=False, cites=()):
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text=text)
        result = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", brand_mentioned=mentioned
        )
        for domain in cites:
            PromptCitation.objects.create(
                prompt_result=result, url=f"https://{domain}/x", domain=domain
            )
        return track

    def _gaps(self):
        return cg.collect_gaps(self.run)


class RankingTests(_Base):
    def test_domain_winning_more_prompts_ranks_higher(self):
        self._prompt("q1", cites=["hubspot.com", "semrush.com"])
        self._prompt("q2", cites=["hubspot.com"])
        gaps = self._gaps()
        self.assertEqual(gaps[0].domain, "hubspot.com")
        self.assertEqual(gaps[0].prompts_won, 2)

    def test_repeated_citations_in_one_answer_count_as_one_prompt(self):
        """Otherwise a verbose source outranks a broadly-cited one."""
        track = PromptTrack.objects.create(analysis_run=self.run, prompt_text="q")
        result = PromptResult.objects.create(
            prompt_track=track, engine="chatgpt", brand_mentioned=False
        )
        for i in range(5):
            PromptCitation.objects.create(
                prompt_result=result, url=f"https://loud.com/{i}", domain="loud.com"
            )
        gap = self._gaps()[0]
        self.assertEqual(gap.prompts_won, 1)
        self.assertEqual(gap.citations, 5)

    def test_example_prompts_are_carried_for_context(self):
        self._prompt("what is GEO?", cites=["hubspot.com"])
        self.assertEqual(self._gaps()[0].example_prompts, ["what is GEO?"])


class ExclusionTests(_Base):
    def test_prompts_where_the_brand_was_mentioned_are_not_gaps(self):
        self._prompt("q", mentioned=True, cites=["hubspot.com"])
        self.assertEqual(self._gaps(), [])

    def test_prompts_that_never_fired_are_not_losses(self):
        """No results means unknown, which is not the same as lost."""
        PromptTrack.objects.create(analysis_run=self.run, prompt_text="never fired")
        self.assertEqual(self._gaps(), [])

    def test_the_brands_own_domain_is_not_a_gap(self):
        self._prompt("q", cites=["acme.com", "hubspot.com"])
        self.assertEqual([g.domain for g in self._gaps()], ["hubspot.com"])

    def test_open_platforms_are_excluded(self):
        """'Get mentioned on medium.com' is not a task anyone can complete."""
        self._prompt("q", cites=["medium.com", "youtube.com", "reddit.com", "hubspot.com"])
        self.assertEqual([g.domain for g in self._gaps()], ["hubspot.com"])

    def test_citations_flagged_as_the_brand_are_skipped(self):
        track = self._prompt("q", cites=["hubspot.com"])
        PromptCitation.objects.filter(prompt_result__prompt_track=track).update(is_brand=True)
        self.assertEqual(self._gaps(), [])

    def test_www_prefix_is_normalised(self):
        self._prompt("q1", cites=["www.hubspot.com"])
        self._prompt("q2", cites=["hubspot.com"])
        gaps = self._gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].prompts_won, 2)


class VerifiedLiveTests(_Base):
    """The contract that keeps the tracker honest."""

    def test_presence_on_the_domain_marks_it_live(self):
        self._prompt("q", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", return_value=True):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.LIVE)

    def test_absence_leaves_the_stored_status(self):
        self._prompt("q", cites=["hubspot.com"])
        cg.set_status(self.org, "hubspot.com", cg.PITCHED)
        with patch.object(cg, "_verify_live", return_value=False):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.PITCHED)

    def test_live_cannot_be_set_by_hand(self):
        with self.assertRaises(ValueError):
            cg.set_status(self.org, "hubspot.com", cg.LIVE)

    def test_an_unknown_presence_check_does_not_promote_to_live(self):
        self._prompt("q", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", return_value=None):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.IDENTIFIED)

    def test_dismissed_targets_are_not_verified(self):
        """No point spending a search on something the user is not pursuing."""
        self._prompt("q", cites=["hubspot.com"])
        cg.set_status(self.org, "hubspot.com", cg.DISMISSED)
        with patch.object(cg, "_verify_live", side_effect=AssertionError("should not verify")):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.DISMISSED)

    def test_verify_can_be_skipped_entirely(self):
        self._prompt("q", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", side_effect=AssertionError("should not verify")):
            cg.report_for_run(self.run, verify=False)


class StatusStoreTests(_Base):
    def test_status_is_stored_per_org_and_domain(self):
        cg.set_status(self.org, "HubSpot.com ", cg.PITCHED, note="emailed editor")
        row = CitationOutreach.objects.get(organization=self.org, domain="hubspot.com")
        self.assertEqual(row.status, cg.PITCHED)
        self.assertEqual(row.note, "emailed editor")

    def test_setting_twice_updates_rather_than_duplicates(self):
        cg.set_status(self.org, "hubspot.com", cg.PITCHED)
        cg.set_status(self.org, "hubspot.com", cg.DISMISSED)
        self.assertEqual(CitationOutreach.objects.filter(organization=self.org).count(), 1)

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(ValueError):
            cg.set_status(self.org, "hubspot.com", "done")

    def test_blank_domain_is_rejected(self):
        with self.assertRaises(ValueError):
            cg.set_status(self.org, "  ", cg.PITCHED)


class EndpointTests(_Base):
    def _url(self):
        from django.urls import reverse

        return reverse("analyzer:citation-gaps", args=[self.run.slug])

    def test_get_returns_ranked_targets(self):
        self._prompt("q", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", return_value=False):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["targets"][0]["domain"], "hubspot.com")

    def test_get_can_skip_verification(self):
        self._prompt("q", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", side_effect=AssertionError("should not verify")):
            resp = self.client.get(self._url(), {"verify": "0"})
        self.assertEqual(resp.status_code, 200)

    def test_patch_records_outreach_state(self):
        resp = self.client.patch(
            self._url(),
            data={"domain": "hubspot.com", "status": "pitched"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pitched")

    def test_patching_live_is_rejected(self):
        resp = self.client.patch(
            self._url(),
            data={"domain": "hubspot.com", "status": "live"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class ReachableTargetTests(_Base):
    """A citation is not automatically an opportunity."""

    def test_academic_publishers_are_not_outreach_targets(self):
        for domain in (
            "sciencedirect.com",
            "link.springer.com",
            "researchgate.net",
            "eureka.patsnap.com",
            "arxiv.org",
            "pubmed.ncbi.nlm.nih.gov",
            "mdpi.com",
            "frontiersin.org",
        ):
            self.assertFalse(cg.is_reachable_target(domain), domain)

    def test_university_domains_are_excluded(self):
        self.assertFalse(cg.is_reachable_target("cs.stanford.edu"))
        self.assertFalse(cg.is_reachable_target("ox.ac.uk"))

    def test_open_platforms_remain_excluded(self):
        self.assertFalse(cg.is_reachable_target("medium.com"))

    def test_pitchable_publications_and_tools_are_kept(self):
        for domain in ("blog.hubspot.com", "frase.io", "g2.com", "techcrunch.com", "airops.com"):
            self.assertTrue(cg.is_reachable_target(domain), domain)

    def test_a_domain_merely_containing_edu_is_not_excluded(self):
        """Suffix matching, not substring — 'edutech.com' is a real target."""
        self.assertTrue(cg.is_reachable_target("edutech.com"))

    def test_academic_citations_are_filtered_from_the_queue(self):
        self._prompt("q", cites=["sciencedirect.com", "blog.hubspot.com"])
        self.assertEqual([g.domain for g in self._gaps()], ["blog.hubspot.com"])
