"""Deciding what to change, and doing it safely.

Provider-agnostic by construction: nothing here imports a vendor SDK. Which
provider applies a change is answered through ``core.ports.code_fix``; the
adapters live under ``apps/integrations/<provider>/``.
"""
