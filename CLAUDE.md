# CLAUDE.md — reaper-agent

An MCP server that lets an AI agent tidy REAPER projects and write section-based FX automation, safely. Owner: Stamatis (musician, Python/ML background). The full plan is `docs/PLAN.md`; read it before any work.

## How to work in this repo

- Work **one phase at a time** from `docs/PLAN.md`. Start each phase in plan mode, show the plan, then execute.
- Tick checkboxes in `docs/PLAN.md` as tasks land. Add new tasks there rather than doing unplanned work.
- **Stop at every gate.** Write the manual test steps for the user, then wait for an explicit OK before the next phase.
- Record significant choices as ADRs in `docs/decisions/NNNN-title.md` (context, decision, consequences; half a page).
- Ask before adding a dependency. The bridge side stays dependency-free (Lua + Python stdlib), as upstream is.
- One branch per phase (`phase-N-short-name`), small commits with clear messages, PR at the gate. The GitHub repo is **private**.

## Non-negotiable rules

1. **Plan, then apply.** Mutating tools return a plan (dry run by default). Only `apply_plan` changes the project.
2. **One plan = one undo step.** Every apply runs in a single `Undo_BeginBlock2`/`Undo_EndBlock2` with `PreventUIRefresh`.
3. **GUIDs, never indices,** for tracks, items, takes and FX across calls.
4. **Never delete user material** (tracks, items, takes, media, envelopes) unless the user asks for that specific deletion.
5. **Save originals** (e.g. original track name) in ext state before changing them.
6. **Stale-plan check:** refuse to apply a plan whose base snapshot hash no longer matches.
7. **Deterministic logic in Python with tests;** the LLM layer only orchestrates. Keep tool descriptions short.
8. **Keep the fork mergeable:** don't edit upstream Reaper Daemon files unless unavoidable; put our code in `agent/` and `lua/agent/`, and note any upstream edit in ADR 0001.
9. `run_script` stays gated off in normal use. Features are named Lua handlers.

## Stack

- REAPER 7.x, Lua (ReaScript) for in-REAPER handlers.
- Python ≥ 3.10 for the MCP server and logic; `pytest`; `ruff` for lint/format.
- Config: `config/conventions.yaml`. Plugin profiles: `profiles/plugins/*.yaml`.
- Target OS: macOS, Windows, Linux. Use `pathlib`; no OS-specific code beyond file locations and `ColorToNative`.

## Testing

- `pytest tests/unit` — no REAPER needed (fake bridge).
- `pytest tests/integration` — needs REAPER running with the bridge loaded; skipped automatically otherwise.
- Every new op needs: a unit test, an integration test, and coverage by the **apply → undo → identical snapshot** round-trip test.
- Never test on the user's real projects; use generated fixtures in `tests/fixtures/` or copies the user provides.

## REAPER API notes

- Verify exact API names and attribute strings against the current ReaScript documentation before using them; don't rely on memory. This matters most for REAPER 7 fixed item lanes and FX formatting functions.
- FX-param envelope values are in the parameter's own range (usually normalized 0–1). Convert real units through `agent/automation/units.py`, never by hand.
- Prefer REAPER native actions (found by name through the bridge) where an action already does the job, e.g. takes to lanes.

## Glossary

- **Family:** instrument group (DRM, BAS, GTR, KEY, VOX, FX, BUS, REF) that drives colour, order and naming.
- **Section:** a REAPER region with a label (e.g. `chug`, `open`). Automation is written per label.
- **Profile:** YAML mapping a plugin's parameters to semantic roles (`band.1.gain`) and units.
- **Plan:** a list of atomic ops plus a summary and base snapshot hash, applied as one undo step.

## Out of scope for now

Section detection, tone matching, render-and-measure, and links to gpscore, the drum humanizer and the Cubase converter. Don't build hooks for them beyond keeping sections as labelled regions.
