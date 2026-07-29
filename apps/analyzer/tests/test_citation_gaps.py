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

    def _as(self, email):
        """Simulate a verified JWT principal (never a client-supplied claim)."""
        from apps.accounts.authentication import VerifiedUser

        return patch(
            "rest_framework.request.Request.user",
            new_callable=lambda: property(lambda _self: VerifiedUser(email=email)),
        )


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

    def test_a_non_string_domain_is_a_value_error_not_an_attribute_error(self):
        """The view turns ValueError into a 400; anything else becomes a 500."""
        for bad in (123, None, ["hubspot.com"], {"domain": "x"}):
            with self.subTest(domain=bad), self.assertRaises(ValueError):
                cg.set_status(self.org, bad, cg.PITCHED)

    def test_a_non_string_note_is_a_value_error(self):
        with self.assertRaises(ValueError):
            cg.set_status(self.org, "hubspot.com", cg.PITCHED, note={"a": 1})

    def test_an_unhashable_status_is_a_value_error_not_a_type_error(self):
        """``x in <set>`` *raises* for a list, so it never reached the verdict."""
        for bad in (["pitched"], {"status": "pitched"}, 1):
            with self.subTest(status=bad), self.assertRaises(ValueError):
                cg.set_status(self.org, "hubspot.com", bad)

    def test_an_unhashable_status_returns_400_not_500(self):
        from django.urls import reverse

        from apps.analyzer.tests.auth_helpers import signed_in

        with signed_in(self.org.owner_email):
            resp = self.client.patch(
                reverse("analyzer:citation-gaps", args=[self.run.slug]),
                data={"domain": "hubspot.com", "status": ["pitched"]},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)

    def test_a_non_string_domain_returns_400_not_500(self):
        from django.urls import reverse

        from apps.analyzer.tests.auth_helpers import signed_in

        with signed_in(self.org.owner_email):
            resp = self.client.patch(
                reverse("analyzer:citation-gaps", args=[self.run.slug]),
                data={"domain": 123, "status": "pitched"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)


class EndpointTests(_Base):
    def _url(self):
        from django.urls import reverse

        return reverse("analyzer:citation-gaps", args=[self.run.slug])

    def _as_owner(self):
        from apps.analyzer.tests.auth_helpers import signed_in

        return signed_in(self.org.owner_email)

    def test_get_returns_ranked_targets(self):
        self._prompt("q", cites=["hubspot.com"])
        with self._as_owner(), patch.object(cg, "_verify_live", return_value=False):
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["targets"][0]["domain"], "hubspot.com")

    def test_get_can_skip_verification(self):
        self._prompt("q", cites=["hubspot.com"])
        with self._as_owner(), patch.object(
            cg, "_verify_live", side_effect=AssertionError("should not verify")
        ):
            resp = self.client.get(self._url(), {"verify": "0"})
        self.assertEqual(resp.status_code, 200)

    def test_patch_records_outreach_state(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email
        ):
            resp = self.client.patch(
                self._url(),
                data={"domain": "hubspot.com", "status": "pitched"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "pitched")

    def test_patching_live_is_rejected(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email
        ):
            resp = self.client.patch(
                self._url(),
                data={"domain": "hubspot.com", "status": "live"},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)


class PromptsLostTests(_Base):
    """The headline metric counts every prompt lost, not the displayed examples.

    ``example_prompts`` is capped at three per domain and the list at
    MAX_TARGETS - both display concerns. Deriving the count from them both
    undercounted and deduplicated by prompt *text*, so two distinct tracks that
    happened to share wording collapsed into one.
    """

    def test_more_than_three_prompts_on_one_domain_are_all_counted(self):
        for i in range(5):
            self._prompt(f"q{i}", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", return_value=False):
            summary = cg.report_for_run(self.run)["summary"]
        self.assertEqual(summary["prompts_lost"], 5)
        # ...while the displayed examples stay capped.
        self.assertEqual(len(self._gaps()[0].example_prompts), 3)

    def test_two_prompts_with_identical_text_count_twice(self):
        self._prompt("same question", cites=["hubspot.com"])
        self._prompt("same question", cites=["hubspot.com"])
        with patch.object(cg, "_verify_live", return_value=False):
            self.assertEqual(cg.report_for_run(self.run)["summary"]["prompts_lost"], 2)

    def test_a_prompt_lost_to_several_domains_counts_once(self):
        self._prompt("q", cites=["hubspot.com", "semrush.com", "ahrefs.com"])
        with patch.object(cg, "_verify_live", return_value=False):
            self.assertEqual(cg.report_for_run(self.run)["summary"]["prompts_lost"], 1)


class ConcurrentVerificationTests(_Base):
    def test_every_eligible_domain_is_verified(self):
        for i, domain in enumerate(["hubspot.com", "semrush.com", "ahrefs.com"]):
            self._prompt(f"q{i}", cites=[domain])
        with patch.object(cg, "_verify_live", return_value=False) as verify:
            cg.report_for_run(self.run)
        self.assertEqual(
            {c.args[1] for c in verify.call_args_list},
            {"hubspot.com", "semrush.com", "ahrefs.com"},
        )

    def test_a_dismissed_target_is_not_re_verified(self):
        self._prompt("q", cites=["hubspot.com"])
        cg.set_status(self.org, "hubspot.com", cg.DISMISSED)
        with patch.object(cg, "_verify_live", side_effect=AssertionError("must not verify")):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.DISMISSED)

    def test_a_verified_presence_wins_over_the_stored_status(self):
        self._prompt("q", cites=["hubspot.com"])
        cg.set_status(self.org, "hubspot.com", cg.PITCHED)
        with patch.object(cg, "_verify_live", return_value=True):
            report = cg.report_for_run(self.run)
        self.assertEqual(report["targets"][0]["status"], cg.LIVE)
        self.assertEqual(report["summary"]["live"], 1)

    def test_one_failing_check_does_not_lose_the_others(self):
        for i, domain in enumerate(["hubspot.com", "semrush.com"]):
            self._prompt(f"q{i}", cites=[domain])

        def _flaky(_brand, domain, industry=""):
            if domain == "hubspot.com":
                raise RuntimeError("search down")
            return True

        with patch("apps.analyzer.pipeline.offpage_presence.brand_present_on_domain", _flaky):
            report = cg.report_for_run(self.run)
        by_domain = {t["domain"]: t for t in report["targets"]}
        self.assertIsNone(by_domain["hubspot.com"]["brand_present"])
        self.assertTrue(by_domain["semrush.com"]["brand_present"])


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


class WriteAuthorizationTests(_Base):
    """PATCH durably mutates CitationOutreach, so a slug alone must not authorize it.

    A run slug is unguessable but it is not a credential: it travels in browser
    history, support tickets and analytics tooling.
    """

    def _url(self):
        from django.urls import reverse

        return reverse("analyzer:citation-gaps", args=[self.run.slug])

    def _patch(self, **body):
        return self.client.patch(
            self._url(),
            data={"domain": "hubspot.com", "status": "pitched", **body},
            content_type="application/json",
        )

    def test_anonymous_write_is_refused_when_auth_is_unconfigured(self):
        """Fails closed, and says the server cannot authenticate rather than 401."""
        with self.settings(BETTER_AUTH_JWKS_URL=""):
            self.assertEqual(self._patch().status_code, 503)

    def test_anonymous_write_is_401_when_auth_is_configured(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"):
            self.assertEqual(self._patch().status_code, 401)

    def test_the_owner_may_write(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email
        ):
            self.assertEqual(self._patch().status_code, 200)

    def test_a_verified_non_owner_gets_404_not_403(self):
        """403 would confirm the run exists to someone who should not know."""
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            "stranger@example.com"
        ):
            self.assertEqual(self._patch().status_code, 404)

    def test_owner_match_is_case_insensitive(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email.upper()
        ):
            self.assertEqual(self._patch().status_code, 200)

    def test_an_agency_teammate_may_write(self):
        """Teammates manage their agency's brands; owner_email is the agency's."""
        from apps.accounts.models import AgencyMembership

        AgencyMembership.objects.create(
            agency_email=self.org.owner_email,
            member_email="mate@agency.com",
            status=AgencyMembership.Status.ACTIVE,
        )
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            "mate@agency.com"
        ):
            self.assertEqual(self._patch().status_code, 200)

    def test_an_invited_but_inactive_teammate_may_not_write(self):
        from apps.accounts.models import AgencyMembership

        AgencyMembership.objects.create(
            agency_email=self.org.owner_email,
            member_email="pending@agency.com",
            status=AgencyMembership.Status.INVITED,
        )
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            "pending@agency.com"
        ):
            self.assertEqual(self._patch().status_code, 404)


class ReadScopingTests(_Base):
    """Reads fail closed, exactly like writes.

    A slug is not a credential. These endpoints are new and have no shipped
    client, so there was nothing to stage: gating reads on the rollout flag would
    only have widened the unauthenticated surface this API is shrinking.
    """

    def _url(self, slug=None):
        from django.urls import reverse

        return reverse("analyzer:citation-gaps", args=[slug or self.run.slug])

    def _get(self, slug=None):
        return self.client.get(self._url(slug), {"verify": "0"})

    def test_an_anonymous_read_is_refused(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"):
            self.assertEqual(self._get().status_code, 401)

    def test_an_anonymous_read_is_refused_even_with_the_rollout_flag_off(self):
        """The flag governs the ~78 legacy endpoints, not these."""
        with self.settings(
            BETTER_AUTH_JWKS_URL="https://auth.example/jwks", REQUIRE_VERIFIED_IDENTITY=False
        ):
            self.assertEqual(self._get().status_code, 401)

    def test_an_unconfigured_deployment_says_so_rather_than_401(self):
        with self.settings(BETTER_AUTH_JWKS_URL=""):
            self.assertEqual(self._get().status_code, 503)

    def test_the_owner_may_read(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email
        ):
            self.assertEqual(self._get().status_code, 200)

    def test_a_verified_stranger_cannot_read_another_brands_run(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            "stranger@example.com"
        ):
            self.assertEqual(self._get().status_code, 404)

    def test_an_unknown_slug_is_404_for_a_verified_caller(self):
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"), self._as(
            self.org.owner_email
        ):
            self.assertEqual(self._get("nope").status_code, 404)

    def test_an_anonymous_caller_cannot_tell_a_real_slug_from_a_fake_one(self):
        """Both 401: looking the run up first would leak which slugs exist."""
        with self.settings(BETTER_AUTH_JWKS_URL="https://auth.example/jwks"):
            self.assertEqual(self._get().status_code, self._get("nope").status_code)
