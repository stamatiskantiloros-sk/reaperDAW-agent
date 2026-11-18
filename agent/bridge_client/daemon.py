"""Real bridge: talks to REAPER through the vendored Reaper Daemon file bridge.

Uses upstream's in-process `reaperd.send_type` (stdlib only) to reach our Lua handlers
(`agent.snapshot`, `agent.apply_plan`, `agent.undo`, `agent.ping`), registered through the
extra-handlers hook (see patches/0001-extra-handlers.patch and scripts/install_agent.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agent.bridge_client.protocol import BridgeError
from agent.core.models import Snapshot
from agent.core.plan import ApplyResult, Plan

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "upstream"


class DaemonBridge:
    def __init__(self, root: str | os.PathLike[str] | None = None, timeout_ms: int = 30000):
        self.root = str(root or os.environ.get("REAPER_DAEMON_ROOT") or DEFAULT_ROOT)
        self.timeout_ms = timeout_ms
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        try:
            import reaperd
        except ImportError as exc:
            raise BridgeError(f"cannot import reaperd from {self.root}: {exc}") from exc
        self._reaperd = reaperd

    def _send(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reply = self._reaperd.send_type(
            command, payload or {}, bridge_root=self.root, timeout_ms=self.timeout_ms
        )
        if not reply.get("ok"):
            error = reply.get("error")
            raise BridgeError(f"{command} failed: {error}")
        return reply.get("data") or {}

    def status(self) -> dict[str, Any]:
        info = self._send("agent.ping")
        return {"alive": True, **info}

    def snapshot(self) -> Snapshot:
        return Snapshot.from_dict(self._send("agent.snapshot"))

    def apply_plan(self, plan: Plan, *, dry_run: bool = False) -> ApplyResult:
        if dry_run:
            # Dry runs never reach REAPER: simulate on a fresh snapshot instead.
            from agent.core import simulate
            from agent.core.plan import OpResult, PlanError

            try:
                _, refs = simulate.apply_ops(self.snapshot(), plan.ops)
            except PlanError as exc:
                return ApplyResult(ok=False, plan_id=plan.id, dry_run=True, error=str(exc))
            results = [
                OpResult(index=i, type=o.type, ok=True, data={"dry_run": True})
                for i, o in enumerate(plan.ops)
            ]
            return ApplyResult(ok=True, plan_id=plan.id, results=results, refs=refs, dry_run=True)
        body = plan.to_dict()
        body.pop("expected", None)
        return ApplyResult.from_dict(self._send("agent.apply_plan", {"plan": body}))

    def undo(self, expect_label: str | None = None) -> bool:
        payload = {"expect_label": expect_label} if expect_label is not None else {}
        return bool(self._send("agent.undo", payload).get("undone"))
