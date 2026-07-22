"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

# Default to production for the WSGI (gunicorn) entrypoint — deploys set this
# explicitly, but the fallback must be a real settings module, NOT the empty
# `config/settings/__init__.py` that a bare `config.settings` resolves to.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')

application = get_wsgi_application()
