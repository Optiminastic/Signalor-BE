"""Importing the Celery app here ensures @shared_task decorators find it
when Django boots a worker or web process."""

from .celery import app as celery_app

__all__ = ["celery_app"]
