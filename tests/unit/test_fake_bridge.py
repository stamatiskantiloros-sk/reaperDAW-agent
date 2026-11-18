"""FakeBridge: apply / undo round trip for every op, atomicity, dry run, undo stack."""

from __future__ import annotations

import pytest

from agent.bridge_client.fake import FakeBridge
from agent.bridge_client.protocol import Bridge
from agent.core.hashing import snapshot_hash
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
from agent.core.plan import Plan
from tests.unit.factories import guid, make_snapshot

DRUMS, KICK, SNARE, BASS, VERB = (guid(i) for i in range(1, 6))
FX, ITEM, TAKE, REGION = guid(101), guid(201), guid(301), guid(401)

# One (setup ops, ops) pair per op type; setup is applied first, then ops are applied and undone.
CASES = {
    "set_track_name": ([], [SetTrackName(track=KICK, name="X")]),
    "set_track_color": ([], [SetTrackColor(track=KICK, color="#102030")]),
    "set_folder_depth": ([], [SetFolderDepth(track=SNARE, depth=0)]),
    "move_track": ([], [MoveTrack(track=BASS, before=KICK)]),
    "insert_track": ([], [InsertTrack(name="N", ref="n", before=BASS)]),
    "set_track_automation_mode": ([], [SetTrackAutomationMode(track=KICK, mode="write")]),
    "set_take_name": ([], [SetTakeName(take=TAKE, name="T")]),
    "set_track_lane_mode": ([], [SetTrackLaneMode(track=KICK, mode="fixed_lanes")]),
    "set_item_lane": (
        [SetTrackLaneMode(track=KICK, mode="fixed_lanes")],
        [SetItemLane(item=ITEM, lane=1)],
    ),
    "set_lane_name": ([], [SetLaneName(track=KICK, lane=1, name="L")]),
    "add_region": ([], [AddRegion(name="r", start=20, end=24, ref="r")]),
    "set_region": ([], [SetRegion(region=REGION, name="y", end=9.0)]),
    "set_ext_state": ([], [SetExtState(scope="project", key="k", value="v")]),
    "set_fx_param": ([], [SetFxParam(track=KICK, fx=FX, param=1, normalized=0.3)]),
    "ensure_fx_envelope": ([], [EnsureFxEnvelope(track=KICK, fx=FX, param=6)]),
    "clear_envelope_range": (
        [InsertEnvelopePoints(track=KICK, fx=FX, param=1, points=[EnvPoint(1.0, 0.2)])],
        [ClearEnvelopeRange(track=KICK, fx=FX, param=1, start=0.0, end=2.0)],
    ),
    "insert_envelope_points": (
        [],
        [InsertEnvelopePoints(track=KICK, fx=FX, param=1, points=[EnvPoint(1.0, 0.2)])],
    ),
}


def plan(*ops, summary="test"):
    return Plan.new(summary, list(ops), "unused")


def test_cases_cover_every_op():
    from agent.core.ops import OPS

    assert set(CASES) == set(OPS)


def test_satisfies_bridge_protocol_and_status():
    b = FakeBridge(make_snapshot())
    assert isinstance(b, Bridge)
    assert b.status()["alive"] is True and b.status()["project"] == "demo.rpp"


def test_snapshot_is_a_deep_copy_and_constructor_copies():
    src = make_snapshot()
    b = FakeBridge(src)
    src.tracks[0].name = "changed outside"
    s = b.snapshot()
    assert s.tracks[0].name == "Drums"
    s.tracks[0].name = "changed copy"
    assert b.snapshot().tracks[0].name == "Drums"


@pytest.mark.parametrize("name", sorted(CASES))
def test_apply_then_undo_restores_identical_snapshot(name):
    setup, ops = CASES[name]
    b = FakeBridge(make_snapshot())
    if setup:
        assert b.apply_plan(plan(*setup)).ok
    before = b.snapshot()
    h_before = snapshot_hash(before)
    fx, env = dict(b.fx_params), {k: list(v) for k, v in b.env_points.items()}

    result = b.apply_plan(plan(*ops))
    assert result.ok and not result.rolled_back and not result.dry_run
    assert [r.type for r in result.results] == [o.type for o in ops]
    assert all(r.ok for r in result.results)

    assert b.undo() is True
    assert snapshot_hash(b.snapshot()) == h_before
    assert b.snapshot() == before  # including agent_ext originals
    assert b.fx_params == fx and b.env_points == env


def test_applied_ops_change_hash_except_ext_only_ops():
    for name, (setup, ops) in CASES.items():
        b = FakeBridge(make_snapshot())
        if setup:
            b.apply_plan(plan(*setup))
        h = snapshot_hash(b.snapshot())
        b.apply_plan(plan(*ops))
        changed = snapshot_hash(b.snapshot()) != h
        assert changed == (name not in ("set_ext_state", "set_fx_param")), name


def test_undo_empty_returns_false_and_stack_is_one_entry_per_apply():
    b = FakeBridge(make_snapshot())
    assert b.undo() is False
    b.apply_plan(plan(SetTrackName(track=KICK, name="a")))
    b.apply_plan(plan(SetTrackName(track=KICK, name="b"), SetTrackName(track=SNARE, name="c")))
    b.apply_plan(plan(SetTrackName(track=KICK, name="d")), dry_run=True)  # no entry
    assert b.undo() and b.snapshot().track(KICK).name == "a"
    assert b.snapshot().track(SNARE).name == "Snare Top"
    assert b.undo() and b.snapshot().track(KICK).name == "Kick In"
    assert b.undo() is False


def test_hidden_state_restored_by_undo():
    b = FakeBridge(make_snapshot())
    b.apply_plan(plan(SetFxParam(track=KICK, fx=FX, param=1, normalized=0.3)))
    b.apply_plan(plan(SetFxParam(track=KICK, fx=FX, param=1, normalized=0.8)))
    assert b.fx_params[(FX, 1)] == 0.8
    b.undo()
    assert b.fx_params[(FX, 1)] == 0.3
    b.undo()
    assert b.fx_params == {}


def test_atomic_rollback_on_mid_plan_failure():
    b = FakeBridge(make_snapshot())
    before = b.snapshot()
    result = b.apply_plan(
        plan(
            SetTrackName(track=KICK, name="x"),
            SetFxParam(track=KICK, fx=FX, param=1, normalized=0.9),
            InsertTrack(name="n", ref="n"),
            SetTrackName(track=guid(999), name="boom"),
            SetTrackName(track=SNARE, name="never"),
        )
    )
    assert result.ok is False and result.rolled_back is True
    assert "op 3 (set_track_name)" in result.error
    assert [(r.index, r.ok) for r in result.results] == [
        (0, True),
        (1, True),
        (2, True),
        (3, False),
    ]
    assert "unknown track" in result.results[3].error
    assert b.snapshot() == before
    assert b.fx_params == {}
    assert b.undo() is False  # a failed apply leaves no undo entry


def test_failed_apply_does_not_consume_guids():
    b = FakeBridge(make_snapshot())
    ins = InsertTrack(name="n", ref="n")
    b.apply_plan(plan(ins, SetTrackName(track=guid(999), name="boom")))
    ok = b.apply_plan(plan(ins))
    assert ok.refs["ref:n"] == "{BB1D0000-0000-0000-0000-000000000001}"


def test_dry_run_changes_nothing():
    b = FakeBridge(make_snapshot())
    before = b.snapshot()
    p = plan(
        InsertTrack(name="n", ref="n"),
        SetFxParam(track=KICK, fx=FX, param=1, normalized=0.9),
        SetTrackName(track="ref:n", name="renamed"),
    )
    result = b.apply_plan(p, dry_run=True)
    assert result.ok and result.dry_run and not result.rolled_back
    assert all(r.ok and r.data == {"dry_run": True} for r in result.results)
    assert len(result.results) == 3 and "ref:n" in result.refs
    assert b.snapshot() == before and b.fx_params == {} and b.undo() is False
    # the real apply afterwards still gets the first GUID of the counter
    real = b.apply_plan(p)
    assert real.refs["ref:n"] == result.refs["ref:n"]


def test_dry_run_reports_failures_without_rollback_flag():
    b = FakeBridge(make_snapshot())
    result = b.apply_plan(plan(SetTrackName(track=guid(999), name="x")), dry_run=True)
    assert not result.ok and result.dry_run and not result.rolled_back
    assert result.results[0].ok is False


def test_refs_returned_and_usable_across_ops():
    b = FakeBridge(make_snapshot())
    result = b.apply_plan(
        plan(
            InsertTrack(name="Grp", ref="g", before=DRUMS),
            MoveTrack(track=DRUMS, before=None),
            SetFolderDepth(track="ref:g", depth=1),
            AddRegion(name="r", start=30, end=31, ref="reg"),
        )
    )
    g, reg = result.refs["ref:g"], result.refs["ref:reg"]
    s = b.snapshot()
    assert s.tracks[0].guid == g and s.marker(reg).name == "r"
    assert result.results[0].data == {"guid": g}


def test_originals_saved_only_once_across_applies():
    b = FakeBridge(make_snapshot())
    b.apply_plan(plan(SetTrackName(track=KICK, name="one")))
    b.apply_plan(plan(SetTrackName(track=KICK, name="two")))
    assert b.snapshot().track(KICK).agent_ext["reaper_agent:orig_name"] == "Kick In"


def test_external_edit_has_no_undo_entry():
    b = FakeBridge(make_snapshot())
    before = snapshot_hash(b.snapshot())
    b.external_edit(lambda s: setattr(s.tracks[0], "name", "edited in REAPER"))
    assert b.snapshot().tracks[0].name == "edited in REAPER"
    assert snapshot_hash(b.snapshot()) != before
    assert b.undo() is False
