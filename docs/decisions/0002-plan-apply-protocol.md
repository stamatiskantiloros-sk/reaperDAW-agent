# ADR 0002 — Plan / apply protocol

Status: accepted for Phase 1 (2026-11-18)

## Context
Every mutating feature (housekeeping, automation) must be safe by construction: reviewable before it runs, one undo step, no index drift, no silent damage. Phase 0 showed Reaper Daemon's reads are thin and its `batch` is not a plan applier, so Phase 1 defines our own protocol on top of its transport.

## Decisions
1. **Snapshot** (`agent/core/models.py`, schema_version 1) has no indices anywhere. Objects are identified by GUID; list order is project order. Colours are `#RRGGBB`, times are seconds. Volatile state (selection, cursor, transport, meters) is not modelled. Our own bookkeeping lives in `agent_ext` (keys `reaper_agent:*`), stored in REAPER as one JSON blob per object under `P_EXT:reaper_agent` (project: section `reaper_agent`), because REAPER cannot enumerate ext keys.
2. **Hash** (`agent/core/hashing.py`): sha256 (32 hex chars) of the canonical snapshot with `agent_ext` removed, items and markers sorted by GUID, floats rounded to 6 decimals. Track order counts.
3. **Ops** (`agent/core/ops.py`): 17 atomic ops, all GUID-addressed; objects created inside a plan are referenced as `ref:<alias>` and resolved to real GUIDs by the executor (`ApplyResult.refs`). FX parameters have no GUID, so they are addressed by FX GUID plus parameter index. Envelope creation is its own op (`ensure_fx_envelope`) because Daemon's combined create+write failed its verification in Phase 0.
4. **Flow** (`agent/core/store.py`): `build_plan` snapshots, hashes and simulates the ops on a copy (`plan.expected` = diff); `apply_stored_plan` re-snapshots and raises `StalePlanError` if the hash changed, applies, re-snapshots and returns any mismatch against `plan.expected`. Dry runs never reach REAPER (pure simulation).
5. **Apply** (`lua/agent/apply_plan.lua`): one Lua call runs all ops inside a single `Undo_BeginBlock2`/`Undo_EndBlock2` named after the plan summary, with `PreventUIRefresh`; the handler is exempt from the bridge's generic undo wrapper (`ctx.no_undo_block`) so there is exactly one block. Track selection is restored afterwards. On the first failing op the run stops and, if the block is REAPER's newest undo point and the project actually changed, it is undone (`rolled_back = true`); otherwise the failure is reported without undoing anything, so a user's earlier edit is never reverted.
6. **Originals**: ops that change user-visible values first save the old value once under `reaper_agent:orig_*` (never overwritten), so tidying stays reversible after saving.
7. **Transport quirks handled once, centrally**: Lua's JSON encoder writes empty tables as `[]`, so `serde.from_dict` accepts `[]` where a dict is expected; Lua integers are produced with `math.floor` so the strict Python model accepts them.

## Measured (live, REAPER 7.86, 150-track fixture)
`agent.snapshot` 52 ms in one round trip (versus 9.2 s from Daemon's per-track commands); `agent.apply_plan` of a 2-op plan 20 ms; one `agent.undo` restores an identical snapshot hash.

## Findings from the live Lua work (REAPER 7.86)
- REAPER adds a default point at t=0 when it creates an FX envelope (point count 1 after `ensure_fx_envelope`). Verification therefore ignores envelope point counts; point content is verified by reading the envelope back in Phase 4.
- Switching a track to fixed lanes yields two empty lane names, and REAPER auto-names lanes "1".."4" from four lanes up; verification only checks the lane names a plan actually set. `set_item_lane` / `set_lane_name` grow the lane count when needed, and `set_lane_name` errors outside fixed-lane mode.
- Project ext state is not part of REAPER's undo, so `orig_region:*` outlives an undo (harmless: it is bookkeeping and excluded from the hash). Enumerated project ext keys come back lower-cased; the snapshot normalises this.
- A plan that only writes project-scope ext state creates no undo point. `undo` therefore takes the plan summary as `expect_label` and refuses when the newest undo step has a different name, so we never undo the user's earlier edit.
- Upstream bug to report: Daemon's `get_fx_param_automation` reads `B_ACTIVE`/`B_VISIBLE`, which are not documented envelope attributes, so it always reports false. Our snapshot uses `ACTIVE`/`VISIBLE`.
- Shape numbers 0 to 5 round-trip, but no documentation names them; the mapping rests on the REAPER envelope-point conventions.

## Known limits
- A small race remains between the stale check and the apply (another edit can land in between); Lua aborts if a target GUID no longer resolves.
- Rollback relies on REAPER's undo stack; the guard prefers doing nothing over undoing the wrong step.
- Integration tests need a live REAPER (macOS fixture loading); they cannot run in CI. Unit, fake-bridge and Lua-syntax tests can.
- Fixed-lane and envelope APIs were verified against REAPER 7.86 only.
