"""Plan operations (v1 vocabulary). FROZEN CONTRACT.

Every op addresses objects by GUID, or by `ref:<alias>` for an object created earlier in the same
plan (see InsertTrack.ref / AddRegion.ref). FX parameters have no GUID: they are addressed by the
FX instance GUID plus the parameter index inside that FX.

Ops are plain data. They validate themselves (`validate`) but never touch REAPER; the Lua
`agent.apply_plan` handler and the fake bridge each implement the same semantics.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from agent.core import serde
from agent.core.models import AUTOMATION_MODES, COLOR_RE, LANE_MODES, is_guid

REF_RE = re.compile(r"^ref:[A-Za-z][A-Za-z0-9_]{0,31}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ENV_SHAPES = ("linear", "square", "slow", "fast_start", "fast_end", "bezier")
EXT_SCOPES = ("track", "item", "take", "project")
MAX_NAME = 255


class OpError(ValueError):
    """Raised when an op is malformed."""


def is_target(value: Any) -> bool:
    return is_guid(value) or (isinstance(value, str) and REF_RE.match(value) is not None)


def _name(value: Any, what: str = "name") -> None:
    if not isinstance(value, str) or len(value) > MAX_NAME or "\x00" in value:
        raise OpError(f"{what} must be a string of at most {MAX_NAME} characters, no NUL")


def _color(value: Any) -> None:
    if value is not None and not (isinstance(value, str) and COLOR_RE.match(value)):
        raise OpError(f"color must be '#RRGGBB' or null, got {value!r}")


def _time(value: Any, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OpError(f"{what} must be a finite number")
    if value < 0:
        raise OpError(f"{what} must be >= 0")


def _int(value: Any, what: str, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise OpError(f"{what} must be an integer in [{lo}, {hi}], got {value!r}")


OPS: dict[str, type[Op]] = {}


def op(cls: type[Op]) -> type[Op]:
    """Class decorator: register an op under its `type` name."""
    if not cls.type or cls.type in OPS:
        raise RuntimeError(f"bad or duplicate op type {cls.type!r}")
    OPS[cls.type] = cls
    return cls


@dataclass
class Op:
    type: ClassVar[str] = ""
    # Field names holding an object id (GUID or ref:alias); checked by the base validate().
    targets: ClassVar[tuple[str, ...]] = ()
    # Field naming a new plan-local alias this op defines, if any.
    defines: ClassVar[str | None] = None
    # True if this op may move, hide or overwrite user content and so needs an original saved.
    saves_original: ClassVar[bool] = False

    def validate(self) -> None:
        for name in self.targets:
            value = getattr(self, name)
            if value is None and name in self.optional_targets():
                continue
            if not is_target(value):
                raise OpError(f"{self.type}.{name}: expected a GUID or ref:alias, got {value!r}")
        ref = self.defined_ref()
        if ref is not None and not REF_RE.match(ref):
            raise OpError(f"{self.type}.{self.defines}: bad alias {ref!r}")
        self.check()

    def optional_targets(self) -> tuple[str, ...]:
        return ()

    def check(self) -> None:  # subclasses add field checks
        pass

    def used_refs(self) -> list[str]:
        out = []
        for name in self.targets:
            value = getattr(self, name)
            if isinstance(value, str) and value.startswith("ref:"):
                out.append(value)
        return out

    def defined_ref(self) -> str | None:
        if self.defines is None:
            return None
        alias = getattr(self, self.defines)
        return f"ref:{alias}" if alias else None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **serde.to_dict(self)}


# --- track ops -------------------------------------------------------------------------------


@op
@dataclass
class SetTrackName(Op):
    type: ClassVar[str] = "set_track_name"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    saves_original: ClassVar[bool] = True
    track: str
    name: str

    def check(self) -> None:
        _name(self.name)


@op
@dataclass
class SetTrackColor(Op):
    type: ClassVar[str] = "set_track_color"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    saves_original: ClassVar[bool] = True
    track: str
    color: str | None

    def check(self) -> None:
        _color(self.color)


@op
@dataclass
class SetFolderDepth(Op):
    type: ClassVar[str] = "set_folder_depth"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    saves_original: ClassVar[bool] = True
    track: str
    depth: int  # raw REAPER value: 1 opens a folder, 0 normal, -n closes n levels

    def check(self) -> None:
        _int(self.depth, "depth", -16, 1)


@op
@dataclass
class MoveTrack(Op):
    type: ClassVar[str] = "move_track"
    targets: ClassVar[tuple[str, ...]] = ("track", "before")
    saves_original: ClassVar[bool] = True
    track: str
    before: str | None = None  # insert immediately before this track; None = move to the end

    def optional_targets(self) -> tuple[str, ...]:
        return ("before",)


@op
@dataclass
class InsertTrack(Op):
    type: ClassVar[str] = "insert_track"
    targets: ClassVar[tuple[str, ...]] = ("before",)
    defines: ClassVar[str | None] = "ref"
    name: str
    ref: str  # alias for later ops in this plan, written without the "ref:" prefix
    before: str | None = None  # None = append at the end
    color: str | None = None

    def optional_targets(self) -> tuple[str, ...]:
        return ("before",)

    def check(self) -> None:
        _name(self.name)
        _color(self.color)


@op
@dataclass
class SetTrackAutomationMode(Op):
    type: ClassVar[str] = "set_track_automation_mode"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    saves_original: ClassVar[bool] = True
    track: str
    mode: str

    def check(self) -> None:
        if self.mode not in AUTOMATION_MODES:
            raise OpError(f"mode must be one of {AUTOMATION_MODES}, got {self.mode!r}")


# --- take / lane ops -------------------------------------------------------------------------


@op
@dataclass
class SetTakeName(Op):
    type: ClassVar[str] = "set_take_name"
    targets: ClassVar[tuple[str, ...]] = ("take",)
    saves_original: ClassVar[bool] = True
    take: str
    name: str

    def check(self) -> None:
        _name(self.name)


@op
@dataclass
class SetTrackLaneMode(Op):
    type: ClassVar[str] = "set_track_lane_mode"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    saves_original: ClassVar[bool] = True
    track: str
    mode: str

    def check(self) -> None:
        if self.mode not in LANE_MODES:
            raise OpError(f"mode must be one of {LANE_MODES}, got {self.mode!r}")


@op
@dataclass
class SetItemLane(Op):
    type: ClassVar[str] = "set_item_lane"
    targets: ClassVar[tuple[str, ...]] = ("item",)
    saves_original: ClassVar[bool] = True
    item: str
    lane: int

    def check(self) -> None:
        _int(self.lane, "lane", 0, 63)


@op
@dataclass
class SetLaneName(Op):
    type: ClassVar[str] = "set_lane_name"
    targets: ClassVar[tuple[str, ...]] = ("track",)
    track: str
    lane: int
    name: str

    def check(self) -> None:
        _int(self.lane, "lane", 0, 63)
        _name(self.name)


# --- region / ext-state ops ------------------------------------------------------------------


@op
@dataclass
class AddRegion(Op):
    type: ClassVar[str] = "add_region"
    defines: ClassVar[str | None] = "ref"
    name: str
    start: float
    end: float
    ref: str
    color: str | None = None

    def check(self) -> None:
        _name(self.name)
        _time(self.start, "start")
        _time(self.end, "end")
        if self.end <= self.start:
            raise OpError("end must be greater than start")
        _color(self.color)


@op
@dataclass
class SetRegion(Op):
    type: ClassVar[str] = "set_region"
    targets: ClassVar[tuple[str, ...]] = ("region",)
    saves_original: ClassVar[bool] = True
    region: str
    name: str | None = None
    start: float | None = None
    end: float | None = None
    color: str | None = None

    def check(self) -> None:
        if self.name is not None:
            _name(self.name)
        if self.start is not None:
            _time(self.start, "start")
        if self.end is not None:
            _time(self.end, "end")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise OpError("end must be greater than start")
        _color(self.color)
        if all(v is None for v in (self.name, self.start, self.end, self.color)):
            raise OpError("set_region changes nothing")


@op
@dataclass
class SetExtState(Op):
    """Write one key under the `reaper_agent` namespace (track/item/take ext state, or project)."""

    type: ClassVar[str] = "set_ext_state"
    scope: str  # one of EXT_SCOPES
    key: str
    value: str
    target: str | None = None  # object GUID/ref; required unless scope == "project"

    def validate(self) -> None:
        if self.scope not in EXT_SCOPES:
            raise OpError(f"scope must be one of {EXT_SCOPES}, got {self.scope!r}")
        if self.scope == "project":
            if self.target is not None:
                raise OpError("project scope takes no target")
        elif not is_target(self.target):
            raise OpError(
                f"set_ext_state.target: expected a GUID or ref:alias, got {self.target!r}"
            )
        if not KEY_RE.match(self.key or ""):
            raise OpError(f"key must match {KEY_RE.pattern}, got {self.key!r}")
        if not isinstance(self.value, str) or len(self.value) > 4096 or "\x00" in self.value:
            raise OpError("value must be a string of at most 4096 characters, no NUL")

    def used_refs(self) -> list[str]:
        t = self.target
        return [t] if isinstance(t, str) and t.startswith("ref:") else []


# --- FX and envelope ops ---------------------------------------------------------------------


@op
@dataclass
class SetFxParam(Op):
    type: ClassVar[str] = "set_fx_param"
    targets: ClassVar[tuple[str, ...]] = ("track", "fx")
    saves_original: ClassVar[bool] = True
    track: str
    fx: str  # FX instance GUID
    param: int  # parameter index inside that FX
    normalized: float  # 0..1; real units are converted before the plan is built

    def check(self) -> None:
        _int(self.param, "param", 0, 100000)
        if (
            isinstance(self.normalized, bool)
            or not isinstance(self.normalized, (int, float))
            or not 0.0 <= self.normalized <= 1.0
        ):
            raise OpError("normalized must be a number in [0, 1]")


@op
@dataclass
class EnsureFxEnvelope(Op):
    """Create the FX-parameter envelope if missing and make it active and visible."""

    type: ClassVar[str] = "ensure_fx_envelope"
    targets: ClassVar[tuple[str, ...]] = ("track", "fx")
    track: str
    fx: str
    param: int

    def check(self) -> None:
        _int(self.param, "param", 0, 100000)


@op
@dataclass
class ClearEnvelopeRange(Op):
    type: ClassVar[str] = "clear_envelope_range"
    targets: ClassVar[tuple[str, ...]] = ("track", "fx")
    track: str
    fx: str
    param: int
    start: float
    end: float

    def check(self) -> None:
        _int(self.param, "param", 0, 100000)
        _time(self.start, "start")
        _time(self.end, "end")
        if self.end <= self.start:
            raise OpError("end must be greater than start")


@dataclass
class EnvPoint:
    time: float
    value: float  # in the envelope's own scale (usually the param's native range)
    shape: str = "linear"


@op
@dataclass
class InsertEnvelopePoints(Op):
    type: ClassVar[str] = "insert_envelope_points"
    targets: ClassVar[tuple[str, ...]] = ("track", "fx")
    track: str
    fx: str
    param: int
    points: list[EnvPoint] = field(default_factory=list)

    def check(self) -> None:
        _int(self.param, "param", 0, 100000)
        if not self.points:
            raise OpError("points must not be empty")
        last = -1.0
        for p in self.points:
            _time(p.time, "point time")
            if isinstance(p.value, bool) or not isinstance(p.value, (int, float)):
                raise OpError("point value must be a number")
            if not math.isfinite(p.value):
                raise OpError("point value must be finite")
            if p.shape not in ENV_SHAPES:
                raise OpError(f"point shape must be one of {ENV_SHAPES}, got {p.shape!r}")
            if p.time < last:
                raise OpError("points must be sorted by time")
            last = p.time


def op_from_dict(data: dict[str, Any]) -> Op:
    """Build and validate an op from its dict form ({"type": ..., fields...})."""
    if not isinstance(data, dict) or "type" not in data:
        raise OpError("op must be an object with a 'type'")
    cls = OPS.get(data["type"])
    if cls is None:
        raise OpError(f"unknown op type {data['type']!r}")
    body = {k: v for k, v in data.items() if k != "type"}
    try:
        built = serde.from_dict(cls, body, path=data["type"])
    except ValueError as exc:
        raise OpError(str(exc)) from exc
    built.validate()
    return built
