"""Snapshot hash rule. FROZEN CONTRACT.

hash = first 32 hex chars of sha256 over a canonical JSON form of the snapshot:
- `agent_ext` is dropped from the project, tracks, items and takes (our own bookkeeping must not make a plan stale),
- items and markers are sorted by GUID (their order carries no meaning),
- track order is kept (it is project content),
- keys sorted, no whitespace, floats rounded to 6 decimals so round trips through Lua are stable.

Volatile state (selection, cursor, transport, meters) is not in the model at all.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent.core.models import Snapshot

FLOAT_DIGITS = 6


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, FLOAT_DIGITS) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(value, list):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return value


def canonical(snapshot: Snapshot) -> dict[str, Any]:
    data = snapshot.to_dict()
    for track in data["tracks"]:
        track.pop("agent_ext", None)
    data["project"].pop("agent_ext", None)
    for item in data["items"]:
        item.pop("agent_ext", None)
        for take in item["takes"]:
            take.pop("agent_ext", None)
    data["items"].sort(key=lambda i: i["guid"])
    data["markers"].sort(key=lambda m: m["guid"])
    return _round(data)


def snapshot_hash(snapshot: Snapshot) -> str:
    text = json.dumps(canonical(snapshot), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
