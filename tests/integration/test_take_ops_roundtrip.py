"""apply -> undo -> identical snapshot, for the take/lane/region/ext-state ops, on a live REAPER."""

from __future__ import annotations

import json

import pytest

from agent.core.hashing import snapshot_hash
from agent.core.ops import (
    AddRegion,
    SetExtState,
    SetItemLane,
    SetLaneName,
    SetRegion,
    SetTakeName,
    SetTrackLaneMode,
)
from agent.core.plan import Plan

pytestmark = pytest.mark.integration

DEAD = "{DEADBEEF-0000-0000-0000-000000000000}"


def _plan(summary, ops, base):
    return Plan.new(summary, ops, snapshot_hash(base))


def _full(snapshot):
    """Snapshot as a dict including agent_ext, except PROJECT ext state: REAPER does not undo
    SetProjExtState, so project-level ext keys (e.g. orig_region:*) legitimately outlive an undo."""
    data = snapshot.to_dict()
    data["project"].pop("agent_ext")
    return data


def _roundtrip(bridge, plan, base):
    """Apply, check something changed, undo once, check hash AND agent_ext are restored."""
    result = bridge.apply_plan(plan)
    assert result.ok, result.error
    assert all(r.ok for r in result.results)
    after = bridge.snapshot()
    assert _full(after) != _full(base) or after.project.agent_ext != base.project.agent_ext, (
        "plan changed nothing"
    )
    assert bridge.undo() is True
    restored = bridge.snapshot()
    assert snapshot_hash(restored) == snapshot_hash(base), "one undo did not restore"
    assert _full(restored) == _full(base), "undo left ext state or fields behind"
    return result, after


def _kick(project):
    track = next(t for t in project.tracks if t.name == "Kick In")
    item = next(i for i in project.items if i.track == track.guid)
    return track, item


def test_set_take_name(bridge, project):
    _, item = _kick(project)
    take = item.takes[0]
    plan = _plan("IT take name", [SetTakeName(take=take.guid, name="Kick take A")], project)
    _, after = _roundtrip(bridge, plan, project)
    changed = after.item(item.guid).takes[0]
    assert changed.name == "Kick take A"
    assert changed.agent_ext["reaper_agent:orig_name"] == take.name


def test_set_track_lane_mode(bridge, project):
    track, _ = _kick(project)
    plan = _plan("IT lane mode", [SetTrackLaneMode(track=track.guid, mode="fixed_lanes")], project)
    _, after = _roundtrip(bridge, plan, project)
    changed = after.track(track.guid)
    assert changed.lane_mode == "fixed_lanes"
    assert changed.agent_ext["reaper_agent:orig_lane_mode"] == "normal"
    plan = _plan("IT lane mode free", [SetTrackLaneMode(track=track.guid, mode="free")], project)
    _, after = _roundtrip(bridge, plan, project)
    assert after.track(track.guid).lane_mode == "free"


def test_item_lane_and_lane_name(bridge, project):
    track, item = _kick(project)
    plan = _plan(
        "IT lanes",
        [
            SetTrackLaneMode(track=track.guid, mode="fixed_lanes"),
            SetLaneName(track=track.guid, lane=2, name="Take 3"),
            SetItemLane(item=item.guid, lane=1),
        ],
        project,
    )
    _, after = _roundtrip(bridge, plan, project)
    changed = after.track(track.guid)
    assert changed.lane_mode == "fixed_lanes"
    assert changed.lane_names[2] == "Take 3"
    assert len(changed.lane_names) >= 3
    assert after.item(item.guid).lane == 1
    assert after.item(item.guid).agent_ext["reaper_agent:orig_lane"] == "0"


def test_item_lane_requires_fixed_lanes(bridge, project):
    _, item = _kick(project)
    result = bridge.apply_plan(
        _plan("IT lane on normal track", [SetItemLane(item=item.guid, lane=1)], project)
    )
    assert not result.ok and "fixed_lanes" in result.error
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)


def test_add_and_set_region_with_ref(bridge, project):
    plan = _plan(
        "IT add+set region",
        [
            AddRegion(name="IT bridge", start=30.0, end=34.0, ref="newreg", color="#D55E00"),
            SetRegion(region="ref:newreg", name="IT bridge 2", end=36.0),
        ],
        project,
    )
    result, after = _roundtrip(bridge, plan, project)
    guid = result.refs["ref:newreg"]
    # The undo above removed it; the apply result still carries the data seen at apply time.
    assert result.results[0].data["guid"] == guid
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)
    new = after.marker(guid)
    assert (new.name, new.position, new.end, new.color) == ("IT bridge 2", 30.0, 36.0, "#D55E00")


def test_set_region_saves_original_once(bridge, project):
    chug = next(m for m in project.markers if m.name == "chug")
    plan = _plan(
        "IT set region",
        [
            SetRegion(region=chug.guid, name="chug renamed"),
            SetRegion(region=chug.guid, start=0.5),
        ],
        project,
    )
    _, after = _roundtrip(bridge, plan, project)
    changed = after.marker(chug.guid)
    assert (changed.name, changed.position, changed.end) == ("chug renamed", 0.5, chug.end)
    assert changed.color == chug.color
    saved = json.loads(after.project.agent_ext[f"reaper_agent:orig_region:{chug.guid}"])
    assert saved["name"] == "chug" and saved["start"] == chug.position and saved["end"] == chug.end


def test_set_region_rejects_marker_and_bad_range(bridge, project):
    hit = next(m for m in project.markers if m.name == "hit")
    chug = next(m for m in project.markers if m.name == "chug")
    result = bridge.apply_plan(_plan("IT marker", [SetRegion(region=hit.guid, name="x")], project))
    assert not result.ok and "marker" in result.error
    bad_range = bridge.apply_plan(
        _plan("IT range", [SetRegion(region=chug.guid, start=chug.end - 1.0)], project)
    )
    assert bad_range.ok  # start < end is fine
    assert bridge.undo() is True
    bad_range = bridge.apply_plan(
        _plan("IT range 2", [SetRegion(region=chug.guid, start=chug.end + 1.0)], project)
    )
    assert not bad_range.ok and "greater than start" in bad_range.error
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)


def test_set_ext_state_all_scopes(bridge, project):
    track, item = _kick(project)
    take = item.takes[0]
    plan = _plan(
        "IT ext state",
        [
            SetExtState(scope="track", key="family", value="DRM", target=track.guid),
            SetExtState(scope="item", key="note", value="loud", target=item.guid),
            SetExtState(scope="take", key="take_note", value="A", target=take.guid),
            SetExtState(scope="project", key="stamp", value="it"),
        ],
        project,
    )
    result = bridge.apply_plan(plan)
    assert result.ok, result.error
    after = bridge.snapshot()
    assert after.track(track.guid).agent_ext["reaper_agent:family"] == "DRM"
    assert after.item(item.guid).agent_ext["reaper_agent:note"] == "loud"
    assert after.item(item.guid).takes[0].agent_ext["reaper_agent:take_note"] == "A"
    assert after.project.agent_ext["reaper_agent:stamp"] == "it"
    assert snapshot_hash(after) == snapshot_hash(project)  # ext state is outside the hash
    assert bridge.undo() is True
    restored = bridge.snapshot()
    assert _full(restored) == _full(project), "undo left track/item/take ext state behind"


def test_failing_take_plan_rolls_back_completely(bridge, project):
    track, _ = _kick(project)
    bad = _plan(
        "IT failing take plan",
        [
            SetTrackLaneMode(track=track.guid, mode="fixed_lanes"),
            SetTakeName(take=DEAD, name="x"),
        ],
        project,
    )
    result = bridge.apply_plan(bad)
    assert not result.ok and result.rolled_back
    assert "op 1" in result.error
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)
