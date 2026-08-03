"""Embedding configuration shared by the models that store vectors and the
client that produces them.

``EMBEDDING_DIMENSIONS`` lived in ``apps.organizations.models``, which made it a
model module that two apps and the embedding client all had to import just to
read one integer. That is one of the edges in the analyzer <-> organizations
cycle (docs/modularization-plan.md §2.2).

It belongs here: the vector width is a property of the embedding provider, not
of the brand-corpus domain. ``organizations`` and ``analyzer`` both declare
``VectorField(dimensions=...)`` against it, so it must stay a plain constant with
no Django imports — importing this module must never trigger app loading.
"""

from __future__ import annotations

# Width of the vectors produced by the configured embedding provider.
# Changing this requires migrating every VectorField column that references it.
EMBEDDING_DIMENSIONS = 768
