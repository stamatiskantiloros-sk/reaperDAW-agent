"""Plan store and the plan -> apply -> verify flow. INTERFACE FROZEN, bodies owned by task 1.1.

`build_plan` and `apply_stored_plan` are the only places that implement the safety rules:
stale-plan check (CLAUDE.md rule 6) and verify-after-apply. Tools call these, never the bridge
directly, for anything that mutates the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.bridge_client.protocol import Bridge
from agent.core.models import Change
from agent.core.ops import Op
from agent.core.plan import ApplyResult, Plan


@dataclass
class ApplyOutcome:
    result: ApplyResult
    # Differences between the plan's expected post-state and a fresh snapshot taken after the
    # apply. Empty means the apply did exactly what the plan said. Always empty for dry runs.
    mismatches: list[Change] = field(default_factory=list)


class PlanStore:
    """In-memory store of plans created in this server session."""

    last_applied: str | None = None  # plan id of the most recent successful real apply

    def add(self, plan: Plan) -> None:
        raise NotImplementedError

    def get(self, plan_id: str) -> Plan:
        """Return the plan or raise PlanError('unknown plan ...')."""
        raise NotImplementedError

    def discard(self, plan_id: str) -> bool:
        raise NotImplementedError

    def list(self) -> list[Plan]:
        raise NotImplementedError


def build_plan(bridge: Bridge, store: PlanStore, summary: str, ops: list[Op]) -> Plan:
    """Snapshot the project, hash it, simulate the ops on a copy to fill `Plan.expected`, store
    and return the plan. Raises PlanError if the ops are invalid or cannot apply to this
    snapshot (unknown GUID, etc.). Never changes the project."""
    raise NotImplementedError


def apply_stored_plan(
    bridge: Bridge, store: PlanStore, plan_id: str, *, dry_run: bool = False
) -> ApplyOutcome:
    """Re-snapshot and refuse with StalePlanError if the hash differs from plan.base_hash; else
    call bridge.apply_plan. After a real, successful apply take a fresh snapshot and diff it
    against plan.expected, returning any differences as `mismatches`. Marks the plan applied
    (store.last_applied) only on a real success."""
    raise NotImplementedError


def undo_last(bridge: Bridge, store: PlanStore) -> bool:
    """Undo one REAPER undo step if store.last_applied is set, then clear it. Returns False if
    there was nothing of ours to undo."""
    raise NotImplementedError
