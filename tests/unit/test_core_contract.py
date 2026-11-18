"""Tests for the frozen contract: models, ops, plans, hashing."""

from __future__ import annotations

import copy

import pytest

from agent.core import hashing
from agent.core.models import SCHEMA_VERSION, Snapshot
from agent.core.ops import (
    OPS,
    AddRegion,
    ClearEnvelopeRange,
    EnsureFxEnvelope,
    EnvPoint,
    InsertEnvelopePoints,
    InsertTrack,
    MoveTrack,
    OpError,
    SetExtState,
    SetFolderDepth,
    SetFxParam,
    SetItemLane,
    SetLaneName,
    SetRegion,
    SetTakeName,
    SetTrackAutomationMode,
    SetTrackColor,
    SetTrackLaneMode,
    SetTrackName,
    op_from_dict,
)
from agent.core.plan import ApplyResult, OpResult, Plan, PlanError
from tests.unit.factories import guid, make_snapshot

T, I, K, F, R = guid(1), guid(201), guid(301), guid(101), guid(401)

# One valid example of every op in the v1 vocabulary.
EXAMPLES = [
    SetTrackName(track=T, name="DRM Kick In"),
    SetTrackColor(track=T, color="#0072B2"),
    SetFolderDepth(track=T, depth=1),
    MoveTrack(track=T, before=guid(2)),
    InsertTrack(name="BUS Drums", ref="drums", before=T, color="#0072B2"),
    SetTakeName(take=K, name="Kick T1"),
    SetItemLane(item=I, lane=1),
    SetTrackLaneMode(track=T, mode="fixed_lanes"),
    SetLaneName(track=T, lane=0, name="take 1"),
    AddRegion(name="chug", start=0.0, end=8.0, ref="r1"),
    SetRegion(region=R, name="open"),
    SetExtState(scope="track", target=T, key="reaper_agent_family", value="DRM"),
    SetFxParam(track=T, fx=F, param=1, normalized=0.35),
    EnsureFxEnvelope(track=T, fx=F, param=1),
    ClearEnvelopeRange(track=T, fx=F, param=1, start=0.0, end=8.0),
    InsertEnvelopePoints(
        track=T, fx=F, param=1, points=[EnvPoint(0.0, 0.25), EnvPoint(8.0, 0.18, "square")]
    ),
    SetTrackAutomationMode(track=T, mode="read"),
]

PLAN_VOCABULARY = {
    "set_track_name", "set_track_color", "set_folder_depth", "move_track", "insert_track",
    "set_take_name", "set_item_lane", "set_track_lane_mode", "set_lane_name", "add_region",
    "set_region", "set_ext_state", "set_fx_param", "ensure_fx_envelope", "clear_envelope_range",
    "insert_envelope_points", "set_track_automation_mode",
}  # fmt: skip


def test_vocabulary_is_exactly_the_planned_seventeen():
    assert set(OPS) == PLAN_VOCABULARY
    assert {type(e).type for e in EXAMPLES} == PLAN_VOCABULARY


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.type)
def test_every_op_round_trips_through_dict(example):
    example.validate()
    again = op_from_dict(example.to_dict())
    assert again == example
    assert again.to_dict() == example.to_dict()


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "set_track_name", "track": "3", "name": "x"},  # an index, not a GUID
        {"type": "set_track_name", "track": T, "name": "x" * 300},
        {"type": "set_track_color", "track": T, "color": "red"},
        {"type": "set_folder_depth", "track": T, "depth": 5},
        {"type": "set_track_lane_mode", "track": T, "mode": "lanes"},
        {"type": "set_item_lane", "item": I, "lane": -1},
        {"type": "add_region", "name": "x", "start": 5, "end": 5, "ref": "r"},
        {"type": "set_region", "region": R},  # changes nothing
        {"type": "set_ext_state", "scope": "project", "target": T, "key": "k", "value": "v"},
        {"type": "set_ext_state", "scope": "track", "key": "k", "value": "v"},
        {"type": "set_ext_state", "scope": "track", "target": T, "key": "bad key!", "value": "v"},
        {"type": "set_fx_param", "track": T, "fx": F, "param": 0, "normalized": 1.5},
        {"type": "insert_envelope_points", "track": T, "fx": F, "param": 0, "points": []},
        {"type": "insert_envelope_points", "track": T, "fx": F, "param": 0,
         "points": [{"time": 5, "value": 0.1}, {"time": 1, "value": 0.1}]},
        {"type": "insert_envelope_points", "track": T, "fx": F, "param": 0,
         "points": [{"time": 1, "value": 0.1, "shape": "wavy"}]},
        {"type": "nonsense"},
        {"type": "set_track_name", "track": T, "name": "x", "extra": 1},  # unknown field
    ],
)  # fmt: skip
def test_malformed_ops_are_rejected(bad):
    with pytest.raises(OpError):
        op_from_dict(bad)


def test_no_op_accepts_an_integer_track_index():
    for example in EXAMPLES:
        for name in type(example).targets:
            data = example.to_dict()
            data[name] = 3
            with pytest.raises((OpError, ValueError)):
                op_from_dict(data)


def test_plan_round_trip_and_ref_rules():
    ops = [
        InsertTrack(name="BUS Drums", ref="drums"),
        MoveTrack(track=T, before="ref:drums"),
        SetFolderDepth(track="ref:drums", depth=1),
    ]
    plan = Plan.new("Group drums", ops, base_hash="abc")
    again = Plan.from_dict(plan.to_dict())
    assert again.to_dict() == plan.to_dict()

    with pytest.raises(PlanError, match="used before"):
        Plan.new("x", [SetTrackName(track="ref:nope", name="a")], "h")
    with pytest.raises(PlanError, match="twice"):
        Plan.new("x", [InsertTrack(name="a", ref="same"), InsertTrack(name="b", ref="same")], "h")
    with pytest.raises(PlanError, match="at least one"):
        Plan.new("x", [], "h")
    with pytest.raises(PlanError):
        Plan.from_dict({**plan.to_dict(), "surprise": 1})


def test_apply_result_round_trip():
    result = ApplyResult(
        ok=False, plan_id="p", results=[OpResult(0, "set_track_name", False, "no such track")],
        refs={"ref:a": T}, rolled_back=True, error="op 0 failed",
    )  # fmt: skip
    assert ApplyResult.from_dict(result.to_dict()) == result


def test_snapshot_round_trip_and_schema_check():
    snap = make_snapshot()
    assert Snapshot.from_dict(snap.to_dict()) == snap
    bad = snap.to_dict()
    bad["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="schema_version"):
        Snapshot.from_dict(bad)
    bad = snap.to_dict()
    bad["tracks"][0]["index"] = 0  # indices are not part of the model
    with pytest.raises(ValueError, match="unknown field"):
        Snapshot.from_dict(bad)


def test_snapshot_model_has_no_index_fields():
    data = make_snapshot().to_dict()

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert not [k for k in keys(data) if "index" in k.lower() or k in ("idx", "id")]


def test_snapshot_sections():
    snap = make_snapshot()
    assert snap.section("project")["name"] == "demo.rpp"
    assert len(snap.section("tracks")) == 5
    with pytest.raises(ValueError):
        snap.section("nope")


def test_hash_ignores_item_marker_order_and_agent_ext():
    a = make_snapshot()
    b = copy.deepcopy(a)
    b.items.reverse()
    b.markers.reverse()
    b.tracks[3].agent_ext["reaper_agent:orig_name"] = "something else"
    b.items[0].agent_ext["reaper_agent:note"] = "x"
    b.items[0].takes[0].agent_ext["reaper_agent:orig_name"] = "x"
    b.project.agent_ext["reaper_agent:orig_region:{x}"] = "{}"
    assert hashing.snapshot_hash(a) == hashing.snapshot_hash(b)


def test_hash_changes_with_content():
    base = hashing.snapshot_hash(make_snapshot())
    renamed = make_snapshot()
    renamed.tracks[0].name = "Drumz"
    assert hashing.snapshot_hash(renamed) != base
    reordered = make_snapshot()
    reordered.tracks.reverse()  # track order is content
    assert hashing.snapshot_hash(reordered) != base
    moved = make_snapshot()
    moved.items[0].position += 0.001
    assert hashing.snapshot_hash(moved) != base


def test_hash_tolerates_float_noise_below_six_decimals():
    a = make_snapshot()
    b = make_snapshot()
    b.items[0].length += 1e-9
    assert hashing.snapshot_hash(a) == hashing.snapshot_hash(b)
