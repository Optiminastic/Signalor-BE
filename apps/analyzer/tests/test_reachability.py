"""Tests for the link-reachability check (pipeline/utils.py).

``drop_unreachable`` exists so a generated link that 404s never reaches the user
as an action to take. The URLs it checks are model-generated, which makes the
fetch target attacker-influenced - so the check runs through the SSRF-guarded
session, and a non-public target is refused rather than probed.
"""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from apps.analyzer.pipeline import utils
from apps.analyzer.url_guard import SSRFValidationError


def _resp(status):
    r = MagicMock()
    r.status_code = status
    return r


class ReachabilityTests(SimpleTestCase):
    def _session(self, **kwargs):
        session = MagicMock()
        session.head.configure_mock(**kwargs.pop("head", {}))
        session.get.configure_mock(**kwargs.pop("get", {}))
        return session

    def _patched(self, session):
        return patch.object(utils, "guarded_session", return_value=session)

    def test_a_non_http_scheme_is_never_fetched(self):
        session = self._session()
        with self._patched(session):
            self.assertFalse(utils.url_is_reachable("file:///etc/passwd"))
        session.head.assert_not_called()

    def test_a_successful_head_is_reachable(self):
        session = self._session(head={"return_value": _resp(200)})
        with self._patched(session):
            self.assertTrue(utils.url_is_reachable("https://acme.com/x"))

    def test_a_404_is_unreachable(self):
        session = self._session(head={"return_value": _resp(404)})
        with self._patched(session):
            self.assertFalse(utils.url_is_reachable("https://acme.com/x"))

    def test_a_head_rejecting_host_falls_back_to_get(self):
        """Plenty of sites answer 403/405 to HEAD but serve GET normally."""
        session = self._session(
            head={"return_value": _resp(405)}, get={"return_value": _resp(200)}
        )
        with self._patched(session):
            self.assertTrue(utils.url_is_reachable("https://acme.com/x"))

    def test_a_head_exception_falls_back_to_get(self):
        session = self._session(
            head={"side_effect": requests.RequestException("reset")},
            get={"return_value": _resp(200)},
        )
        with self._patched(session):
            self.assertTrue(utils.url_is_reachable("https://acme.com/x"))

    def test_both_failing_is_unreachable_not_an_exception(self):
        session = self._session(
            head={"side_effect": requests.RequestException("a")},
            get={"side_effect": requests.RequestException("b")},
        )
        with self._patched(session):
            self.assertFalse(utils.url_is_reachable("https://acme.com/x"))

    def test_an_internal_target_is_refused_not_probed(self):
        """Blind SSRF: kept/dropped would otherwise leak whether a host exists."""
        session = self._session(head={"side_effect": SSRFValidationError("private")})
        with self._patched(session):
            self.assertFalse(utils.url_is_reachable("http://169.254.169.254/latest/meta-data/"))
        session.get.assert_not_called()

    def test_an_ssrf_refusal_during_the_get_fallback_is_handled(self):
        session = self._session(
            head={"side_effect": requests.RequestException("reset")},
            get={"side_effect": SSRFValidationError("private")},
        )
        with self._patched(session):
            self.assertFalse(utils.url_is_reachable("https://acme.com/x"))

    def test_the_session_is_closed(self):
        session = self._session(head={"return_value": _resp(200)})
        with self._patched(session):
            utils.url_is_reachable("https://acme.com/x")
        session.close.assert_called_once()

    def test_the_streamed_fallback_response_is_closed(self):
        """stream=True defers the body, so this one genuinely holds a connection."""
        response = _resp(200)
        session = self._session(head={"return_value": _resp(403)}, get={"return_value": response})
        with self._patched(session):
            utils.url_is_reachable("https://acme.com/x")
        response.close.assert_called_once()


class DropUnreachableTests(SimpleTestCase):
    def test_unreachable_rows_are_dropped(self):
        rows = [{"url": "https://a.com"}, {"url": "https://b.com"}]
        with patch.object(utils, "url_is_reachable", side_effect=lambda u: u.endswith("a.com")):
            self.assertEqual(utils.drop_unreachable(rows, "url"), [{"url": "https://a.com"}])

    def test_an_empty_list_short_circuits(self):
        self.assertEqual(utils.drop_unreachable([], "url"), [])
