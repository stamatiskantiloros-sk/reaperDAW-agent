"""Snapshot data model. FROZEN CONTRACT (schema_version 1).

Rules baked into the model:
- Objects are identified by GUID only. There are no track/item/take indices anywhere; list order
  is the project order (tracks) and carries no identity.
- Colours are "#RRGGBB" strings (or None for the default colour), never OS-native integers.
- Times are seconds (floats). Volatile state (selection, cursor, transport, meters) is NOT part
  of the model, so a snapshot hash only changes when the project's content changes.
- `agent_ext` holds our own `reaper_agent:` ext-state keys (originals, family overrides). It is
  excluded from the snapshot hash (see hashing.py).

Changing a field here is a contract change: bump SCHEMA_VERSION and update the Lua `agent.snapshot`
handler, the fake bridge and the tests together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.core import serde

SCHEMA_VERSION = 1

GUID_RE = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

LANE_MODES = ("normal", "free", "fixed_lanes")
AUTOMATION_MODES = ("trim_read", "read", "touch", "write", "latch", "latch_preview")
SECTIONS = ("project", "tempo", "tracks", "items", "markers")


def is_guid(value: Any) -> bool:
    return isinstance(value, str) and GUID_RE.match(value) is not None


@dataclass
class ProjectInfo:
    name: str
    path: str  # full path of the .rpp, "" if the project was never saved
    sample_rate: int
    reaper_version: str
    length: float
    agent_ext: dict[str, str] = field(default_factory=dict)  # project ext state, reaper_agent only


@dataclass
class TempoPoint:
    time: float
    bpm: float
    num: int = 4
    den: int = 4
    linear: bool = False


@dataclass
class Send:
    dest: str  # destination track GUID
    volume_db: float = 0.0
    pre_fader: bool = False


@dataclass
class FxInfo:
    guid: str
    name: str  # as REAPER names it, e.g. "VST: ReaEQ (Cockos)"
    enabled: bool = True
    offline: bool = False
    param_count: int = 0


@dataclass
class EnvelopeInfo:
    kind: str  # "track" (Volume, Pan, ...) or "fx"
    name: str
    fx: str | None = None  # FX GUID when kind == "fx"
    param: int | None = None  # parameter index within that FX (params have no GUID)
    active: bool = True
    visible: bool = False
    point_count: int = 0


@dataclass
class Track:
    guid: str
    name: str
    color: str | None = None
    folder_depth: int = 0  # REAPER I_FOLDERDEPTH: 1 opens a folder, 0 normal, -n closes n levels
    parent: str | None = None  # parent folder track GUID
    lane_mode: str = "normal"  # one of LANE_MODES
    lane_names: list[str] = field(default_factory=list)
    automation_mode: str = "trim_read"  # one of AUTOMATION_MODES
    master_send: bool = True
    sends: list[Send] = field(default_factory=list)
    fx: list[FxInfo] = field(default_factory=list)
    envelopes: list[EnvelopeInfo] = field(default_factory=list)
    agent_ext: dict[str, str] = field(default_factory=dict)


@dataclass
class Take:
    guid: str
    name: str
    source: str = ""  # source file path, "" for empty or MIDI-in-project
    offline: bool = False
    active: bool = False
    agent_ext: dict[str, str] = field(default_factory=dict)


@dataclass
class Item:
    guid: str
    track: str  # owning track GUID
    position: float
    length: float
    lane: int = 0  # fixed-lane index (0 when the track is not in fixed-lane mode)
    takes: list[Take] = field(default_factory=list)
    agent_ext: dict[str, str] = field(default_factory=dict)


@dataclass
class Marker:
    guid: str
    number: int  # REAPER's display number (not an identity)
    name: str
    position: float
    end: float | None = None  # set for regions, None for plain markers
    color: str | None = None

    @property
    def is_region(self) -> bool:
        return self.end is not None


@dataclass
class Snapshot:
    project: ProjectInfo
    tempo_map: list[TempoPoint] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return serde.to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported snapshot schema_version {data.get('schema_version')!r}, "
                f"expected {SCHEMA_VERSION}"
            )
        return serde.from_dict(cls, data)

    def section(self, name: str) -> Any:
        """Return one section as plain data (for the get_snapshot tool)."""
        if name not in SECTIONS:
            raise ValueError(f"unknown section {name!r}; choose from {SECTIONS}")
        key = {"tempo": "tempo_map"}.get(name, name)
        return (
            serde.to_dict(getattr(self, key)) if key != "project" else serde.to_dict(self.project)
        )

    def track(self, guid: str) -> Track | None:
        return next((t for t in self.tracks if t.guid == guid), None)

    def item(self, guid: str) -> Item | None:
        return next((i for i in self.items if i.guid == guid), None)

    def marker(self, guid: str) -> Marker | None:
        return next((m for m in self.markers if m.guid == guid), None)


@dataclass
class Change:
    """One field-level difference between two snapshots (output of the diff engine)."""

    kind: str  # "track" | "item" | "take" | "fx" | "marker" | "project" | "tempo"
    guid: str  # object GUID, "" for project-level changes
    field: str  # e.g. "name", "color", "added", "removed"
    before: Any = None
    after: Any = None
