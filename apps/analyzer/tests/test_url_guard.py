"""SSRF guard for user-supplied URLs.

The analyzer fetches whatever URL a user submits, so a URL resolving to an
internal address (cloud metadata, localhost, RFC1918) must be rejected before it
is stored or fetched.

Run:
    python manage.py test apps.analyzer.tests.test_url_guard
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.analyzer.url_guard import (
    SSRFValidationError,
    validate_public_url,
)


class ValidatePublicUrlTests(SimpleTestCase):
    def test_public_ip_literal_is_allowed(self):
        self.assertEqual(validate_public_url("http://93.184.216.34/"), "http://93.184.216.34/")

    def test_metadata_ip_is_blocked(self):
        with self.assertRaises(SSRFValidationError):
            validate_public_url("http://169.254.169.254/latest/meta-data/")

    def test_loopback_is_blocked(self):
        for url in ("http://127.0.0.1:6379/", "http://localhost:15672/"):
            with self.assertRaises(SSRFValidationError):
                validate_public_url(url)

    def test_private_ranges_are_blocked(self):
        for url in ("http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/"):
            with self.assertRaises(SSRFValidationError):
                validate_public_url(url)

    def test_non_http_scheme_is_blocked(self):
        with self.assertRaises(SSRFValidationError):
            validate_public_url("file:///etc/passwd")

    def test_hostname_resolving_to_private_is_blocked(self):
        # A public-looking name that resolves to an internal IP (split-horizon).
        with mock.patch(
            "apps.analyzer.url_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.1.2.3", 0))],
        ):
            with self.assertRaises(SSRFValidationError):
                validate_public_url("http://evil.example.com/")

    def test_hostname_resolving_to_public_is_allowed(self):
        with mock.patch(
            "apps.analyzer.url_guard.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            self.assertEqual(
                validate_public_url("http://example.com/"), "http://example.com/"
            )
