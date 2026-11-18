"""Simulation of every op: happy path, failure path, originals, refs, immutability."""

from __future__ import annotations

import copy
import json

import pytest

from agent.core import hashing
from agent.core.models import Snapshot
from agent.core.ops import (
    AddRegion,
    ClearEnvelopeRange,
    EnsureFxEnvelope,
    EnvPoint,
    InsertEnvelopePoints,
    InsertTrack,
    MoveTrack,
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
)
from agent.core.plan import PlanError
from agent.core.simulate import GuidCounter, HiddenState, apply_ops, recompute_parents
from tests.unit.factories import guid, make_snapshot

DRUMS, KICK, SNARE, BASS, VERB = (guid(i) for i in range(1, 6))
FX, ITEM_KICK, TAKE_KICK, REGION, MARKER = guid(101), guid(201), guid(301), guid(401), guid(403)
P = "reaper_agent:"


def run(*ops, snap=None, hidden=None):
    return apply_ops(snap or make_snapshot(), list(ops), hidden=hidden)


def fail(*ops, match, snap=None):
    with pytest.raises(PlanError, match=match):
        run(*ops, snap=snap)


def names(snap: Snapshot) -> list[str]:
    return [t.name for t in snap.tracks]


# --- general behaviour -------------------------------------------------------------------------


def test_input_is_never_mutated_and_default_guids_are_deterministic():
    snap = make_snapshot()
    before = copy.deepcopy(snap)
    ops = [InsertTrack(name="A", ref="a"), AddRegion(name="r", start=1, end=2, ref="r")]
    out1, refs1 = apply_ops(snap, ops)
    out2, refs2 = apply_ops(snap, ops)
    assert snap == before
    assert refs1 == refs2 == {
        "ref:a": "{FA4E0000-0000-0000-0000-000000000001}",
        "ref:r": "{FA4E0000-0000-0000-0000-000000000002}",
    }  # fmt: skip
    assert out1 == out2


def test_custom_new_guid_and_duplicate_guid_rejected():
    _, refs = run_ops_with(GuidCounter("AAAA0000"), InsertTrack(name="A", ref="a"))
    assert refs["ref:a"].startswith("{AAAA0000-")
    with pytest.raises(PlanError, match="already exists"):
        apply_ops(make_snapshot(), [InsertTrack(name="A", ref="a")], new_guid=lambda: KICK)


def run_ops_with(new_guid, *ops):
    return apply_ops(make_snapshot(), list(ops), new_guid=new_guid)


def test_error_names_first_failing_op_and_leaves_input_alone():
    snap = make_snapshot()
    before = copy.deepcopy(snap)
    ops = [SetTrackName(track=KICK, name="ok"), SetTrackName(track=guid(999), name="bad")]
    with pytest.raises(PlanError, match=r"op 1 \(set_track_name\): unknown track"):
        apply_ops(snap, ops)
    assert snap == before


def test_undefined_and_duplicate_refs_fail():
    fail(SetTrackName(track="ref:nope", name="x"), match="not defined")
    fail(InsertTrack(name="a", ref="x"), InsertTrack(name="b", ref="x"), match="defined twice")


def test_invalid_op_is_reported_with_its_index():
    fail(SetTrackName(track="3", name="x"), match=r"op 0 \(set_track_name\)")  # type: ignore[arg-type]


# --- track ops ---------------------------------------------------------------------------------


def test_set_track_name_saves_original_once():
    out, _ = run(SetTrackName(track=KICK, name="K1"), SetTrackName(track=KICK, name="K2"))
    t = out.track(KICK)
    assert t.name == "K2"
    assert t.agent_ext[P + "orig_name"] == "Kick In"  # first value, not the intermediate "K1"
    out2, _ = run(SetTrackName(track=BASS, name="Bass"))
    assert out2.track(BASS).agent_ext[P + "orig_name"] == "Audio 07"  # existing key kept


def test_set_track_name_unknown_track():
    fail(SetTrackName(track=guid(999), name="x"), match="unknown track")


def test_set_track_color_saves_original():
    out, _ = run(SetTrackColor(track=DRUMS, color="#112233"), SetTrackColor(track=BASS, color=None))
    assert out.track(DRUMS).color == "#112233"
    assert out.track(DRUMS).agent_ext[P + "orig_color"] == "#0072B2"
    assert out.track(BASS).agent_ext[P + "orig_color"] == ""
    fail(SetTrackColor(track=guid(999), color=None), match="unknown track")


def test_set_folder_depth_recomputes_parents():
    out, _ = run(SetFolderDepth(track=SNARE, depth=0))  # snare no longer closes the folder
    assert out.track(SNARE).agent_ext[P + "orig_folder_depth"] == "-1"
    assert out.track(BASS).parent == DRUMS and out.track(VERB).parent == DRUMS
    out, _ = run(SetFolderDepth(track=BASS, depth=-1), SetFolderDepth(track=SNARE, depth=0))
    assert out.track(VERB).parent is None
    fail(SetFolderDepth(track=guid(999), depth=0), match="unknown track")


def test_recompute_parents_nested_and_multi_close():
    snap = make_snapshot()
    a, b, c, d = snap.tracks[:4]
    a.folder_depth, b.folder_depth, c.folder_depth, d.folder_depth = 1, 1, -2, 0
    recompute_parents(snap.tracks)
    assert (a.parent, b.parent, c.parent, d.parent) == (None, a.guid, b.guid, None)


def test_move_track_before_and_to_end():
    out, _ = run(MoveTrack(track=BASS, before=KICK))
    assert out.tracks[1].guid == BASS
    assert out.track(BASS).agent_ext[P + "orig_prev"] == SNARE
    assert out.track(BASS).parent == DRUMS  # now inside the folder
    out, _ = run(MoveTrack(track=DRUMS, before=None))
    assert out.tracks[-1].guid == DRUMS
    assert out.track(DRUMS).agent_ext[P + "orig_prev"] == ""
    assert out.track(KICK).parent is None  # children are not moved along


def test_move_track_failures():
    fail(MoveTrack(track=guid(999)), match="unknown track")
    fail(MoveTrack(track=KICK, before=guid(999)), match="unknown track")
    fail(MoveTrack(track=KICK, before=KICK), match="before itself")


def test_insert_track_position_and_ref_use():
    out, refs = run(
        InsertTrack(name="Bus", ref="bus", before=BASS, color="#FFFFFF"),
        InsertTrack(name="Last", ref="last"),
        SetTrackName(track="ref:bus", name="Bus 2"),
    )
    new = refs["ref:bus"]
    assert [t.guid for t in out.tracks][3:5] == [new, BASS]
    assert out.tracks[-1].guid == refs["ref:last"]
    assert out.track(new).name == "Bus 2" and out.track(new).color == "#FFFFFF"
    assert out.track(new).folder_depth == 0


def test_insert_track_inside_folder_gets_parent():
    out, refs = run(InsertTrack(name="Tom", ref="tom", before=SNARE))
    assert out.track(refs["ref:tom"]).parent == DRUMS


def test_insert_track_failure():
    fail(InsertTrack(name="x", ref="x", before=guid(999)), match="unknown track")


def test_refs_across_ops_group_drums():
    out, refs = run(
        InsertTrack(name="BUS Drums", ref="grp", before=DRUMS),
        MoveTrack(track=DRUMS, before=None),
        SetFolderDepth(track="ref:grp", depth=1),
        SetFolderDepth(track=VERB, depth=-1),
    )
    grp = refs["ref:grp"]
    assert out.tracks[0].guid == grp
    assert out.track(grp).parent is None
    assert out.track(KICK).parent == grp and out.track(SNARE).parent == grp
    assert out.track(BASS).parent is None  # snare (-1) closed the new folder
    assert out.tracks[-1].guid == DRUMS and out.track(DRUMS).parent is None


def test_set_track_automation_mode():
    out, _ = run(SetTrackAutomationMode(track=KICK, mode="write"))
    assert out.track(KICK).automation_mode == "write"
    assert out.track(KICK).agent_ext[P + "orig_automation_mode"] == "trim_read"
    fail(SetTrackAutomationMode(track=guid(999), mode="read"), match="unknown track")


# --- take / lane ops ---------------------------------------------------------------------------


def test_set_take_name():
    out, _ = run(SetTakeName(take=TAKE_KICK, name="T1"))
    take = out.items[0].takes[0]
    assert take.name == "T1" and take.agent_ext[P + "orig_name"] == "Kick In"
    fail(SetTakeName(take=guid(999), name="x"), match="unknown take")
    fail(SetTakeName(take=KICK, name="x"), match="unknown take")  # a track is not a take


def test_set_track_lane_mode():
    out, _ = run(SetTrackLaneMode(track=KICK, mode="fixed_lanes"))
    t = out.track(KICK)
    assert t.lane_mode == "fixed_lanes" and t.agent_ext[P + "orig_lane_mode"] == "normal"
    assert out.items[0].lane == 0  # only the mode changes
    fail(SetTrackLaneMode(track=guid(999), mode="free"), match="unknown track")


def test_set_item_lane_requires_fixed_lanes():
    out, _ = run(
        SetTrackLaneMode(track=KICK, mode="fixed_lanes"), SetItemLane(item=ITEM_KICK, lane=2)
    )
    assert out.item(ITEM_KICK).lane == 2
    assert out.item(ITEM_KICK).agent_ext[P + "orig_lane"] == "0"
    fail(SetItemLane(item=ITEM_KICK, lane=1), match="fixed_lanes")
    fail(SetItemLane(item=guid(999), lane=1), match="unknown item")


def test_set_lane_name_pads():
    out, _ = run(SetLaneName(track=KICK, lane=2, name="take 3"))
    assert out.track(KICK).lane_names == ["", "", "take 3"]
    out, _ = run(
        SetLaneName(track=KICK, lane=2, name="c"), SetLaneName(track=KICK, lane=0, name="a")
    )
    assert out.track(KICK).lane_names == ["a", "", "c"]
    assert out.track(KICK).lane_mode == "normal"
    fail(SetLaneName(track=guid(999), lane=0, name="x"), match="unknown track")


# --- regions and ext state ---------------------------------------------------------------------


def test_add_region_numbers_and_ref():
    out, refs = run(
        AddRegion(name="a", start=16, end=20, ref="a", color="#010203"),
        AddRegion(name="b", start=20, end=24, ref="b"),
    )
    a, b = out.marker(refs["ref:a"]), out.marker(refs["ref:b"])
    assert (a.number, b.number) == (4, 5)
    assert a.is_region and a.end == 20 and a.color == "#010203"
    fail(
        AddRegion(name="a", start=0, end=1, ref="a"),
        AddRegion(name="b", start=0, end=1, ref="a"),
        match="twice",
    )


def test_set_region_partial_update_and_original():
    out, _ = run(SetRegion(region=REGION, name="verse"), SetRegion(region=REGION, end=9.0))
    r = out.marker(REGION)
    assert (r.name, r.position, r.end) == ("verse", 0.0, 9.0)
    orig = json.loads(out.project.agent_ext[f"{P}orig_region:{REGION}"])
    assert orig == {"name": "chug", "start": 0.0, "end": 8.0, "color": None}


def test_set_region_by_ref_and_color():
    out, refs = run(
        AddRegion(name="n", start=30, end=40, ref="n"), SetRegion(region="ref:n", color="#ABCDEF")
    )
    assert out.marker(refs["ref:n"]).color == "#ABCDEF"


def test_set_region_failures():
    fail(SetRegion(region=guid(999), name="x"), match="unknown region")
    fail(SetRegion(region=MARKER, name="x"), match="not a region")
    fail(SetRegion(region=REGION, end=0.0), match="greater than start")
    fail(SetRegion(region=REGION, start=8.0), match="greater than start")
    fail(SetRegion(region=REGION, start=3.0, end=3.0), match="greater than start")


@pytest.mark.parametrize(
    ("scope", "target", "getter"),
    [
        ("track", KICK, lambda s: s.track(KICK).agent_ext),
        ("item", ITEM_KICK, lambda s: s.item(ITEM_KICK).agent_ext),
        ("take", TAKE_KICK, lambda s: s.items[0].takes[0].agent_ext),
        ("project", None, lambda s: s.project.agent_ext),
    ],
)
def test_set_ext_state_scopes(scope, target, getter):
    out, _ = run(SetExtState(scope=scope, target=target, key="family", value="DRM"))
    assert getter(out) == {P + "family": "DRM"}


def test_set_ext_state_by_ref_and_failures():
    out, refs = run(
        InsertTrack(name="n", ref="n"),
        SetExtState(scope="track", target="ref:n", key="k", value="v"),
    )
    assert out.track(refs["ref:n"]).agent_ext == {P + "k": "v"}
    fail(SetExtState(scope="track", target=guid(999), key="k", value="v"), match="unknown track")
    fail(SetExtState(scope="item", target=KICK, key="k", value="v"), match="unknown item")


# --- FX and envelopes --------------------------------------------------------------------------


def test_set_fx_param_hidden_state_and_original_once():
    hidden = HiddenState()
    out, _ = run(SetFxParam(track=KICK, fx=FX, param=1, normalized=0.2), hidden=hidden)
    run_again = apply_ops(
        out, [SetFxParam(track=KICK, fx=FX, param=1, normalized=0.9)], hidden=hidden
    )[0]
    assert hidden.fx_params[(FX, 1)] == 0.9
    assert run_again.track(KICK).agent_ext[f"{P}orig_fxparam:{FX}:1"] == "0.5"
    # the saved original is the value before the FIRST change (0.5 default), not 0.2


def test_set_fx_param_failure_leaves_hidden_untouched():
    hidden = HiddenState()
    ops = [
        SetFxParam(track=KICK, fx=FX, param=1, normalized=0.2),
        SetFxParam(track=SNARE, fx=FX, param=1, normalized=0.3),  # fx not on that track
    ]
    with pytest.raises(PlanError, match=r"op 1 .*not on track"):
        apply_ops(make_snapshot(), ops, hidden=hidden)
    assert hidden.fx_params == {}
    fail(SetFxParam(track=KICK, fx=guid(999), param=0, normalized=0.1), match="not on track")


def test_ensure_fx_envelope_creates_once():
    out, _ = run(EnsureFxEnvelope(track=KICK, fx=FX, param=4))
    env = [e for e in out.track(KICK).envelopes if e.param == 4]
    assert len(env) == 1
    e = env[0]
    assert (e.kind, e.name, e.fx, e.active, e.visible, e.point_count) == (
        "fx", "param 4", FX, True, True, 0,
    )  # fmt: skip
    out2, _ = apply_ops(out, [EnsureFxEnvelope(track=KICK, fx=FX, param=4)])
    assert out2 == out
    # an existing envelope (param 1) is left untouched
    out3, _ = run(EnsureFxEnvelope(track=KICK, fx=FX, param=1))
    assert out3 == make_snapshot()
    fail(EnsureFxEnvelope(track=SNARE, fx=FX, param=1), match="not on track")


def test_insert_and_clear_envelope_points():
    hidden = HiddenState()
    pts = [EnvPoint(0.0, 0.1), EnvPoint(8.0, 0.4)]
    out, _ = run(
        EnsureFxEnvelope(track=KICK, fx=FX, param=2),
        InsertEnvelopePoints(track=KICK, fx=FX, param=2, points=pts),
        InsertEnvelopePoints(track=KICK, fx=FX, param=2, points=[EnvPoint(4.0, 0.2)]),
        hidden=hidden,
    )
    env = next(e for e in out.track(KICK).envelopes if e.param == 2)
    assert env.point_count == 3
    assert [p.time for p in hidden.env_points[(FX, 2)]] == [0.0, 4.0, 8.0]
    out, _ = apply_ops(
        out, [ClearEnvelopeRange(track=KICK, fx=FX, param=2, start=0.0, end=8.0)], hidden=hidden
    )
    env = next(e for e in out.track(KICK).envelopes if e.param == 2)
    assert env.point_count == 1  # [start, end): the point at 8.0 stays
    assert [p.time for p in hidden.env_points[(FX, 2)]] == [8.0]


def test_envelope_point_ops_need_existing_envelope():
    fail(
        InsertEnvelopePoints(track=KICK, fx=FX, param=7, points=[EnvPoint(0.0, 0.1)]),
        match="no envelope",
    )
    fail(ClearEnvelopeRange(track=KICK, fx=FX, param=7, start=0, end=1), match="no envelope")
    fail(ClearEnvelopeRange(track=guid(999), fx=FX, param=1, start=0, end=1), match="unknown track")


def test_hash_unchanged_by_ext_only_ops():
    snap = make_snapshot()
    out, _ = run(SetExtState(scope="project", key="k", value="v"))
    assert hashing.snapshot_hash(out) == hashing.snapshot_hash(snap)
