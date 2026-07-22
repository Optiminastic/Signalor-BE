"""Analysis-worker readiness check.

A live web process with a dead analysis worker is a distinct failure the general
``/health/`` misses — the API answers but no run can finish. These cover the
helper (``analysis_worker_health``) and the ``/health/worker/`` endpoint that
returns 503 when nothing is consuming.

Run:
    python manage.py test apps.analyzer.tests.test_worker_health
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.analyzer.health import analysis_worker_health
from apps.analyzer.views import WorkerHealthView
from config.celery_rabbit import analysis_app

_PONG = [{"celery@analysis-worker": {"ok": "pong"}}]


class AnalysisWorkerHealthTests(SimpleTestCase):
    def test_eager_mode_is_healthy_without_a_broker(self):
        # Local dev / tests run the pipeline in-process, so there's no worker to
        # be down — the check must not report a false outage.
        with mock.patch.object(analysis_app.conf, "task_always_eager", True):
            result = analysis_worker_health()
        self.assertTrue(result["ok"])

    def test_no_worker_responding_is_unhealthy(self):
        with (
            mock.patch.object(analysis_app.conf, "task_always_eager", False),
            mock.patch.object(analysis_app.control, "ping", return_value=[]),
        ):
            result = analysis_worker_health()
        self.assertFalse(result["ok"])
        self.assertEqual(result["workers"], 0)

    def test_worker_responding_is_healthy(self):
        with (
            mock.patch.object(analysis_app.conf, "task_always_eager", False),
            mock.patch.object(analysis_app.control, "ping", return_value=_PONG),
        ):
            result = analysis_worker_health()
        self.assertTrue(result["ok"])
        self.assertEqual(result["workers"], 1)

    def test_broker_unreachable_is_unhealthy(self):
        with (
            mock.patch.object(analysis_app.conf, "task_always_eager", False),
            mock.patch.object(
                analysis_app.control, "ping", side_effect=OSError("connection refused")
            ),
        ):
            result = analysis_worker_health()
        self.assertFalse(result["ok"])


class WorkerHealthViewTests(SimpleTestCase):
    def _get(self):
        request = APIRequestFactory().get("/api/analyzer/health/worker/")
        return WorkerHealthView.as_view()(request)

    def test_returns_503_when_no_worker(self):
        with (
            mock.patch.object(analysis_app.conf, "task_always_eager", False),
            mock.patch.object(analysis_app.control, "ping", return_value=[]),
        ):
            response = self._get()
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], "unhealthy")

    def test_returns_200_when_worker_alive(self):
        with (
            mock.patch.object(analysis_app.conf, "task_always_eager", False),
            mock.patch.object(analysis_app.control, "ping", return_value=_PONG),
        ):
            response = self._get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "healthy")
