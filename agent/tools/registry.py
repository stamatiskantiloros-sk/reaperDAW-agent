"""Tool plumbing shared by the MCP server and the tool modules (stdlib only)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.bridge_client.protocol import Bridge
from agent.core.models import is_guid

MAX_DESCRIPTION = 200


class ToolError(Exception):
    """A tool failure. Becomes a result with isError true and this message as its text."""


@dataclass
class Context:
    """What a tool handler may use. `store_api` is the module (or stub) that provides
    build_plan / apply_stored_plan / undo_last, injected so tests need no real store."""

    bridge: Bridge
    store: Any
    store_api: Any


@dataclass
class Tool:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: list[str]
    handler: Callable[[Context, dict[str, Any]], str]
    profiles: tuple[str, ...] = ("core", "full")
    guid_args: tuple[str, ...] = ()  # arguments that must be GUIDs, never indices
    read_only: bool = False

    def __post_init__(self) -> None:
        if len(self.description) >= MAX_DESCRIPTION:
            raise ValueError(f"description of {self.name} must be under {MAX_DESCRIPTION} chars")

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": self.required,
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": self.read_only},
        }


_TYPES: dict[str, type] = {"string": str, "integer": int, "boolean": bool}


def check_arguments(tool: Tool, args: dict[str, Any]) -> None:
    """Validate tool arguments; raise ToolError with a short, actionable message."""
    for name in tool.guid_args:
        value = args.get(name)
        if value is None:
            continue
        if (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, str) and value.strip().isdigit()
        ):
            raise ToolError(
                f"{name}: indices are not accepted. Pass the GUID "
                "(get_snapshot section=tracks lists them)."
            )
        if not is_guid(value):
            raise ToolError(f"{name}: expected a GUID like {{XXXXXXXX-XXXX-...}}, got {value!r}.")
    for name in tool.required:
        if name not in args:
            raise ToolError(f"missing required argument {name!r}.")
    for name, value in args.items():
        spec = tool.properties.get(name)
        if spec is None:
            raise ToolError(f"unknown argument {name!r}.")
        want = spec["type"]
        ok = isinstance(value, _TYPES[want])
        if want == "integer" and isinstance(value, bool):
            ok = False
        if not ok:
            raise ToolError(f"{name} must be of type {want}.")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{name} must be one of {list(spec['enum'])}.")
        if "minimum" in spec and value < spec["minimum"]:
            raise ToolError(f"{name} must be >= {spec['minimum']}.")
        if "maximum" in spec and value > spec["maximum"]:
            raise ToolError(f"{name} must be <= {spec['maximum']}.")
