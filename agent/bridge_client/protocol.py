"""Bridge interface. FROZEN CONTRACT.

Two implementations: the real one (daemon.py, talks to REAPER through the Reaper Daemon file
bridge) and the in-memory fake (fake.py) used by unit tests. Tools and plan code depend only on
this Protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent.core.models import Snapshot
from agent.core.plan import ApplyResult, Plan


class BridgeError(RuntimeError):
    """The bridge could not be reached or returned an unusable reply."""


@runtime_checkable
class Bridge(Protocol):
    def status(self) -> dict[str, Any]:
        """Liveness info, e.g. {"alive": True, "reaper_version": "7.86", "project": "x.rpp"}."""
        ...

    def snapshot(self) -> Snapshot:
        """Read the whole project in one round trip."""
        ...

    def apply_plan(self, plan: Plan, *, dry_run: bool = False) -> ApplyResult:
        """Execute the plan as one undo step named after plan.summary.

        With dry_run=True nothing changes; the result reports what would run. On an op error the
        bridge stops, reverts everything the plan did if it can (rolled_back=True) and reports
        the failing op. The caller (not the bridge) performs the stale-plan hash check.
        """
        ...

    def undo(self, expect_label: str | None = None) -> bool:
        """Undo exactly one REAPER undo step. Returns False if there was nothing to undo.

        If expect_label is given, undo only when the newest undo step has that name (the plan
        summary), so a plan that created no undo point never causes the user's earlier edit to
        be undone."""
        ...
