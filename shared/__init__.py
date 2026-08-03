"""Dependency-free primitives shared by every layer.

The split against ``core/``: **shared/ is data, core/ is behavior.**
Types, constants, exception classes, schemas and validators live here; clients,
adapters, middleware and I/O live in ``core/``. Without that rule the two become
interchangeable and every new util triggers a "which package?" debate.

Nothing in here may import Django models, an app, or ``core``.
"""
