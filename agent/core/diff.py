"""Snapshot diff engine: field-level `Change`s between two snapshots. `agent_ext` is ignored.

Values in a Change are JSON-safe (dataclasses become dicts) with floats rounded to the same six
decimals the snapshot hash uses, so tiny float noise is not reported as a change.
"""

from __future__ import annotations

import json
from typing import Any

from agent.core import serde
from agent.core.hashing import FLOAT_DIGITS
from agent.core.models import Change, Snapshot

_KIND_RANK = {"track": 0, "item": 1, "take": 2, "marker": 3, "project": 4, "tempo": 5}

TRACK_FIELDS = (
    "name",
    "color",
    "folder_depth",
    "parent",
    "lane_mode",
    "lane_names",
    "automation_mode",
    "master_send",
    "sends",
    "fx",
    "envelopes",
)
ITEM_FIELDS = ("track", "position", "length", "lane")
TAKE_FIELDS = ("name", "active", "offline", "source")
MARKER_FIELDS = ("name", "position", "end", "color")
PROJECT_FIELDS = ("name", "path")


def _norm(value: Any) -> Any:
    value = serde.to_dict(value)
    if isinstance(value, float):
        return round(value, FLOAT_DIGITS) + 0.0
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    return value


def sort_changes(changes: list[Change]) -> list[Change]:
    return sorted(changes, key=lambda c: (_KIND_RANK.get(c.kind, 99), c.guid, c.field))


def _fields(
    kind: str, guid: str, a: Any, b: Any, names: tuple[str, ...], out: list[Change]
) -> None:
    for name in names:
        before, after = _norm(getattr(a, name)), _norm(getattr(b, name))
        if before != after:
            out.append(Change(kind, guid, name, before, after))


def _takes(snap: Snapshot) -> dict[str, Any]:
    return {tk.guid: tk for it in snap.items for tk in it.takes}


def _objects(
    kind: str,
    before: dict[str, Any],
    after: dict[str, Any],
    names: tuple[str, ...],
    label: Any,
    out: list[Change],
) -> None:
    """Added / removed / per-field changes for one kind of GUID-identified object."""
    for guid, obj in after.items():
        if guid not in before:
            out.append(Change(kind, guid, "added", None, label(obj)))
    for guid, obj in before.items():
        if guid not in after:
            out.append(Change(kind, guid, "removed", label(obj), None))
        else:
            _fields(kind, guid, obj, after[guid], names, out)


def diff(before: Snapshot, after: Snapshot) -> list[Change]:
    out: list[Change] = []
    b_tracks = {t.guid: t for t in before.tracks}
    a_tracks = {t.guid: t for t in after.tracks}
    _objects("track", b_tracks, a_tracks, TRACK_FIELDS, lambda t: t.name, out)
    survivors_before = [t.guid for t in before.tracks if t.guid in a_tracks]
    survivors_after = [t.guid for t in after.tracks if t.guid in b_tracks]
    if survivors_before != survivors_after:
        out.append(Change("track", "", "order", survivors_before, survivors_after))

    _objects(
        "item",
        {i.guid: i for i in before.items},
        {i.guid: i for i in after.items},
        ITEM_FIELDS,
        lambda i: i.track,
        out,
    )
    _objects("take", _takes(before), _takes(after), TAKE_FIELDS, lambda t: t.name, out)
    _objects(
        "marker",
        {m.guid: m for m in before.markers},
        {m.guid: m for m in after.markers},
        MARKER_FIELDS,
        lambda m: m.name,
        out,
    )
    _fields("project", "", before.project, after.project, PROJECT_FIELDS, out)
    b_tempo, a_tempo = _norm(before.tempo_map), _norm(after.tempo_map)
    if b_tempo != a_tempo:
        out.append(Change("tempo", "", "tempo_map", b_tempo, a_tempo))
    return sort_changes(out)


def _fmt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def format_changes(changes: list[Change]) -> str:
    """One readable line per change."""
    lines = []
    for c in changes:
        who = f"{c.kind} {c.guid}" if c.guid else c.kind
        if c.field == "added":
            lines.append(f"{who}: added {_fmt(c.after)}")
        elif c.field == "removed":
            lines.append(f"{who}: removed {_fmt(c.before)}")
        else:
            lines.append(f"{who}: {c.field} {_fmt(c.before)} -> {_fmt(c.after)}")
    return "\n".join(lines)
