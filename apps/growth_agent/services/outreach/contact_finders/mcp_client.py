"""A minimal MCP client over Streamable HTTP: initialize, list tools, call a tool.

Spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- Every message is a JSON-RPC POST to the server URL.
- The server answers with JSON, or with a short ``text/event-stream`` whose
  ``data:`` lines carry the JSON-RPC response.
- After ``initialize`` the server may hand out an ``Mcp-Session-Id`` header
  that must accompany every later request.

Tool output is untrusted provider data; callers parse it defensively.
"""

from __future__ import annotations

import json
import logging

import requests

from .base import TIMEOUT_SECONDS, FinderError

logger = logging.getLogger("apps")
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "signalor.ai", "version": "1.0"}
SESSION_HEADER = "Mcp-Session-Id"


class McpAuthError(FinderError):
    """The server refused the access token (HTTP 401): refresh it and retry once."""


def _events(text: str) -> list[dict]:
    """JSON-RPC messages from an SSE body (``data:`` lines, blank-line separated)."""
    messages, buffer = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            buffer.append(line[len("data:") :].strip())
        elif not line.strip() and buffer:
            try:
                messages.append(json.loads("".join(buffer)))
            except ValueError:
                logger.warning("mcp: skipped a non-JSON event")
            buffer = []
    return messages


class McpSession:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.session_id = ""
        self._next_id = 0

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return headers

    def _send(self, message: dict) -> requests.Response:
        try:
            reply = requests.post(self.url, json=message, headers=self._headers(), timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise FinderError(f"MCP server couldn't be reached: {type(exc).__name__}") from exc
        if reply.status_code == 401:
            raise McpAuthError("MCP server refused the sign-in token")
        if not reply.ok:
            raise FinderError(f"MCP server answered HTTP {reply.status_code}")
        return reply

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        reply = self._send(message)
        self.session_id = reply.headers.get(SESSION_HEADER, self.session_id)
        if "text/event-stream" in reply.headers.get("Content-Type", ""):
            candidates = _events(reply.text)
        else:
            candidates = [reply.json()]
        response = next((m for m in candidates if m.get("id") == self._next_id), None)
        if response is None:
            raise FinderError(f"MCP server sent no answer to {method}")
        if "error" in response:
            raise FinderError(f"MCP {method} failed: {response['error'].get('message', 'error')}")
        return response.get("result") or {}

    def initialize(self) -> None:
        params = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}
        self._request("initialize", params)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        return self._request("tools/list", {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict) -> object:
        """The tool's output: ``structuredContent`` if given, else its text parsed as JSON."""
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise FinderError(f"MCP tool {name} failed: {tool_text(result)[:200]}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        text = tool_text(result)
        try:
            return json.loads(text)
        except ValueError:
            return text


def tool_text(result: dict) -> str:
    parts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    return "\n".join(parts)
