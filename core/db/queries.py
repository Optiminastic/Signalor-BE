"""Query helpers that survive a dropped database connection.

``_safe_first`` lived in ``analyzer.services.site_resolution`` but has 18 call
sites and no analyzer knowledge: it retries a ``.first()`` once across a
recycled connection, then degrades to None rather than 500-ing the request.

The retry matters because long-lived worker processes and pooled connections both
see ``OperationalError``/``InterfaceError`` when the database closes an idle
socket. ``close_old_connections()`` drops the stale handle so the retry gets a
fresh one.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError, close_old_connections
from django.db.utils import InterfaceError, OperationalError

logger = logging.getLogger("apps")


def safe_first(queryset, context: str = "query"):
    """``queryset.first()``, retried once on a dropped connection, else None.

    ``context`` is only used for the log line - pass something that identifies
    the call site, since a None return is otherwise indistinguishable from "no
    matching row".
    """
    try:
        return queryset.first()
    except (OperationalError, InterfaceError):
        close_old_connections()
        try:
            return queryset.first()
        except (OperationalError, InterfaceError, DatabaseError):
            logger.warning("DB unavailable during %s.", context)
            return None
    except DatabaseError:
        logger.warning("Database error during %s.", context)
        return None
