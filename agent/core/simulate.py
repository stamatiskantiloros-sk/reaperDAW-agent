"""Pure-Python simulation of every plan op on a Snapshot.

This is the reference semantics for `agent.apply_plan`: the fake bridge runs it, `build_plan` uses
it to predict the result, and the Lua handler must match it.

Values REAPER keeps but the Snapshot does not expose (FX parameter values, envelope points) live in
a `HiddenState`. Pass one to `apply_ops` to simulate them; without it they are simulated on a
throw-away default state (so ops still validate, but nothing is remembered).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from agent.core.models import EnvelopeInfo, Item, Marker, Snapshot, Take, Track
from agent.core.ops import EnvPoint, Op, OpError
from agent.core.plan import PlanError

EXT_PREFIX = "reaper_agent:"
DEFAULT_FX_PARAM = 0.5


class OpFailed(PlanError):
    """An op could not be applied. `str(exc)` is 'op i (type): reason'."""

    def __init__(self, index: int, op_type: str, reason: str) -> None:
        super().__init__(f"op {index} ({op_type}): {reason}")
        self.index = index
        self.op_type = op_type
        self.reason = reason


class GuidCounter:
    """Deterministic GUID source: {<head>-0000-0000-0000-<12 hex counter>}."""

    def __init__(self, head: str = "FA4E0000", start: int = 1) -> None:
        self.head = head
        self.next = start

    def __call__(self) -> str:
        guid = f"{{{self.head}-0000-0000-0000-{self.next:012X}}}"
        self.next += 1
        return guid


@dataclass
class HiddenState:
    """Project state outside the Snapshot model."""

    fx_params: dict[tuple[str, int], float] = field(default_factory=dict)
    env_points: dict[tuple[str, int], list[EnvPoint]] = field(default_factory=dict)

    def copy(self) -> HiddenState:
        return copy.deepcopy(self)

    def assign(self, other: HiddenState) -> None:
        """Replace this state's contents with `other`'s (in place)."""
        self.fx_params = other.fx_params
        self.env_points = other.env_points


@dataclass
class SimResult:
    snapshot: Snapshot
    refs: dict[str, str]  # "ref:alias" -> GUID
    op_data: list[dict]  # per op, e.g. {"guid": ...} for ops that create an object


class _Reason(Exception):
    """Internal: why an op failed."""


def recompute_parents(tracks: list[Track]) -> None:
    """Set every track's `parent` from the folder-depth sequence (depth 1 opens, -n closes n)."""
    stack: list[str] = []
    for t in tracks:
        t.parent = stack[-1] if stack else None
        if t.folder_depth > 0:
            stack.append(t.guid)
        elif t.folder_depth < 0:
            del stack[max(0, len(stack) + t.folder_depth) :]


def _sorted_insert(points: list[EnvPoint], new: list[EnvPoint]) -> list[EnvPoint]:
    return sorted(points + [copy.copy(p) for p in new], key=lambda p: p.time)  # stable


class _Simulator:
    def __init__(self, snap: Snapshot, hidden: HiddenState, new_guid: Callable[[], str]) -> None:
        self.snap = snap
        self.hidden = hidden
        self.new_guid = new_guid
        self.refs: dict[str, str] = {}
        self.known: set[str] = {m.guid for m in snap.markers}
        for t in snap.tracks:
            self.known.add(t.guid)
            self.known.update(f.guid for f in t.fx)
        for it in snap.items:
            self.known.add(it.guid)
            self.known.update(tk.guid for tk in it.takes)

    # -- helpers -------------------------------------------------------------------------

    def mint(self) -> str:
        guid = self.new_guid()
        if guid in self.known:
            raise _Reason(f"new_guid produced a GUID that already exists: {guid}")
        self.known.add(guid)
        return guid

    def define(self, op: Op, guid: str) -> None:
        alias = op.defined_ref()
        if alias is None:
            return
        if alias in self.refs:
            raise _Reason(f"{alias} is defined twice")
        self.refs[alias] = guid

    def resolve(self, target: str) -> str:
        if target.startswith("ref:"):
            if target not in self.refs:
                raise _Reason(f"{target} is not defined by an earlier op")
            return self.refs[target]
        return target

    def track(self, target: str) -> Track:
        t = self.snap.track(self.resolve(target))
        if t is None:
            raise _Reason(f"unknown track {target}")
        return t

    def item(self, target: str) -> Item:
        it = self.snap.item(self.resolve(target))
        if it is None:
            raise _Reason(f"unknown item {target}")
        return it

    def take(self, target: str) -> Take:
        guid = self.resolve(target)
        for it in self.snap.items:
            for tk in it.takes:
                if tk.guid == guid:
                    return tk
        raise _Reason(f"unknown take {target}")

    def region(self, target: str) -> Marker:
        m = self.snap.marker(self.resolve(target))
        if m is None:
            raise _Reason(f"unknown region {target}")
        if m.end is None:
            raise _Reason(f"{target} is a marker, not a region")
        return m

    @staticmethod
    def keep(ext: dict[str, str], key: str, value: str) -> None:
        """Save an original, only if it has not been saved before."""
        ext.setdefault(EXT_PREFIX + key, value)

    def fx_on_track(self, track: Track, fx: str) -> str:
        guid = self.resolve(fx)
        if not any(f.guid == guid for f in track.fx):
            raise _Reason(f"fx {fx} is not on track {track.guid}")
        return guid

    def envelope(self, track: Track, fx_guid: str, param: int) -> EnvelopeInfo | None:
        return next(
            (e for e in track.envelopes if e.kind == "fx" and e.fx == fx_guid and e.param == param),
            None,
        )

    def existing_envelope(self, track: Track, fx_guid: str, param: int) -> EnvelopeInfo:
        env = self.envelope(track, fx_guid, param)
        if env is None:
            raise _Reason(f"no envelope for fx {fx_guid} param {param}")
        return env

    # -- track ops -----------------------------------------------------------------------

    def set_track_name(self, o) -> dict:
        t = self.track(o.track)
        self.keep(t.agent_ext, "orig_name", t.name)
        t.name = o.name
        return {}

    def set_track_color(self, o) -> dict:
        t = self.track(o.track)
        self.keep(t.agent_ext, "orig_color", t.color or "")
        t.color = o.color
        return {}

    def set_folder_depth(self, o) -> dict:
        t = self.track(o.track)
        self.keep(t.agent_ext, "orig_folder_depth", str(t.folder_depth))
        t.folder_depth = o.depth
        recompute_parents(self.snap.tracks)
        return {}

    def move_track(self, o) -> dict:
        tracks = self.snap.tracks
        t = self.track(o.track)
        i = tracks.index(t)
        before_guid = None
        if o.before is not None:
            before_guid = self.track(o.before).guid
            if before_guid == t.guid:
                raise _Reason("cannot move a track before itself")
        self.keep(t.agent_ext, "orig_prev", tracks[i - 1].guid if i > 0 else "")
        tracks.pop(i)
        at = len(tracks) if before_guid is None else [x.guid for x in tracks].index(before_guid)
        tracks.insert(at, t)
        recompute_parents(tracks)
        return {}

    def insert_track(self, o) -> dict:
        tracks = self.snap.tracks
        at = len(tracks)
        if o.before is not None:
            before_guid = self.track(o.before).guid
            at = [x.guid for x in tracks].index(before_guid)
        new = Track(guid=self.mint(), name=o.name, color=o.color, folder_depth=0)
        self.define(o, new.guid)
        tracks.insert(at, new)
        recompute_parents(tracks)
        return {"guid": new.guid}

    def set_track_automation_mode(self, o) -> dict:
        t = self.track(o.track)
        self.keep(t.agent_ext, "orig_automation_mode", t.automation_mode)
        t.automation_mode = o.mode
        return {}

    # -- take / lane ops -----------------------------------------------------------------

    def set_take_name(self, o) -> dict:
        tk = self.take(o.take)
        self.keep(tk.agent_ext, "orig_name", tk.name)
        tk.name = o.name
        return {}

    def set_track_lane_mode(self, o) -> dict:
        t = self.track(o.track)
        self.keep(t.agent_ext, "orig_lane_mode", t.lane_mode)
        t.lane_mode = o.mode
        return {}

    def set_item_lane(self, o) -> dict:
        it = self.item(o.item)
        owner = self.snap.track(it.track)
        if owner is None or owner.lane_mode != "fixed_lanes":
            raise _Reason(f"track {it.track} is not in fixed_lanes mode")
        self.keep(it.agent_ext, "orig_lane", str(it.lane))
        it.lane = o.lane
        return {}

    def set_lane_name(self, o) -> dict:
        t = self.track(o.track)
        if len(t.lane_names) <= o.lane:
            t.lane_names.extend([""] * (o.lane + 1 - len(t.lane_names)))
        t.lane_names[o.lane] = o.name
        return {}

    # -- region / ext-state ops ----------------------------------------------------------

    def add_region(self, o) -> dict:
        number = max((m.number for m in self.snap.markers), default=0) + 1
        m = Marker(
            guid=self.mint(),
            number=number,
            name=o.name,
            position=o.start,
            end=o.end,
            color=o.color,
        )
        self.define(o, m.guid)
        self.snap.markers.append(m)
        return {"guid": m.guid}

    def set_region(self, o) -> dict:
        m = self.region(o.region)
        assert m.end is not None
        start = m.position if o.start is None else o.start
        end = m.end if o.end is None else o.end
        if end <= start:
            raise _Reason(f"end ({end}) must be greater than start ({start})")
        key = f"orig_region:{m.guid}"
        original = {"name": m.name, "start": m.position, "end": m.end, "color": m.color}
        self.keep(self.snap.project.agent_ext, key, json.dumps(original))
        if o.name is not None:
            m.name = o.name
        if o.color is not None:
            m.color = o.color
        m.position, m.end = start, end
        return {}

    def set_ext_state(self, o) -> dict:
        if o.scope == "project":
            ext = self.snap.project.agent_ext
        elif o.scope == "track":
            ext = self.track(o.target).agent_ext
        elif o.scope == "item":
            ext = self.item(o.target).agent_ext
        else:
            ext = self.take(o.target).agent_ext
        ext[EXT_PREFIX + o.key] = o.value
        return {}

    # -- FX and envelope ops -------------------------------------------------------------

    def set_fx_param(self, o) -> dict:
        t = self.track(o.track)
        fx = self.fx_on_track(t, o.fx)
        old = self.hidden.fx_params.get((fx, o.param), DEFAULT_FX_PARAM)
        self.keep(t.agent_ext, f"orig_fxparam:{fx}:{o.param}", repr(old))
        self.hidden.fx_params[(fx, o.param)] = float(o.normalized)
        return {}

    def ensure_fx_envelope(self, o) -> dict:
        t = self.track(o.track)
        fx = self.fx_on_track(t, o.fx)
        if self.envelope(t, fx, o.param) is None:
            t.envelopes.append(
                EnvelopeInfo(
                    kind="fx",
                    name=f"param {o.param}",
                    fx=fx,
                    param=o.param,
                    active=True,
                    visible=True,
                )
            )
        return {}

    def insert_envelope_points(self, o) -> dict:
        t = self.track(o.track)
        fx = self.resolve(o.fx)
        env = self.existing_envelope(t, fx, o.param)
        key = (fx, o.param)
        self.hidden.env_points[key] = _sorted_insert(self.hidden.env_points.get(key, []), o.points)
        env.point_count += len(o.points)
        return {}

    def clear_envelope_range(self, o) -> dict:
        t = self.track(o.track)
        fx = self.resolve(o.fx)
        env = self.existing_envelope(t, fx, o.param)
        key = (fx, o.param)
        points = self.hidden.env_points.get(key, [])
        kept = [p for p in points if not (o.start <= p.time < o.end)]
        env.point_count = max(0, env.point_count - (len(points) - len(kept)))
        if key in self.hidden.env_points:
            self.hidden.env_points[key] = kept
        return {}


def run_ops(
    snapshot: Snapshot,
    ops: list[Op],
    new_guid: Callable[[], str] | None = None,
    hidden: HiddenState | None = None,
) -> SimResult:
    """Like `apply_ops` but also reports per-op data. Raises `OpFailed` on the first bad op."""
    snap = copy.deepcopy(snapshot)
    work = hidden.copy() if hidden is not None else HiddenState()
    sim = _Simulator(snap, work, new_guid or GuidCounter())
    data: list[dict] = []
    for i, o in enumerate(ops):
        try:
            o.validate()
            data.append(getattr(sim, o.type)(o))
        except OpError as exc:
            raise OpFailed(i, o.type, str(exc)) from exc
        except _Reason as exc:
            raise OpFailed(i, o.type, str(exc)) from exc
    if hidden is not None:
        hidden.assign(work)  # only after every op succeeded
    return SimResult(snap, sim.refs, data)


def apply_ops(
    snapshot: Snapshot,
    ops: list[Op],
    new_guid: Callable[[], str] | None = None,
    hidden: HiddenState | None = None,
) -> tuple[Snapshot, dict[str, str]]:
    """Apply `ops` to a deep copy of `snapshot`; return (new snapshot, refs).

    `refs` maps "ref:alias" to the GUID of the object the alias created. The input is never
    mutated; `hidden`, if given, is updated in place only when every op succeeds. Raises
    PlanError("op i (type): reason") on the first failing op.
    """
    result = run_ops(snapshot, ops, new_guid, hidden)
    return result.snapshot, result.refs
