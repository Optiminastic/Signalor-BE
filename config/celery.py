"""Celery app bootstrap.

Lives at ``config.celery:app`` so the standard Celery worker invocation
(``celery -A config worker``) finds it. Picks up settings via the
``CELERY_*`` keys in ``config.settings`` and autodiscovers tasks from any
``apps.<x>.celery_tasks`` module.

Only enabled task today: ``apps.analyzer.celery_tasks.run_sitemap_audit_task``.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("signalor")
app.config_from_object("django.conf:settings", namespace="CELERY")
# Look for `celery_tasks` modules in each installed app (rather than the
# default `tasks` module) so we don't accidentally pick up threading-only
# helpers that live in `apps/<x>/tasks.py`.
app.autodiscover_tasks(related_name="celery_tasks")
