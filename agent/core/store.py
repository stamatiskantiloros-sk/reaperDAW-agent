"""Plan store and the plan -> apply -> verify flow. INTERFACE FROZEN, bodies owned by task 1.1.

`build_plan` and `apply_stored_plan` are the only places that implement the safety rules:
stale-plan check (CLAUDE.md rule 6) and verify-after-apply. Tools call these, never the bridge
directly, for anything that mutates the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.bridge_client.protocol import Bridge
from agent.core import diff as diff_mod
from agent.core import simulate
from agent.core.hashing import snapshot_hash
from agent.core.models import Change
from agent.core.ops import Op
from agent.core.plan import ApplyResult, Plan, PlanError, StalePlanError


@dataclass
class ApplyOutcome:
    result: ApplyResult
    # Differences between the plan's expected post-state and a fresh snapshot taken after the
    # apply. Empty means the apply did exactly what the plan said. Always empty for dry runs.
    mismatches: list[Change] = field(default_factory=list)


class PlanStore:
    """In-memory store of plans created in this server session."""

    last_applied: str | None = None  # plan id of the most recent successful real apply

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self.last_applied = None

    def add(self, plan: Plan) -> None:
        self._plans[plan.id] = plan

    def get(self, plan_id: str) -> Plan:
        """Return the plan or raise PlanError('unknown plan ...')."""
        try:
            return self._plans[plan_id]
        except KeyError:
            raise PlanError(f"unknown plan {plan_id!r}") from None

    def discard(self, plan_id: str) -> bool:
        return self._plans.pop(plan_id, None) is not None

    def list(self) -> list[Plan]:
        return list(self._plans.values())


def build_plan(bridge: Bridge, store: PlanStore, summary: str, ops: list[Op]) -> Plan:
    """Snapshot the project, hash it, simulate the ops on a copy to fill `Plan.expected`, store
    and return the plan. Raises PlanError if the ops are invalid or cannot apply to this
    snapshot (unknown GUID, etc.). Never changes the project."""
    snapshot = bridge.snapshot()
    plan = Plan.new(summary, ops, snapshot_hash(snapshot))
    simulated, _ = simulate.apply_ops(snapshot, plan.ops)
    plan.expected = diff_mod.diff(snapshot, simulated)
    store.add(plan)
    return plan


def _translate(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_translate(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _translate(v, mapping) for k, v in value.items()}
    return value


def _without_point_counts(value: Any) -> Any:
    """Envelope point counts are not predictable (the snapshot does not list existing points),
    so verification compares envelope existence, activity and visibility only. Point content is
    verified by reading the envelope back (Phase 4)."""
    if isinstance(value, list):
        return [_without_point_counts(v) for v in value]
    if isinstance(value, dict):
        return {k: _without_point_counts(v) for k, v in value.items() if k != "point_count"}
    return value


def _loosen(changes: list[Change]) -> list[Change]:
    out = []
    for c in changes:
        if c.field == "envelopes":
            c = Change(
                c.kind,
                c.guid,
                c.field,
                _without_point_counts(c.before),
                _without_point_counts(c.after),
            )
            if c.before == c.after:
                continue
        out.append(c)
    return out


def _compare(expected: list[Change], actual: list[Change]) -> list[Change]:
    """Differences between two change lists. A mismatch has before = the value the plan expected
    the field to end with and after = the value it actually ended with."""
    expected, actual = _loosen(expected), _loosen(actual)
    exp = {(c.kind, c.guid, c.field): c for c in expected}
    act = {(c.kind, c.guid, c.field): c for c in actual}
    out: list[Change] = []
    for key in exp.keys() | act.keys():
        e, a = exp.get(key), act.get(key)
        want = e.after if e else a.before  # type: ignore[union-attr]
        got = a.after if a else e.before  # type: ignore[union-attr]
        if want != got:
            out.append(Change(key[0], key[1], key[2], want, got))
    return diff_mod.sort_changes(out)


def apply_stored_plan(
    bridge: Bridge, store: PlanStore, plan_id: str, *, dry_run: bool = False
) -> ApplyOutcome:
    """Re-snapshot and refuse with StalePlanError if the hash differs from plan.base_hash; else
    call bridge.apply_plan. After a real, successful apply take a fresh snapshot and diff it
    against plan.expected, returning any differences as `mismatches`. Marks the plan applied
    (store.last_applied) only on a real success."""
    plan = store.get(plan_id)
    before = bridge.snapshot()
    if snapshot_hash(before) != plan.base_hash:
        raise StalePlanError(f"plan {plan_id} is stale: the project changed since it was planned")
    result = bridge.apply_plan(plan, dry_run=dry_run)
    if dry_run or not result.ok:
        return ApplyOutcome(result)
    # Simulated GUIDs of newly created objects -> the real ones the bridge reported.
    try:
        _, sim_refs = simulate.apply_ops(before, plan.ops)
    except PlanError:
        sim_refs = {}
    mapping = {sim_refs[a]: g for a, g in result.refs.items() if a in sim_refs}
    expected = [
        Change(
            c.kind,
            _translate(c.guid, mapping),
            c.field,
            _translate(c.before, mapping),
            _translate(c.after, mapping),
        )
        for c in plan.expected
    ]
    actual = diff_mod.diff(before, bridge.snapshot())
    store.last_applied = plan.id
    return ApplyOutcome(result, _compare(expected, actual))


def undo_last(bridge: Bridge, store: PlanStore) -> bool:
    """Undo one REAPER undo step if store.last_applied is set, then clear it. Returns False if
    there was nothing of ours to undo."""
    if store.last_applied is None:
        return False
    store.last_applied = None
    return bridge.undo()
