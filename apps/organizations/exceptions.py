"""Exceptions other apps raise about an organization, without this app importing them."""

from __future__ import annotations


class OrganizationDeletionBlocked(Exception):
    """The brand can't be deleted right now (for example a send is in flight or its
    outcome is still unknown). ``detail`` is safe to return to the caller with a 409."""

    code = "unresolved_executions"

    def __init__(self, detail: dict):
        super().__init__("This brand can't be deleted yet.")
        self.detail = {"code": self.code, **detail}
