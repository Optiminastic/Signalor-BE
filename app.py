"""WSGI entrypoint shim for ``gunicorn app:app``.

The Render staging web service has drifted to a native Python runtime with no
explicit Start Command, so Render falls back to its default ``gunicorn app:app``
instead of the ``config.wsgi:application`` declared in ``render.yaml`` and the
``Dockerfile``. That default crashes with ``ModuleNotFoundError: No module
named 'app'`` and the service never boots.

This module re-exports the Django WSGI callable as ``app`` so the default
command works. It is harmless under the correct command.

Proper fix: in the Render dashboard set the Start Command to
``opentelemetry-instrument gunicorn config.wsgi:application --bind 0.0.0.0:$PORT``
(or re-sync the blueprint so the service runs from the Dockerfile). Once that's
done this shim is inert and can be removed.
"""

from config.wsgi import application

app = application
