"""Every run-scoped endpoint declares an authorization and a rate-limit policy.

CLAUDE.md forbids adding an endpoint without scope/authorization checks *or* a
rate-limit policy. Both are easy to forget on a new view and neither shows up in
a functional test, so they are pinned here as a contract over the view classes
rather than re-tested per endpoint.

The 429 behaviour itself is not asserted: throttles count in the shared cache,
which the test settings deliberately replace with a DummyCache so that cached
state cannot leak between tests. What is enforceable here - and what actually
regresses - is that a policy is declared at all.
"""

from django.test import SimpleTestCase

from apps.analyzer import views
from core.permissions.throttling import ExpensiveThrottle, PollingThrottle

# (view, expected throttle, handlers that must refuse an unverified caller).
# Every handler is listed: _scoped_run fails closed, so reads are gated too.
POLICY = [
    (views.CitationGapsView, PollingThrottle, {"get", "patch"}),
    (views.PromptCoverageView, PollingThrottle, {"get"}),
    (views.CrawlerAccessView, PollingThrottle, {"get"}),
    (views.PromptAnswerBlockView, ExpensiveThrottle, {"post"}),
    (views.EntityResolutionView, ExpensiveThrottle, {"post"}),
    (views.IndexNowView, ExpensiveThrottle, {"get", "post"}),
]


class ThrottlePolicyTests(SimpleTestCase):
    def test_every_run_scoped_view_declares_a_throttle(self):
        for view, expected, _ in POLICY:
            with self.subTest(view=view.__name__):
                self.assertIn(expected, view.throttle_classes)

    def test_billable_views_use_the_expensive_bucket(self):
        """A paid call must not share the generous polling budget."""
        for view, expected, _ in POLICY:
            if expected is ExpensiveThrottle:
                with self.subTest(view=view.__name__):
                    self.assertNotIn(PollingThrottle, view.throttle_classes)


class AuthorizationPolicyTests(SimpleTestCase):
    def test_every_run_scoped_view_resolves_its_run_through_the_scoping_seam(self):
        """Not get_object_or_404 directly: that is the cross-tenant hole."""
        import inspect

        for view, _, _ in POLICY:
            with self.subTest(view=view.__name__):
                source = inspect.getsource(view)
                self.assertIn("_scoped_run(", source)
                self.assertNotIn("get_object_or_404(AnalysisRun", source)

    def test_every_handler_resolves_its_run_before_doing_any_work(self):
        """_scoped_run fails closed, so reaching it first is the whole gate."""
        import inspect

        for view, _, handlers in POLICY:
            for method in handlers:
                with self.subTest(view=view.__name__, method=method):
                    source = inspect.getsource(getattr(view, method))
                    self.assertIn("_scoped_run(request, slug)", source)
                    self.assertIn("if err is not None:", source)

    def test_the_scoping_seam_has_no_opt_out(self):
        """A per-call flag is how reads drifted open once already."""
        import inspect

        signature = inspect.signature(views._scoped_run)
        self.assertEqual(list(signature.parameters), ["request", "slug"])
