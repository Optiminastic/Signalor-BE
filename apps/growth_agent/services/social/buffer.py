"""The brand's own Buffer account: its channels, and adding approved posts to its queue.

Signed in through Buffer's hosted MCP server with OAuth (``contact_finders/oauth.py``,
provider ``buffer``). Buffer doesn't publish its MCP tool names, so each call reads
the tool list and picks a tool by name, filling only the arguments its input
schema declares; when nothing fits, the error names what the server offers.

Posts are added to the channel's Buffer queue (Buffer publishes them at the
channel's next slot), never published by Signalor directly.
"""

from __future__ import annotations

from apps.integrations.models import Integration

from ..outreach.contact_finders import mcp_finders
from ..outreach.contact_finders.base import FinderError
from ..outreach.contact_finders.mcp_client import McpSession

PROVIDER = "buffer"
# Buffer's service names for the platforms the agent posts to.
SERVICES = {"linkedin": ("linkedin",), "x": ("twitter", "x"), "instagram": ("instagram",)}
TEXT_PARAMS = ("text", "content", "body", "message")
CHANNEL_PARAMS = (
    "channelId",
    "channel_id",
    "channelIds",
    "channel_ids",
    "profileId",
    "profile_ids",
    "profileIds",
)
FOLLOWER_KEYS = ("followers", "followerCount", "followers_count", "follower_count")


class BufferError(FinderError):
    """Buffer couldn't do what was asked; the message is safe to show the owner."""


def connection(organization) -> Integration | None:
    """The brand's signed-in Buffer account, if any."""
    return (
        Integration.objects.filter(organization=organization, provider=PROVIDER, is_active=True)
        .exclude(access_token_encrypted="")
        .first()
    )


def _pick(tools: list[dict], words: tuple[str, ...], purpose: str) -> dict:
    found = [t for t in tools if all(w in (t.get("name") or "").lower() for w in words)]
    if not found:
        offered = ", ".join(sorted(t.get("name", "?") for t in tools)) or "none"
        raise BufferError(f"Buffer's connector offers no {purpose} tool (offered: {offered})")
    return min(found, key=lambda t: len(t.get("name", "")))


def _properties(tool: dict) -> dict:
    return (tool.get("inputSchema") or {}).get("properties") or {}


def _records(value: object) -> list[dict]:
    """The list of channel records inside a tool answer, wherever it sits."""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for key in ("channels", "data", "items", "results", "profiles"):
            if key in value:
                return _records(value[key])
    return []


def channel_summary(record: dict) -> dict:
    service = str(record.get("service") or record.get("type") or record.get("platform") or "").lower()
    followers = next((record.get(k) for k in FOLLOWER_KEYS if isinstance(record.get(k), int)), None)
    return {
        "id": str(record.get("id") or record.get("_id") or ""),
        "service": service,
        "name": str(record.get("name") or record.get("displayName") or record.get("handle") or ""),
        "followers": followers,
    }


def channels(integration: Integration) -> list[dict]:
    """The account's channels: id, service (linkedin, twitter, instagram...), name, followers if given."""

    def work(session: McpSession, tools: list[dict]) -> list[dict]:
        tool = _pick(tools, ("channel",), "channel list")
        records = _records(session.call_tool(tool["name"], {}))
        return [c for c in (channel_summary(r) for r in records) if c["id"]]

    return mcp_finders.with_session(integration, work)


def channel_for(saved: list[dict], platform: str) -> dict | None:
    services = SERVICES.get(platform, ())
    return next((c for c in saved if c.get("service") in services), None)


def _post_args(tool: dict, channel_id: str, text: str) -> dict:
    props = _properties(tool)
    text_param = next((p for p in TEXT_PARAMS if p in props), "text")
    channel_param = next((p for p in CHANNEL_PARAMS if p in props), "channelId")
    is_list = props.get(channel_param, {}).get("type") == "array"
    args = {text_param: text, channel_param: [channel_id] if is_list else channel_id}
    mode = props.get("mode") or props.get("schedulingType") or {}
    queue = next((v for v in mode.get("enum") or [] if "queue" in str(v).lower()), None)
    if queue is not None:
        args["mode" if "mode" in props else "schedulingType"] = queue
    return args


def add_to_queue(integration: Integration, channel_id: str, text: str) -> dict:
    """Add one post to the channel's Buffer queue. Returns Buffer's answer (post id when given)."""

    def work(session: McpSession, tools: list[dict]) -> dict:
        tool = _pick(tools, ("create", "post"), "create post")
        answer = session.call_tool(tool["name"], _post_args(tool, channel_id, text))
        return answer if isinstance(answer, dict) else {"answer": str(answer)[:500]}

    return mcp_finders.with_session(integration, work)


def save_channels(organization) -> list[dict]:
    """Read the account's channels and keep them on the connection. Never raises: a failure
    leaves the last saved list, and the next weekly check tries again."""
    import logging

    integration = connection(organization)
    if integration is None:
        return []
    try:
        found = channels(integration)
    except FinderError as exc:
        logging.getLogger("apps").warning("buffer channels could not be read: %s", exc)
        return integration.metadata.get("channels") or []
    integration.metadata = {**integration.metadata, "channels": found}
    integration.save(update_fields=["metadata", "updated_at"])
    return found


def saved_channels(organization) -> list[dict]:
    integration = connection(organization)
    return (integration.metadata.get("channels") or []) if integration else []
