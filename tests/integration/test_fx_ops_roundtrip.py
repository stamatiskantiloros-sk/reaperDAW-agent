"""apply -> undo -> identical snapshot, for the FX parameter and envelope ops, on a live REAPER.

FX parameter values and envelope point values are not part of the snapshot, so they are checked
through op result data and the bridge's own get_fx_param_automation command.
"""

from __future__ import annotations

import pytest

from agent.core.hashing import snapshot_hash
from agent.core.ops import (
    ClearEnvelopeRange,
    EnsureFxEnvelope,
    EnvPoint,
    InsertEnvelopePoints,
    SetFxParam,
)
from agent.core.plan import Plan

pytestmark = pytest.mark.integration

DEAD = "{DEADBEEF-0000-0000-0000-000000000000}"
PARAM = 1  # a ReaEQ parameter with a continuous range


@pytest.fixture(scope="module")
def eq(bridge, project):
    """Snapshot, track GUID and ReaEQ GUID; ReaEQ is added once through the daemon's add_fx.

    The project fixture is shared by all integration modules, so the added FX is undone again
    at teardown.
    """
    track = next(t for t in project.tracks if t.name == "Snare Top")
    assert not track.fx, "fixture track unexpectedly has FX already"
    bridge._send("add_fx", {"target_track_guid": track.guid, "fx_name": "ReaEQ"})
    base = bridge.snapshot()
    track = base.track(track.guid)
    assert track.fx, "ReaEQ was not added"
    yield base, track.guid, track.fx[0].guid
    assert bridge.undo() is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(project)


def _plan(summary, ops, base):
    return Plan.new(summary, ops, snapshot_hash(base))


def _points(bridge, track, fx, param):
    reply = bridge._send(
        "get_fx_param_automation",
        {"target_track_guid": track, "fx_guid": fx, "param_index": param},
    )
    return reply["envelope"].get("exists", False), reply["points"]


def test_set_fx_param_roundtrip(bridge, eq):
    base, track, fx = eq
    first = bridge.apply_plan(
        _plan("IT fx param", [SetFxParam(track=track, fx=fx, param=PARAM, normalized=0.8)], base)
    )
    assert first.ok, first.error
    data = first.results[0].data
    original = data["before"]
    assert abs(data["after"] - 0.8) < 0.01
    after = bridge.snapshot()
    key = f"reaper_agent:orig_fxparam:{fx}:{PARAM}"
    assert float(after.track(track).agent_ext[key]) == pytest.approx(original, abs=1e-12)
    assert snapshot_hash(after) == snapshot_hash(base)
    assert bridge.undo() is True
    assert after.track(track).agent_ext != bridge.snapshot().track(track).agent_ext
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base)
    # Undo restored the parameter: a second apply must report the same "before".
    second = bridge.apply_plan(
        _plan("IT fx param 2", [SetFxParam(track=track, fx=fx, param=PARAM, normalized=0.3)], base)
    )
    assert second.ok, second.error
    assert second.results[0].data["before"] == pytest.approx(original, abs=1e-9)
    # The original is kept from the first change only.
    third = bridge.apply_plan(
        _plan("IT fx param 3", [SetFxParam(track=track, fx=fx, param=PARAM, normalized=0.4)], base)
    )
    assert third.ok
    saved = bridge.snapshot().track(track).agent_ext[key]
    assert float(saved) == pytest.approx(original, abs=1e-12)
    assert bridge.undo() is True and bridge.undo() is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base)


def test_envelope_chain_ensure_insert_clear(bridge, eq):
    base, track, fx = eq
    assert _points(bridge, track, fx, PARAM) == (False, [])
    points = [
        EnvPoint(time=1.0, value=0.2),
        EnvPoint(time=2.0, value=0.4, shape="square"),
        EnvPoint(time=3.0, value=0.6, shape="bezier"),
        EnvPoint(time=4.0, value=0.8, shape="fast_end"),
    ]
    plan = _plan(
        "IT envelope chain",
        [
            EnsureFxEnvelope(track=track, fx=fx, param=PARAM),
            EnsureFxEnvelope(track=track, fx=fx, param=PARAM),  # no-op the second time
            InsertEnvelopePoints(track=track, fx=fx, param=PARAM, points=points),
            ClearEnvelopeRange(track=track, fx=fx, param=PARAM, start=1.5, end=3.5),
        ],
        base,
    )
    result = bridge.apply_plan(plan)
    assert result.ok, result.error
    assert [r.data.get("created") for r in result.results[:2]] == [True, False]
    assert result.results[2].data["inserted"] == 4
    assert result.results[3].data["removed"] == 2
    after = bridge.snapshot()
    env = next(e for e in after.track(track).envelopes if e.kind == "fx")
    assert (env.fx, env.param, env.active, env.visible, env.point_count) == (
        fx,
        PARAM,
        True,
        True,
        3,  # REAPER adds a default point at t=0 when it creates an FX envelope
    )
    exists, live = _points(bridge, track, fx, PARAM)
    assert exists and live[0]["time"] == 0.0
    assert [(p["time"], p["value"], p["shape"]) for p in live[1:]] == [
        (1.0, 0.2, 0),
        (4.0, 0.8, 4),
    ]
    assert bridge.undo() is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base)
    assert _points(bridge, track, fx, PARAM) == (False, [])


def test_insert_points_maps_shapes(bridge, eq):
    base, track, fx = eq
    shapes = ["linear", "square", "slow", "fast_start", "fast_end", "bezier"]
    points = [EnvPoint(time=float(i + 1), value=0.1 * i, shape=s) for i, s in enumerate(shapes)]
    result = bridge.apply_plan(
        _plan(
            "IT shapes",
            [
                EnsureFxEnvelope(track=track, fx=fx, param=PARAM),
                InsertEnvelopePoints(track=track, fx=fx, param=PARAM, points=points),
            ],
            base,
        )
    )
    assert result.ok, result.error
    _, live = _points(bridge, track, fx, PARAM)
    live = live[1:]  # skip REAPER's default point at t=0
    assert [p["shape"] for p in live] == [0, 1, 2, 3, 4, 5]
    assert [p["time"] for p in live] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert bridge.undo() is True
    assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base)


def test_missing_envelope_and_bad_fx_roll_back(bridge, eq):
    base, track, fx = eq
    cases = [
        (
            "insert without envelope",
            [InsertEnvelopePoints(track=track, fx=fx, param=PARAM, points=[EnvPoint(1.0, 0.5)])],
            "does not exist",
        ),
        (
            "clear without envelope",
            [ClearEnvelopeRange(track=track, fx=fx, param=PARAM, start=0.0, end=1.0)],
            "does not exist",
        ),
        (
            "set param on foreign FX",
            [
                EnsureFxEnvelope(track=track, fx=fx, param=PARAM),
                SetFxParam(track=track, fx=DEAD, param=PARAM, normalized=0.5),
            ],
            "not on this track",
        ),
        (
            "param out of range",
            [SetFxParam(track=track, fx=fx, param=99999, normalized=0.5)],
            "no parameter",
        ),
    ]
    for summary, ops, message in cases:
        result = bridge.apply_plan(_plan("IT " + summary, ops, base))
        assert not result.ok, summary
        assert message in result.error, (summary, result.error)
        assert snapshot_hash(bridge.snapshot()) == snapshot_hash(base), summary
        assert _points(bridge, track, fx, PARAM)[0] is False, summary
