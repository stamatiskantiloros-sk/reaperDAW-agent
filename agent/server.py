"""Dependency-free MCP server over stdio (newline-delimited JSON-RPC 2.0).

stdout carries protocol messages only; all logging goes to stderr.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, TextIO

from agent.core import store as default_store_api
from agent.tools import plans, project
from agent.tools.registry import Context, Tool, ToolError, check_arguments

SERVER_NAME = "reaper-agent"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2025-06-18"
PROFILES = ("core", "full")
ALL_TOOLS: list[Tool] = [*project.TOOLS, *plans.TOOLS]


def _log(text: str) -> None:
    print(f"[reaper-agent] {text}", file=sys.stderr, flush=True)


def _result(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


class Server:
    def __init__(self, ctx: Context, tools: list[Tool], profile: str) -> None:
        self.ctx = ctx
        self.profile = profile
        self.tools = {t.name: t for t in tools}

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        """One parsed JSON-RPC message -> response dict, or None for notifications."""
        if not isinstance(msg, dict):
            return _error(None, -32600, "invalid request")
        if "id" not in msg:  # notification: never answered, never executed
            return None
        mid = msg["id"]
        method = msg.get("method")
        if not isinstance(method, str):
            return _error(mid, -32600, "invalid request: method must be a string")
        params = msg.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(mid, -32602, "params must be an object")
        params = params or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
            return _result(
                mid,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": "0.0.0"},
                },
            )
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": [t.listing() for t in self.tools.values()]})
        if method == "tools/call":
            return self._call(mid, params)
        return _error(mid, -32601, f"method not found: {method!r}")

    def _call(self, mid: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            return _error(mid, -32602, "params.name must be a string")
        tool = self.tools.get(name)
        if tool is None:
            return _error(mid, -32602, f"unknown tool: {name!r}")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(mid, -32602, "params.arguments must be an object")
        try:
            check_arguments(tool, args)
            return _result(mid, _tool_result(tool.handler(self.ctx, args)))
        except ToolError as exc:
            return _result(mid, _tool_result(str(exc), is_error=True))
        except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the server
            _log(f"tool {name} crashed: {exc!r}")
            return _result(mid, _tool_result(f"internal error in {name}: {exc}", is_error=True))

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                response: dict[str, Any] | None = _error(None, -32700, "parse error")
            else:
                response = self.handle_message(msg)
            if response is not None:
                stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                stdout.flush()


def build_server(
    bridge: Any, store: Any, profile: str = "core", *, store_api: Any = None
) -> Server:
    """Create a server. `store_api` provides build_plan / apply_stored_plan / undo_last and
    defaults to agent.core.store; tests inject a stub."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {PROFILES}")
    tools = [t for t in ALL_TOOLS if profile in t.profiles]
    ctx = Context(bridge=bridge, store=store, store_api=store_api or default_store_api)
    return Server(ctx, tools, profile)


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except AttributeError:
            pass
    profile = os.environ.get("REAPER_AGENT_PROFILE", "core").strip() or "core"
    if profile not in PROFILES:
        _log(f"REAPER_AGENT_PROFILE must be one of {PROFILES}, got {profile!r}")
        return 2
    try:
        from agent.bridge_client.daemon import DaemonBridge
    except ImportError:
        _log("the real bridge (agent.bridge_client.daemon.DaemonBridge) is not available yet")
        return 2
    bridge = DaemonBridge()
    server = build_server(bridge, default_store_api.PlanStore(), profile)
    _log(f"serving profile {profile} with {len(server.tools)} tools")
    server.serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
