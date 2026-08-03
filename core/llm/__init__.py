"""Provider-agnostic LLM infrastructure shared by every app.

Phase 1 of docs/modularization-plan.md. Modules land here as their app-level
dependencies are removed; ``client`` and ``structured`` are still in
``apps/analyzer/pipeline`` because they reach the ``LLMResponseCache`` model.
See the plan's Phase 2 for how that inverts.
"""
