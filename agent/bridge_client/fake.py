"""In-memory Bridge for unit tests: applies plans with `agent.core.simulate`, no REAPER needed.

Differences from the real bridge worth knowing: the undo stack restores whole snapshots (so it
also reverts `external_edit`s made before the undone apply), and new objects get GUIDs from a
`{BB1D0000-...}` counter, distinct from the simulation default, so GUID translation in the
verify step is really exercised.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from agent.core import simulate
from agent.core.models import Snapshot
from agent.core.plan import ApplyResult, OpResult, Plan
from agent.core.simulate import GuidCounter, HiddenState


class FakeBridge:
    def __init__(self, snapshot: Snapshot) -> None:
        self._snapshot = copy.deepcopy(snapshot)
        self._hidden = HiddenState()
        self._undo: list[tuple[Snapshot, HiddenState]] = []
        self._guids = GuidCounter(head="BB1D0000")

    # FX parameter values and envelope points are not part of the Snapshot; see HiddenState.
    @property
    def fx_params(self) -> dict[tuple[str, int], float]:
        return self._hidden.fx_params

    @property
    def env_points(self) -> dict[tuple[str, int], list]:
        return self._hidden.env_points

    def status(self) -> dict[str, Any]:
        return {
            "alive": True,
            "fake": True,
            "reaper_version": self._snapshot.project.reaper_version,
            "project": self._snapshot.project.name,
        }

    def snapshot(self) -> Snapshot:
        return copy.deepcopy(self._snapshot)

    def apply_plan(self, plan: Plan, *, dry_run: bool = False) -> ApplyResult:
        # A dry run must not consume GUIDs or touch hidden state, so it simulates on copies.
        counter = copy.copy(self._guids)
        saved = (copy.deepcopy(self._snapshot), self._hidden.copy())
        hidden = self._hidden.copy() if dry_run else self._hidden  # updated only on success
        try:
            sim = simulate.run_ops(self._snapshot, plan.ops, counter, hidden)
        except simulate.OpFailed as exc:
            results = [
                OpResult(i, plan.ops[i].type, True, data={"dry_run": dry_run} if dry_run else {})
                for i in range(exc.index)
            ]
            results.append(OpResult(exc.index, exc.op_type, False, error=exc.reason))
            return ApplyResult(
                ok=False,
                plan_id=plan.id,
                results=results,
                rolled_back=not dry_run,
                dry_run=dry_run,
                error=str(exc),
            )
        if dry_run:
            results = [
                OpResult(i, o.type, True, data={"dry_run": True}) for i, o in enumerate(plan.ops)
            ]
            return ApplyResult(True, plan.id, results, dict(sim.refs), dry_run=True)
        self._undo.append(saved)
        self._guids = counter
        self._snapshot = sim.snapshot
        results = [
            OpResult(i, o.type, True, data=d) for i, (o, d) in enumerate(zip(plan.ops, sim.op_data))
        ]
        return ApplyResult(True, plan.id, results, dict(sim.refs))

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._snapshot, saved = self._undo.pop()
        self._hidden.assign(saved)
        return True

    def external_edit(self, fn: Callable[[Snapshot], Any]) -> Any:
        """Test helper: mutate the snapshot in place as if the user edited REAPER (no undo entry)."""
        return fn(self._snapshot)
