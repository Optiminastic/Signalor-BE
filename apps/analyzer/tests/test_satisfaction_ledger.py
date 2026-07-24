"""Tests for the satisfaction ledger (services/satisfaction_ledger.py)."""

from django.test import TestCase

from apps.analyzer.models import AnalysisRun, TaskSatisfaction
from apps.analyzer.pipeline.satisfaction import PageSignals
from apps.analyzer.services.satisfaction_ledger import (
    apply_gate,
    record_satisfied,
    recorded_satisfied,
)
from apps.organizations.models import Organization

_FAQ_HTML = "<html><body><h2>FAQ</h2><p>Answers.</p></body></html>"
_BARE_HTML = "<html><body><p>just text</p></body></html>"


def _rec(code: str, url: str) -> dict:
    return {"finding_code": code, "affected_pages": [url]}


class LedgerTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", owner_email="a@b.com", url="https://acme.com")
        self.run = AnalysisRun.objects.create(url="https://acme.com", organization=self.org)

    def test_record_is_an_upsert(self):
        record_satisfied(organization_id=self.org.id, page_url="https://acme.com/a",
                         finding_code="no_faq_section", content_hash="h1")
        # Same key (trailing slash normalizes) → updates, does not duplicate.
        record_satisfied(organization_id=self.org.id, page_url="https://acme.com/a/",
                         finding_code="no_faq_section", content_hash="h2")
        self.assertEqual(TaskSatisfaction.objects.count(), 1)
        led = recorded_satisfied(self.org.id, ["https://acme.com/a"])
        self.assertEqual(led[("https://acme.com/a", "no_faq_section")], "h2")

    def test_gate_suppresses_and_records_via_verifier(self):
        ps = PageSignals.from_html("https://acme.com/a", _FAQ_HTML)
        kept, sup = apply_gate(self.run, [_rec("no_faq_section", "https://acme.com/a")],
                               {"https://acme.com/a": ps})
        self.assertEqual(len(sup), 1)
        self.assertEqual(len(kept), 0)
        # Deterministic confirmation was written to the ledger for future runs.
        self.assertTrue(
            TaskSatisfaction.objects.filter(finding_code="no_faq_section", content_hash=ps.content_hash).exists()
        )

    def test_tier0_suppresses_unchanged_page_without_reverifying(self):
        # A bare page the verifier would REJECT, but the ledger recorded it done
        # at this exact content hash → suppress via memory (e.g. user marked done).
        ps = PageSignals.from_html("https://acme.com/a", _BARE_HTML)
        record_satisfied(organization_id=self.org.id, page_url="https://acme.com/a",
                         finding_code="no_faq_section", content_hash=ps.content_hash, source="user")
        kept, sup = apply_gate(self.run, [_rec("no_faq_section", "https://acme.com/a")],
                               {"https://acme.com/a": ps})
        self.assertEqual(len(sup), 1)

    def test_resurfaces_when_content_changes(self):
        old = PageSignals.from_html("https://acme.com/a", _BARE_HTML)
        record_satisfied(organization_id=self.org.id, page_url="https://acme.com/a",
                         finding_code="no_faq_section", content_hash=old.content_hash, source="user")
        # Page content changed → hash differs → ledger miss, verifier still says not done → keep.
        new = PageSignals.from_html("https://acme.com/a", "<html><body><p>brand new copy here</p></body></html>")
        kept, sup = apply_gate(self.run, [_rec("no_faq_section", "https://acme.com/a")],
                               {"https://acme.com/a": new})
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(sup), 0)

    def test_anonymous_run_uses_pure_filter_and_records_nothing(self):
        anon = AnalysisRun.objects.create(url="https://anon.com")  # no organization
        ps = PageSignals.from_html("https://anon.com/a", _FAQ_HTML)
        kept, sup = apply_gate(anon, [_rec("no_faq_section", "https://anon.com/a")],
                               {"https://anon.com/a": ps})
        self.assertEqual(len(sup), 1)  # pure deterministic still suppresses
        self.assertEqual(TaskSatisfaction.objects.count(), 0)  # nothing persisted (no org)
