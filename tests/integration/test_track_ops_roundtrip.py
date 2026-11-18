"""apply -> undo -> identical snapshot, for the track ops, on a live REAPER."""

from __future__ import annotations

import pytest

from agent.core.hashing import snapshot_hash
from agent.core.ops import (
    InsertTrack,
    MoveTrack,
    SetFolderDepth,
    SetTrackAutomationMode,
    SetTrackColor,
    SetTrackName,
)
from agent.core.plan import Plan

pytestmark = pytest.mark.integration


def _plan(summary, ops, base):
    return Plan.new(summary, ops, snapshot_hash(base))


def _roundtrip(bridge, plan, base):
    result = bridge.apply_plan(plan)
    assert result.ok, result.error
    assert all(r.ok for r in result.results)
    after = bridge.snapshot()
    assert snapshot_hash(after) != snapshot_hash(base), "plan changed nothing"
    assert bridge.undo() is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base), "one undo did not restore"
    return result, after


def test_rename_and_recolour(bridge, project):
    kick = next(t for t in project.tracks if t.name == "Kick In")
    plan = _plan(
        "IT rename+colour",
        [
            SetTrackName(track=kick.guid, name="DRM Kick In"),
            SetTrackColor(track=kick.guid, color="#0072B2"),
        ],
        project,
    )
    _, after = _roundtrip(bridge, plan, project)
    changed = after.track(kick.guid)
    assert (changed.name, changed.color) == ("DRM Kick In", "#0072B2")
    assert changed.agent_ext["reaper_agent:orig_name"] == "Kick In"


def test_insert_move_folder_with_refs(bridge, project):
    drums = project.tracks[0]
    kick = next(t for t in project.tracks if t.name == "Kick In")
    plan = _plan(
        "IT insert+move+folder",
        [
            InsertTrack(name="BUS Test", ref="bus", before=drums.guid),
            SetFolderDepth(track="ref:bus", depth=1),
            MoveTrack(track=kick.guid, before=None),
        ],
        project,
    )
    result, after = _roundtrip(bridge, plan, project)
    new_guid = result.refs["ref:bus"]
    assert after.tracks[0].guid == new_guid and after.tracks[-1].guid == kick.guid


def test_automation_mode(bridge, project):
    snare = next(t for t in project.tracks if t.name == "Snare Top")
    plan = _plan(
        "IT automation mode", [SetTrackAutomationMode(track=snare.guid, mode="read")], project
    )
    _, after = _roundtrip(bridge, plan, project)
    assert after.track(snare.guid).automation_mode == "read"


def test_failing_plan_rolls_back_completely(bridge, project):
    kick = next(t for t in project.tracks if t.name == "Kick In")
    bad = _plan(
        "IT failing plan",
        [
            SetTrackName(track=kick.guid, name="MUST NOT STAY"),
            SetTrackName(track="{DEADBEEF-0000-0000-0000-000000000000}", name="x"),
        ],
        project,
    )
    result = bridge.apply_plan(bad)
    assert not result.ok and result.rolled_back
    assert "op 1" in result.error
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)
