"""Stub bridge, plan store and store API for server/tool tests (no dependency on task 1.1)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agent.bridge_client.protocol import BridgeError
from agent.core.hashing import snapshot_hash
from agent.core.models import Change, Snapshot
from agent.core.plan import ApplyResult, OpResult, Plan, PlanError, StalePlanError
from agent.core.store import ApplyOutcome
from tests.unit.factories import make_snapshot


class StubBridge:
    def __init__(self, snapshot: Snapshot | None = None) -> None:
        self.snap = snapshot or make_snapshot()
        self.applied: list[Plan] = []
        self.undos = 0
        self.alive = True
        self.fail_ops = False

    def status(self) -> dict[str, Any]:
        if not self.alive:
            raise BridgeError("bridge not reachable")
        return {"alive": True, "reaper_version": "7.86"}

    def snapshot(self) -> Snapshot:
        if not self.alive:
            raise BridgeError("bridge not reachable")
        return self.snap

    def apply_plan(self, plan: Plan, *, dry_run: bool = False) -> ApplyResult:
        results = [
            OpResult(
                index=i, type=o.type, ok=not self.fail_ops, error="boom" if self.fail_ops else None
            )
            for i, o in enumerate(plan.ops)
        ]
        if self.fail_ops:
            return ApplyResult(
                ok=False, plan_id=plan.id, results=results, rolled_back=True, error="op failed"
            )
        if not dry_run:
            self.applied.append(plan)
            for o in plan.ops:
                if o.type == "set_track_name":
                    self.snap.track(o.track).name = o.name  # type: ignore[union-attr]
        return ApplyResult(ok=True, plan_id=plan.id, results=results, dry_run=dry_run)

    def undo(self) -> bool:
        self.undos += 1
        return True


class StubStore:
    def __init__(self) -> None:
        self.plans: dict[str, Plan] = {}
        self.last_applied: str | None = None

    def add(self, plan: Plan) -> None:
        self.plans[plan.id] = plan

    def get(self, plan_id: str) -> Plan:
        if plan_id not in self.plans:
            raise PlanError(f"unknown plan {plan_id!r}")
        return self.plans[plan_id]

    def discard(self, plan_id: str) -> bool:
        return self.plans.pop(plan_id, None) is not None

    def list(self) -> list[Plan]:
        return list(self.plans.values())


def _build_plan(bridge, store, summary, ops):
    snap = bridge.snapshot()
    for o in ops:
        if o.type == "set_track_name" and snap.track(o.track) is None:
            raise PlanError(f"unknown track {o.track}")
    plan = Plan.new(summary, ops, snapshot_hash(snap))
    for o in ops:
        if o.type == "set_track_name":
            before = snap.track(o.track).name
            plan.expected.append(Change("track", o.track, "name", before, o.name))
    store.add(plan)
    return plan


def _apply_stored_plan(bridge, store, plan_id, *, dry_run=False):
    plan = store.get(plan_id)
    if snapshot_hash(bridge.snapshot()) != plan.base_hash:
        raise StalePlanError("base hash mismatch")
    result = bridge.apply_plan(plan, dry_run=dry_run)
    if result.ok and not dry_run:
        store.last_applied = plan.id
    return ApplyOutcome(result=result)


def _undo_last(bridge, store):
    if store.last_applied is None:
        return False
    store.last_applied = None
    return bridge.undo()


STORE_API = SimpleNamespace(
    build_plan=_build_plan, apply_stored_plan=_apply_stored_plan, undo_last=_undo_last
)


def call(server, name: str, arguments: dict[str, Any] | None = None, mid: int = 1) -> dict:
    """Send tools/call through the JSON round trip and return the result dict."""
    msg = {"jsonrpc": "2.0", "id": mid, "method": "tools/call"}
    msg["params"] = {"name": name, "arguments": arguments or {}}
    resp = server.handle_message(json.loads(json.dumps(msg)))
    assert resp is not None and "result" in resp, resp
    return resp["result"]


def text_of(result: dict) -> str:
    return result["content"][0]["text"]
