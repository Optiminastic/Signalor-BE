"""Contact finding through a brand's signed-in Hunter or Apollo MCP server.

Neither provider publishes its MCP tool names, so each lookup reads the tool
list and picks a tool by name (Hunter: ``domain_search``; Apollo: a people
search, and a people match to reveal one email). Arguments are filled only
for the parameters the tool's input schema declares. If no fitting tool is
offered, the error names what the server does offer, so the mapping can be
corrected without guessing.
"""

from __future__ import annotations

from collections.abc import Callable

from apps.integrations.models import Integration

from . import apollo, hunter, oauth
from .base import PITCH_TITLES, Candidate, FinderError
from .mcp_client import McpAuthError, McpSession

HUNTER_LIMIT = 10
APOLLO_PER_PAGE = 10
DOMAIN_PARAMS = (
    "q_organization_domains_list",
    "organization_domains",
    "q_organization_domains",
    "domains",
    "domain",
)
PERSON_ID_PARAMS = ("id", "person_id", "apollo_id")


def _properties(tool: dict) -> dict:
    return (tool.get("inputSchema") or {}).get("properties") or {}


def _pick(tools: list[dict], match: Callable[[str], bool], purpose: str, label: str) -> dict:
    found = [t for t in tools if match((t.get("name") or "").lower())]
    if not found:
        offered = ", ".join(sorted(t.get("name", "?") for t in tools)) or "none"
        raise FinderError(f"{label}'s connector offers no {purpose} tool (offered: {offered})")
    return min(found, key=lambda t: len(t.get("name", "")))  # the plainest name, e.g. not "bulk_..."


def _is_people_search(name: str) -> bool:
    return "people" in name and "search" in name


def _is_people_match(name: str) -> bool:
    return "people" in name and ("match" in name or "enrich" in name) and "bulk" not in name


def _set_if_declared(args: dict, props: dict, name: str, value: object) -> None:
    if name in props:
        args[name] = value


def _domain_args(props: dict, domain: str) -> dict:
    param = next((p for p in DOMAIN_PARAMS if p in props), "domain")
    return {param: [domain] if props.get(param, {}).get("type") == "array" else domain}


def _hunter_args(tool: dict, domain: str) -> dict:
    props = _properties(tool)
    args = {"domain": domain}
    _set_if_declared(args, props, "limit", HUNTER_LIMIT)
    _set_if_declared(args, props, "type", "personal")
    _set_if_declared(args, props, "department", hunter.DEPARTMENTS)
    return args


def _apollo_search_args(tool: dict, domain: str) -> dict:
    props = _properties(tool)
    args = _domain_args(props, domain)
    _set_if_declared(args, props, "person_titles", list(PITCH_TITLES))
    _set_if_declared(args, props, "include_similar_titles", True)
    _set_if_declared(args, props, "per_page", APOLLO_PER_PAGE)
    return args


def _with_session(integration: Integration, work: Callable[[McpSession, list[dict]], object]) -> object:
    """Open a session with a fresh token; on a refused token, refresh once and retry."""
    url = oauth.SERVERS[integration.provider].mcp_url
    for force_refresh in (False, True):
        session = McpSession(url, oauth.access_token(integration, force_refresh=force_refresh))
        try:
            session.initialize()
            return work(session, session.list_tools())
        except McpAuthError:
            if force_refresh:
                raise
    raise FinderError("unreachable")  # pragma: no cover - the loop always returns or raises


def find(integration: Integration, domain: str) -> list[Candidate]:
    if integration.provider == hunter.PROVIDER:

        def hunter_search(session: McpSession, tools: list[dict]) -> list[Candidate]:
            tool = _pick(tools, lambda n: "domain" in n and "search" in n, "domain search", "Hunter")
            return hunter.candidates_from(session.call_tool(tool["name"], _hunter_args(tool, domain)))

        return _with_session(integration, hunter_search)

    def apollo_search(session: McpSession, tools: list[dict]) -> list[Candidate]:
        tool = _pick(tools, _is_people_search, "people search", "Apollo")
        return apollo.candidates_from(session.call_tool(tool["name"], _apollo_search_args(tool, domain)))

    return _with_session(integration, apollo_search)


def reveal(integration: Integration, person_id: str) -> str:
    """One person's work email via Apollo's people match (1 Apollo credit)."""

    def match(session: McpSession, tools: list[dict]) -> str:
        tool = _pick(tools, _is_people_match, "people match", "Apollo")
        props = _properties(tool)
        param = next((p for p in PERSON_ID_PARAMS if p in props), "id")
        return apollo.email_from(session.call_tool(tool["name"], {param: person_id}))

    email = _with_session(integration, match)
    if not email:
        raise FinderError("Apollo has no work email for this person.")
    return email
