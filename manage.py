#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def _default_settings_module() -> str:
    """Pick the settings module for this invocation.

    ``manage.py test`` used to fall through to the development settings, whose
    LocMemCache is process-global and outlives each test's DB rollback. DRF
    throttle buckets then leak across tests and roughly twenty unrelated
    assertions fail with 429 depending on execution order — failures that look
    like real bugs and are not. ``config.settings.test`` uses DummyCache
    precisely to stop that (see the comment there), and CI always passes
    ``--settings=config.settings.test`` explicitly.

    An explicit ``--settings=`` on the command line, or a DJANGO_SETTINGS_MODULE
    already in the environment, still wins — this only changes the default.
    """
    if 'test' in sys.argv[1:2] and not any(a.startswith('--settings') for a in sys.argv):
        return 'config.settings.test'
    return 'config.settings.development'


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', _default_settings_module())
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
