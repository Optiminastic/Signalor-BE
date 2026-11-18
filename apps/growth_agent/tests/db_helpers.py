"""
Test-only access past the append-only database triggers (migration 0022).

Some tests fake an old timestamp or simulate tampering by changing a protected
row directly. On Postgres the triggers refuse that, as they should. As a
superuser, ``append_only_bypass`` switches triggers off for the enclosed
statements only (``SET LOCAL session_replication_role = replica``). As the
restricted runtime role that is impossible, which is the point of the role
split, so the test is skipped instead.
"""

from __future__ import annotations

import contextlib
import unittest

from django.db import DatabaseError, connection, transaction


@contextlib.contextmanager
def append_only_bypass():
    if connection.vendor != "postgresql":
        yield
        return
    with transaction.atomic():
        try:
            # In a savepoint, so a refusal doesn't break the surrounding transaction.
            # A released savepoint keeps the setting until the outer block ends.
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
        except DatabaseError as exc:
            raise unittest.SkipTest(f"protected rows can't be changed as this database role: {exc}") from exc
        yield
