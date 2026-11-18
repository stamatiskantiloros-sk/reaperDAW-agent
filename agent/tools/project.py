"""Read-only project tools: overview and paged snapshot."""

from __future__ import annotations

import json
from typing import Any

from agent.bridge_client.protocol import BridgeError
from agent.core.hashing import snapshot_hash
from agent.core.models import SECTIONS
from agent.tools.registry import Context, Tool, ToolError

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
PAGED = ("tracks", "items")


def get_project_overview(ctx: Context, args: dict[str, Any]) -> str:
    try:
        status = ctx.bridge.status()
        snap = ctx.bridge.snapshot()
    except BridgeError as exc:
        raise ToolError(f"bridge error: {exc}") from exc
    p = snap.project
    regions = sum(1 for m in snap.markers if m.is_region)
    lines = [
        f"Project: {p.name or '(unsaved)'}" + (f" ({p.path})" if p.path else ""),
        f"REAPER {p.reaper_version}, {p.sample_rate} Hz, length {p.length:.1f} s",
        (
            f"Tracks: {len(snap.tracks)}  Items: {len(snap.items)}  "
            f"Takes: {sum(len(i.takes) for i in snap.items)}"
        ),
        (
            f"Regions: {regions}  Markers: {len(snap.markers) - regions}  "
            f"Tempo points: {len(snap.tempo_map)}"
        ),
        f"Snapshot hash: {snapshot_hash(snap)}",
        f"Bridge: {json.dumps(status, sort_keys=True)}",
    ]
    return "\n".join(lines)


def get_snapshot(ctx: Context, args: dict[str, Any]) -> str:
    section = args["section"]
    limit = args.get("limit", DEFAULT_LIMIT)
    offset = args.get("offset", 0)
    try:
        snap = ctx.bridge.snapshot()
        data = snap.section(section)
    except BridgeError as exc:
        raise ToolError(f"bridge error: {exc}") from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    out: dict[str, Any] = {"section": section, "snapshot_hash": snapshot_hash(snap)}
    if isinstance(data, list):
        out["total"] = len(data)
        if section in PAGED:
            out.update(offset=offset, limit=limit, data=data[offset : offset + limit])
            if offset + limit < len(data):
                out["next_offset"] = offset + limit
        else:
            out["data"] = data
    else:
        out["data"] = data
    return json.dumps(out, separators=(",", ":"))


TOOLS = [
    Tool(
        name="get_project_overview",
        description="Project name, counts of tracks/items/regions, snapshot hash and bridge status. Start here.",
        properties={},
        required=[],
        handler=get_project_overview,
        read_only=True,
    ),
    Tool(
        name="get_snapshot",
        description="Read one snapshot section as JSON. Tracks and items are paged (default limit 100); use next_offset to continue.",
        properties={
            "section": {"type": "string", "enum": list(SECTIONS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            "offset": {"type": "integer", "minimum": 0},
        },
        required=["section"],
        handler=get_snapshot,
        read_only=True,
    ),
]
