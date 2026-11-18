"""Apply -> undo -> identical snapshot, for every op in the vocabulary, on the fake bridge."""

from __future__ import annotations

import pytest

from agent.core import hashing
from agent.core.ops import OPS, SetItemLane, SetLaneName, SetTrackLaneMode, op_from_dict
from agent.core.plan import Plan
from tests.unit.factories import guid, make_snapshot
from tests.unit.test_core_contract import EXAMPLES

# EXAMPLES use drum/fixture GUIDs; remap them onto objects that exist in make_snapshot().
# guid(1) (Drums folder, no items) -> guid(2) (Kick In: has an item, a take, FX 101, an envelope).
# guid(2) -> guid(5) so MoveTrack does not move a track before itself.
GUID_MAP = {guid(1): guid(2), guid(2): guid(5)}
LANE_OPS = {SetItemLane, SetLaneName}


def _remap(example) -> dict:
    data = example.to_dict()

    def fix(value):
        if isinstance(value, str):
            return GUID_MAP.get(value, value)
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value

    return fix(data)


def _ops_for(example):
    """The example op, preceded by a lane-mode switch when it needs fixed lanes."""
    ops = []
    if type(example) in LANE_OPS:
        # Same plan, earlier op: the track is in fixed-lane mode before the lane op runs.
        ops.append(SetTrackLaneMode(track=guid(2), mode="fixed_lanes"))
    ops.append(op_from_dict(_remap(example)))
    return ops


@pytest.mark.parametrize("op_type", sorted(OPS))
def test_apply_then_undo_restores_snapshot_hash(op_type):
    try:
        from agent.bridge_client.fake import FakeBridge
    except ImportError:
        pytest.skip("fake bridge not merged yet")

    example = next(e for e in EXAMPLES if type(e).type == op_type)
    snapshot = make_snapshot()
    original_hash = hashing.snapshot_hash(snapshot)
    bridge = FakeBridge(snapshot)

    plan = Plan.new(f"roundtrip {op_type}", _ops_for(example), base_hash=original_hash)
    result = bridge.apply_plan(plan)
    assert result.ok, result.error

    assert bridge.undo() is True
    assert hashing.snapshot_hash(bridge.snapshot()) == original_hash
