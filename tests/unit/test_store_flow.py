"""PlanStore and the build -> apply -> verify -> undo flow, against the fake bridge."""

from __future__ import annotations

import pytest

from agent.bridge_client.fake import FakeBridge
from agent.core.diff import diff
from agent.core.hashing import snapshot_hash
from agent.core.ops import (
    AddRegion,
    EnsureFxEnvelope,
    EnvPoint,
    InsertEnvelopePoints,
    InsertTrack,
    MoveTrack,
    SetFolderDepth,
    SetRegion,
    SetTrackName,
)
from agent.core.plan import ApplyResult, Plan, PlanError, StalePlanError
from agent.core.store import PlanStore, apply_stored_plan, build_plan, undo_last
from tests.unit.factories import guid, make_snapshot

DRUMS, KICK, SNARE, BASS, VERB = (guid(i) for i in range(1, 6))
FX, REGION = guid(101), guid(401)


def setup():
    return FakeBridge(make_snapshot()), PlanStore()


def test_plan_store_basics():
    store = PlanStore()
    p = Plan.new("a", [SetTrackName(track=KICK, name="x")], "h")
    q = Plan.new("b", [SetTrackName(track=KICK, name="y")], "h")
    store.add(p)
    store.add(q)
    assert store.get(p.id) is p and store.list() == [p, q]
    assert store.discard(p.id) is True and store.discard(p.id) is False
    with pytest.raises(PlanError, match="unknown plan"):
        store.get(p.id)
    assert store.last_applied is None
    assert PlanStore().list() == []  # no state shared between stores


def test_build_plan_fills_hash_and_expected_and_changes_nothing():
    bridge, store = setup()
    before = bridge.snapshot()
    plan = build_plan(bridge, store, "Rename", [SetTrackName(track=KICK, name="DRM Kick")])
    assert plan.base_hash == snapshot_hash(before)
    assert [(c.kind, c.guid, c.field, c.after) for c in plan.expected] == [
        ("track", KICK, "name", "DRM Kick")
    ]
    assert store.get(plan.id) is plan
    assert bridge.snapshot() == before and bridge.undo() is False


def test_build_plan_rejects_bad_plans_and_stores_nothing():
    bridge, store = setup()
    with pytest.raises(PlanError, match="unknown track"):
        build_plan(bridge, store, "x", [SetTrackName(track=guid(999), name="a")])
    with pytest.raises(PlanError, match="used before"):
        build_plan(bridge, store, "x", [SetTrackName(track="ref:nope", name="a")])
    with pytest.raises(PlanError, match="at least one"):
        build_plan(bridge, store, "x", [])
    assert store.list() == []


def test_apply_roundtrip_with_refs_has_no_mismatches_then_undo():
    bridge, store = setup()
    before = bridge.snapshot()
    plan = build_plan(
        bridge,
        store,
        "Group drums",
        [
            InsertTrack(name="BUS Drums", ref="grp", before=DRUMS),
            MoveTrack(track=DRUMS, before=None),
            SetFolderDepth(track="ref:grp", depth=1),
            AddRegion(name="bridge", start=16, end=20, ref="reg"),
            SetRegion(region="ref:reg", name="outro"),
            SetTrackName(track=KICK, name="Kick"),
        ],
    )
    sim_guid = next(c.guid for c in plan.expected if c.field == "added" and c.kind == "track")
    outcome = apply_stored_plan(bridge, store, plan.id)
    assert outcome.result.ok and outcome.mismatches == []
    real = outcome.result.refs["ref:grp"]
    assert real != sim_guid  # the fake mints different GUIDs, so translation was exercised
    assert bridge.snapshot().tracks[0].guid == real
    assert store.last_applied == plan.id

    assert undo_last(bridge, store) is True
    assert store.last_applied is None
    assert bridge.snapshot() == before
    assert undo_last(bridge, store) is False


def test_dry_run_changes_nothing_and_does_not_mark_applied():
    bridge, store = setup()
    before = bridge.snapshot()
    plan = build_plan(bridge, store, "x", [SetTrackName(track=KICK, name="K")])
    outcome = apply_stored_plan(bridge, store, plan.id, dry_run=True)
    assert outcome.result.ok and outcome.result.dry_run and outcome.mismatches == []
    assert bridge.snapshot() == before and store.last_applied is None
    assert undo_last(bridge, store) is False


def test_stale_plan_refused_after_external_edit():
    bridge, store = setup()
    plan = build_plan(bridge, store, "x", [SetTrackName(track=KICK, name="K")])
    bridge.external_edit(lambda s: setattr(s.tracks[3], "name", "user renamed"))
    with pytest.raises(StalePlanError):
        apply_stored_plan(bridge, store, plan.id)
    with pytest.raises(StalePlanError):
        apply_stored_plan(bridge, store, plan.id, dry_run=True)
    assert bridge.snapshot().track(KICK).name == "Kick In"  # nothing was applied
    assert store.last_applied is None


def test_ext_only_external_edit_is_not_stale():
    bridge, store = setup()
    plan = build_plan(bridge, store, "x", [SetTrackName(track=KICK, name="K")])
    bridge.external_edit(lambda s: s.tracks[0].agent_ext.update({"reaper_agent:note": "hi"}))
    assert apply_stored_plan(bridge, store, plan.id).result.ok


def test_unknown_plan_id():
    bridge, store = setup()
    with pytest.raises(PlanError, match="unknown plan"):
        apply_stored_plan(bridge, store, "nope")


def test_real_failure_returns_result_without_mismatch_check_or_last_applied():
    bridge, store = setup()
    plan = build_plan(
        bridge,
        store,
        "x",
        [
            SetTrackName(track=KICK, name="K"),
            EnsureFxEnvelope(track=KICK, fx=FX, param=3),
            InsertEnvelopePoints(track=KICK, fx=FX, param=3, points=[EnvPoint(0.0, 0.1)]),
        ],
    )
    # Make the plan fail on the bridge although the base hash still matches: the bridge-side
    # state of hidden FX data cannot change the snapshot, so instead sabotage the plan itself.
    plan.ops[1] = SetTrackName(track=guid(999), name="boom")
    before = bridge.snapshot()
    outcome = apply_stored_plan(bridge, store, plan.id)
    assert not outcome.result.ok and outcome.result.rolled_back
    assert outcome.mismatches == []
    assert store.last_applied is None
    assert bridge.snapshot() == before
    assert undo_last(bridge, store) is False


def test_failed_apply_keeps_previous_last_applied():
    bridge, store = setup()
    ok = build_plan(bridge, store, "ok", [SetTrackName(track=KICK, name="K")])
    apply_stored_plan(bridge, store, ok.id)
    bad = build_plan(bridge, store, "bad", [SetTrackName(track=SNARE, name="S")])
    bad.ops[0] = SetTrackName(track=guid(999), name="boom")
    assert not apply_stored_plan(bridge, store, bad.id).result.ok
    assert store.last_applied == ok.id


class MisbehavingBridge(FakeBridge):
    """Applies the plan, then also renames the bass track (an unexpected extra change)."""

    def apply_plan(self, plan, *, dry_run=False) -> ApplyResult:
        result = super().apply_plan(plan, dry_run=dry_run)
        if result.ok and not dry_run:
            self.external_edit(lambda s: setattr(s.track(BASS), "name", "surprise"))
        return result


class DroppingBridge(FakeBridge):
    """Reports success but silently skips the plan."""

    def apply_plan(self, plan, *, dry_run=False) -> ApplyResult:
        result = super().apply_plan(plan, dry_run=dry_run)
        if result.ok and not dry_run:
            self.undo()
        return result


def test_unexpected_change_is_reported_as_mismatch():
    bridge, store = MisbehavingBridge(make_snapshot()), PlanStore()
    plan = build_plan(bridge, store, "x", [SetTrackName(track=KICK, name="K")])
    outcome = apply_stored_plan(bridge, store, plan.id)
    assert outcome.result.ok and store.last_applied == plan.id
    # before = what the plan expected (unchanged), after = what actually happened
    assert [(c.kind, c.guid, c.field, c.before, c.after) for c in outcome.mismatches] == [
        ("track", BASS, "name", "Bass DI", "surprise")
    ]


def test_missing_change_is_reported_as_mismatch():
    bridge, store = DroppingBridge(make_snapshot()), PlanStore()
    plan = build_plan(bridge, store, "x", [SetTrackName(track=KICK, name="K")])
    outcome = apply_stored_plan(bridge, store, plan.id)
    assert [(c.guid, c.field, c.before, c.after) for c in outcome.mismatches] == [
        (KICK, "name", "K", "Kick In")
    ]


def test_expected_matches_actual_diff_for_a_mixed_plan():
    bridge, store = setup()
    pre = bridge.snapshot()
    plan = build_plan(
        bridge,
        store,
        "mixed",
        [
            InsertTrack(name="A", ref="a", before=SNARE),
            EnsureFxEnvelope(track=KICK, fx=FX, param=9),
            InsertEnvelopePoints(track=KICK, fx=FX, param=9, points=[EnvPoint(0.0, 0.1)]),
        ],
    )
    outcome = apply_stored_plan(bridge, store, plan.id)
    assert outcome.mismatches == []
    fields = {(c.kind, c.field) for c in diff(pre, bridge.snapshot())}
    assert ("track", "added") in fields and ("track", "envelopes") in fields


def test_undo_refuses_when_newest_step_is_not_ours():
    from agent.bridge_client.fake import FakeBridge
    from agent.core.hashing import snapshot_hash
    from agent.core.ops import SetTrackName
    from agent.core.plan import Plan
    from tests.unit.factories import guid, make_snapshot

    bridge = FakeBridge(make_snapshot())
    base = bridge.snapshot()
    plan = Plan.new(
        "Rename drums", [SetTrackName(track=guid(1), name="Drumz")], snapshot_hash(base)
    )
    assert bridge.apply_plan(plan).ok
    assert bridge.undo(expect_label="Some other step") is False  # nothing reverted
    assert bridge.snapshot().track(guid(1)).name == "Drumz"
    assert bridge.undo(expect_label="Rename drums") is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base)


def test_verification_tolerates_reaper_lane_padding_and_envelope_point_counts():
    from agent.core.models import Change
    from agent.core.store import _compare

    padded = [Change("track", "{T}", "lane_names", [], ["", ""])]
    named = [Change("track", "{T}", "lane_names", [], ["", "Take 2"])]
    assert _compare(padded, [Change("track", "{T}", "lane_names", [], ["", ""])]) == []
    assert _compare(named, [Change("track", "{T}", "lane_names", [], ["", "Take 2", ""])]) == []
    assert _compare(named, [Change("track", "{T}", "lane_names", [], ["", "Other"])]) != []

    env = lambda n: [{"kind": "fx", "param": 1, "point_count": n, "active": True}]
    assert _compare([Change("track", "{T}", "envelopes", [], env(0))],
                    [Change("track", "{T}", "envelopes", [], env(1))]) == []  # fmt: skip
