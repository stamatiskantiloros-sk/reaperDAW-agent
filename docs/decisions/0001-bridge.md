# ADR 0001 — Bridge: fork Reaper Daemon

Status: **proposed**, pending Gate 0 · Date: 2026-11-18 · REAPER 7.86 (macOS arm64)

## Context

Phase 0 checks which REAPER bridge to build on: Reaper Daemon (MIT, Lua bridge in REAPER + stdlib Python, file IPC) or ReaperMCP (Apache 2.0, about 180 tools). Evidence in `0001-evidence/`:

- `reaper-daemon-map.md`: code map of Reaper Daemon v3.23.1 (commit b906ce4).
- `checklist-static.md`: the 12-item checklist answered from code for both bridges. **ReaperMCP was not run live**; its answers are static only.
- Reaper Daemon was run live on generated fixtures (`scripts/make_fixtures.py`) in a scratch project, with all write gates off.

## Reaper Daemon in brief

- Transport: JSON files in `inbox/` and `outbox/` inside the clone (`upstream/` here), a Lua `defer` loop (0.25 s idle, 0.05 s active). Python side is stdlib only. About 7.4k lines of core Lua and 4.7k of core Python; the rest is unrelated (drums, guitar, transcription, console).
- Safety: `dry_run` honoured, one undo block per command or per `batch`, disk-write gates and `run_script` closed by default. Installer adds a managed block to REAPER's `Scripts/__startup.lua` (removable with `--uninstall`).
- MCP server: 43 tools, no tool profiles. We will not use `reaper_mcp.py`; our own server in `agent/` talks to the same file protocol.

## Live checklist (Reaper Daemon)

| # | Item | Result | How / gap |
| --- | --- | --- | --- |
| 1 | Read tracks | Partial | `get_context`: GUID, name, folder depth, sends. No colour, no parent GUID. |
| 2 | Rename, recolour | Pass | `rename_track`, `set_track_color` by GUID. |
| 3 | Folder depth, move track | No | Read only. Needs new Lua handlers. |
| 4 | Items, takes | Partial | `get_items` per track, by index, active take name only. No item GUID, take list or active-take index. |
| 5 | Fixed item lanes | No | Needs new handlers (verify API first). |
| 6 | Regions, markers | Pass | Read in `get_context`, `add_region`. (An early region-end anomaly was our fixture's bug; generator fixed, reopen to confirm.) |
| 7 | FX list, params | Pass | `get_fx_parameters`: names, normalized values, formatted values (no unit text). |
| 8 | Set by real unit | Pass | `set_fx_param formatted_value`: `-3 dB`, `120 Hz` on ReaEQ landed exactly. Third-party plugin not yet tried. |
| 9 | FX envelope | Mostly | Write, read, shapes, dry run work. No delete or clear-only command. See bug below. |
| 10 | One undo block | Pass | `batch` of rename, recolour, add FX reverted by one undo; dry run changed nothing. |
| 11 | Tempo map | Pass | `get_tempo_markers`; bar/beat points are correct across a tempo change. |
| 12 | Project path, media | Partial | Only the project file name in `get_context`; media via `get_items` `source_file`. No project path. |

Static result for ReaperMCP: 3 yes, 6 partial, 3 no. It addresses everything by integer index with no GUIDs, its undo grouping only labels steps, and it has no set-by-real-unit. Reaper Daemon's static result: 6 yes, 4 partial, 2 no.

## Measurements

Per-command round trip is about 57–62 ms, independent of project size, of which about 30 ms is the Lua defer gap (reported in each reply's `timing`). These numbers include starting the Python CLI per call, so an in-process client will be somewhat faster.

| Project | `get_context` | Full snapshot with Daemon commands |
| --- | --- | --- |
| 50 tracks | 58 ms | 3.1 s (52 calls) |
| 150 tracks | 62 ms | 9.2 s (154 calls) |

The cost is call count (one `get_items` per track), not data size. A one-call `agent.snapshot` handler inside REAPER (Phase 1) is required and should bring this near one round trip.

## Findings to carry forward

1. **Envelope-creation bug (needs confirming).** The first `write_fx_param_automation` that creates an envelope fails its own read-back hash and rolls back, when the first point's value differs from the parameter's current value; later writes to the existing envelope pass. Probable cause is the point REAPER seeds at time 0. Workaround: our `ensure_fx_envelope` op runs first.
2. `clear_existing_in_range: false` with points identical to existing ones also fails verification. We always clear the range, so low impact.
3. Reaper Daemon reads are thin: no item GUIDs, no take list, no colour. Our `agent.snapshot` handler provides all of these.
4. IPC folders live inside the clone. Fine for dev; they are gitignored. Document this in the README.
5. `reaperd.py` must run without `python -I` (it imports sibling modules).

## Decision

Fork Reaper Daemon, keep it under `upstream/` with the `upstream` remote, and leave its files unmodified. Our code goes in `agent/` and `lua/agent/`: a new MCP server with `core`/`full` profiles, new Lua handlers (snapshot, apply_plan, folders and moves, lanes, take list, project path, envelope ensure/clear), and our own file-protocol client. **Upstream edits so far: none.**

## Consequences

- We inherit a large codebase of which we use about a third; monthly merges stay easy because we do not edit it.
- We depend on a single-maintainer project (pushed 2026-10-21). If it stalls, the file protocol is small enough to own.
- Lanes and folder handlers need REAPER 7 API verification against current docs before coding.
- Open at the gate: ReaperMCP was compared statically only; a live run could be added if you want a second opinion.
