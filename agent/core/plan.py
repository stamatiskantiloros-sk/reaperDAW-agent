"""Plan and apply-result models. FROZEN CONTRACT.

A Plan is an ordered list of atomic ops plus a human summary and the hash of the snapshot it was
computed from. It changes nothing by itself; `Bridge.apply_plan` executes it as ONE undo step.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent.core import serde
from agent.core.models import Change
from agent.core.ops import Op, OpError, op_from_dict


class PlanError(ValueError):
    """Raised when a plan is malformed or cannot be applied as it stands."""


class StalePlanError(PlanError):
    """The project changed since the plan's base snapshot was taken; re-plan."""


@dataclass
class Plan:
    id: str
    created_at: str  # UTC ISO-8601
    summary: str  # becomes the REAPER undo-step name
    base_hash: str  # hash of the snapshot the plan was computed from (hashing.snapshot_hash)
    ops: list[Op] = field(default_factory=list)
    expected: list[Change] = field(default_factory=list)  # filled by the fake-bridge simulation

    @classmethod
    def new(cls, summary: str, ops: list[Op], base_hash: str) -> Plan:
        plan = cls(
            id=uuid.uuid4().hex[:12],
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary=summary,
            base_hash=base_hash,
            ops=list(ops),
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        if not self.summary or len(self.summary) > 200:
            raise PlanError("summary must be 1-200 characters")
        if not self.ops:
            raise PlanError("a plan needs at least one op")
        defined: set[str] = set()
        for i, o in enumerate(self.ops):
            try:
                o.validate()
            except OpError as exc:
                raise PlanError(f"op {i} ({o.type}): {exc}") from exc
            for ref in o.used_refs():
                if ref not in defined:
                    raise PlanError(f"op {i} ({o.type}): {ref} is used before it is defined")
            new = o.defined_ref()
            if new is not None:
                if new in defined:
                    raise PlanError(f"op {i} ({o.type}): {new} is defined twice")
                defined.add(new)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "summary": self.summary,
            "base_hash": self.base_hash,
            "ops": [o.to_dict() for o in self.ops],
            "expected": serde.to_dict(self.expected),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        extra = set(data) - {"id", "created_at", "summary", "base_hash", "ops", "expected"}
        if extra:
            raise PlanError(f"unknown plan field(s) {sorted(extra)}")
        try:
            plan = cls(
                id=data["id"],
                created_at=data["created_at"],
                summary=data["summary"],
                base_hash=data["base_hash"],
                ops=[op_from_dict(o) for o in data["ops"]],
                expected=serde.from_dict(list[Change], data.get("expected", [])),
            )
        except (KeyError, OpError, ValueError) as exc:
            raise PlanError(str(exc)) from exc
        plan.validate()
        return plan


@dataclass
class OpResult:
    index: int  # position in Plan.ops
    type: str
    ok: bool
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplyResult:
    ok: bool
    plan_id: str
    results: list[OpResult] = field(default_factory=list)
    refs: dict[str, str] = field(default_factory=dict)  # "ref:alias" -> real GUID
    rolled_back: bool = False  # True if a failure was reverted automatically
    dry_run: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return serde.to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplyResult:
        return serde.from_dict(cls, data)
