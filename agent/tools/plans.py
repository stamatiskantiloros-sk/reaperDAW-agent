"""Plan tools. Everything that changes the project goes through the store functions."""

from __future__ import annotations

import json
from typing import Any

from agent.bridge_client.protocol import BridgeError
from agent.core import serde
from agent.core.ops import OpError, SetTrackName
from agent.core.plan import Plan, PlanError, StalePlanError
from agent.tools.registry import Context, Tool, ToolError


def format_changes(changes: list[Any]) -> str:
    """Readable change list: agent.core.diff.format_changes when it exists, else raw dicts."""
    if not changes:
        return "(none)"
    try:
        from agent.core.diff import format_changes as fmt
    except ImportError:
        fmt = None
    if fmt is not None:
        text = fmt(changes)
        return text if isinstance(text, str) else "\n".join(str(t) for t in text)
    return "\n".join(
        json.dumps(serde.to_dict(c), sort_keys=True, separators=(",", ":")) for c in changes
    )


def describe_plan(plan: Plan, *, with_ops: bool = False) -> str:
    lines = [
        f"Plan {plan.id}: {plan.summary}",
        f"Base hash: {plan.base_hash}",
        f"Created: {plan.created_at}",
        "Expected changes:",
        format_changes(plan.expected),
    ]
    if with_ops:
        lines.append("Ops:")
        lines += [json.dumps(o.to_dict(), separators=(",", ":")) for o in plan.ops]
    return "\n".join(lines)


def plan_rename_track(ctx: Context, args: dict[str, Any]) -> str:
    op = SetTrackName(track=args["track"], name=args["new_name"])
    try:
        op.validate()
        summary = f"Rename track to {args['new_name']!r}"[:200]
        plan = ctx.store_api.build_plan(ctx.bridge, ctx.store, summary, [op])
    except (PlanError, OpError) as exc:
        raise ToolError(f"cannot plan: {exc}") from exc
    except BridgeError as exc:
        raise ToolError(f"bridge error: {exc}") from exc
    return (
        describe_plan(plan) + f"\nNothing has changed yet. Call apply_plan with plan_id {plan.id} "
        "(dry_run=true to preview)."
    )


def apply_plan(ctx: Context, args: dict[str, Any]) -> str:
    plan_id = args["plan_id"]
    dry_run = args.get("dry_run", False)
    try:
        outcome = ctx.store_api.apply_stored_plan(ctx.bridge, ctx.store, plan_id, dry_run=dry_run)
    except StalePlanError as exc:
        raise ToolError(
            f"Plan {plan_id} is stale: the project changed since it was made ({exc}). "
            "Nothing was applied. Re-plan from the current project."
        ) from exc
    except PlanError as exc:
        raise ToolError(f"cannot apply plan {plan_id}: {exc}") from exc
    except BridgeError as exc:
        raise ToolError(f"bridge error: {exc}") from exc
    res = outcome.result
    if not res.ok:
        failed = next((r for r in res.results if not r.ok), None)
        where = f" at op {failed.index} ({failed.type}): {failed.error}" if failed else ""
        tail = "Changes were rolled back." if res.rolled_back else "Check the project; use undo."
        raise ToolError(
            f"Plan {plan_id} failed{where}. {res.error or ''} {tail}".replace("  ", " ")
        )
    if res.dry_run or dry_run:
        return f"Dry run of plan {plan_id}: {len(res.results)} op(s) would run. Nothing changed."
    text = f"Applied plan {plan_id} ({len(res.results)} op(s)) as one undo step."
    if outcome.mismatches:
        text += "\nWarning: the project differs from the plan's expected result:\n" + (
            format_changes(outcome.mismatches)
        )
    return text


def discard_plan(ctx: Context, args: dict[str, Any]) -> str:
    if not ctx.store.discard(args["plan_id"]):
        raise ToolError(f"unknown plan {args['plan_id']!r}.")
    return f"Discarded plan {args['plan_id']}."


def undo_last_plan(ctx: Context, args: dict[str, Any]) -> str:
    try:
        done = ctx.store_api.undo_last(ctx.bridge, ctx.store)
    except BridgeError as exc:
        raise ToolError(f"bridge error: {exc}") from exc
    return "Undid the last applied plan." if done else "Nothing to undo: no plan applied by us."


def list_plans(ctx: Context, args: dict[str, Any]) -> str:
    plans = ctx.store.list()
    if not plans:
        return "No plans."
    return "\n".join(f"{p.id}  {p.created_at}  {p.summary}" for p in plans)


def get_plan(ctx: Context, args: dict[str, Any]) -> str:
    try:
        plan = ctx.store.get(args["plan_id"])
    except PlanError as exc:
        raise ToolError(str(exc)) from exc
    return describe_plan(plan, with_ops=True)


_PLAN_ID = {"type": "string", "description": "Plan id from a plan_* tool"}

TOOLS = [
    Tool(
        name="plan_rename_track",
        description="Plan a track rename (dry run, changes nothing). Track is identified by GUID. Returns plan id, base hash and expected changes.",
        properties={
            "track": {"type": "string", "description": "Track GUID"},
            "new_name": {"type": "string"},
        },
        required=["track", "new_name"],
        handler=plan_rename_track,
        guid_args=("track",),
    ),
    Tool(
        name="apply_plan",
        description="Apply a stored plan as one undo step. Refuses if the project changed since planning. dry_run=true previews only.",
        properties={"plan_id": _PLAN_ID, "dry_run": {"type": "boolean"}},
        required=["plan_id"],
        handler=apply_plan,
    ),
    Tool(
        name="discard_plan",
        description="Forget a stored plan without applying it.",
        properties={"plan_id": _PLAN_ID},
        required=["plan_id"],
        handler=discard_plan,
    ),
    Tool(
        name="undo_last_plan",
        description="Undo the most recently applied plan (one REAPER undo step).",
        properties={},
        required=[],
        handler=undo_last_plan,
    ),
    Tool(
        name="list_plans",
        description="List plans stored in this session.",
        properties={},
        required=[],
        handler=list_plans,
        profiles=("full",),
        read_only=True,
    ),
    Tool(
        name="get_plan",
        description="Show one stored plan: summary, base hash, ops and expected changes.",
        properties={"plan_id": _PLAN_ID},
        required=["plan_id"],
        handler=get_plan,
        profiles=("full",),
        read_only=True,
    ),
]
