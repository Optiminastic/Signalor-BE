"""Capability ports: how one app asks another a question without importing it.

Each module here declares an interface that ``core`` owns, and the app that can
answer registers an adapter from its ``AppConfig.ready()``. The asking app
depends on the port; the answering app depends on nothing new. That is what
turns a cycle into a one-way edge.

Same contract as ``core.llm.cache_port`` in every case:

- **Unregistered is valid, not an error.** Every port returns a documented
  neutral value when no adapter is installed, so a worker, a management command
  or a test can boot without the answering app loaded.
- **Adapters are best-effort.** A failure is logged and treated as "cannot
  answer", never propagated into the caller's flow.

See docs/modularization-plan.md §2.2 for the cycles these break.
"""
