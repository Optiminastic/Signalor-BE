"""Background task entrypoints, separated from the apps that own the logic.

A worker module is a thin celery binding: it decorates, retries and reports. The
work itself stays in the owning app's ``services/`` so it is callable and
testable without a broker.

Registration is EXPLICIT (``config/celery*.py`` ``imports``), not autodiscovery.
Autodiscovery only scans ``<installed_app>/<related_name>.py``, so a task moved
out of an app would silently never register - it would queue and never run.
Every task also pins an explicit ``name=``, so task identity survives any future
move; queued messages are unaffected by where the module lives.
"""
