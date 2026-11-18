# ADR 0001 — Bridge: fork Reaper Daemon

Status: **accepted** at Gate 0 (2026-11-18) · Date: 2026-11-18 · REAPER 7.86 (macOS arm64)

## Context

Phase 0 checks which REAPER bridge to build on: Reaper Daemon (MIT, Lua bridge in REAPER + stdlib Python, file IPC) or ReaperMCP (Apache 2.0, about 180 tools). Evidence in `0001-evidence/`:

- `reaper-daemon-map.md`: code map of Reaper Daemon v3.23.1 (commit b906ce4).
- `checklist-static.md`: the 12-item checklist answered from code for both bridges.
- Both bridges were then run live in the same REAPER 7.86 on generated fixtures (`scripts/make_fixtures.py`), in a scratch project. Reaper Daemon with all write gates off; ReaperMCP's Lua server loaded by hand (its installer was not used).

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

## Live checklist (ReaperMCP)

| # | Item | Result | How / gap |
| --- | --- | --- | --- |
| 1 | Read tracks | Partial | `track_get_all`: index, name, colour (r,g,b), folder depth, FX and send counts. No GUID, no parent. |
| 2 | Rename, recolour | Pass | `track_rename`, `track_set_color`, **by index**. |
| 3 | Folder depth, move track | Partial | `track_set_folder` writes the raw depth with no neighbour fix-up (a lone `1` left the folder unclosed). No move command. |
| 4 | Items, takes | Pass | `item_get_all`, `item_take_list` (all takes with `is_active`). Index-addressed. |
| 5 | Fixed item lanes | No | No command (static). |
| 6 | Regions, markers | Pass | `marker_get_all` with `region_end`; `marker_add_region`. |
| 7 | FX list, params | Pass | `fx_get_chain`, `fx_get_params`: **display strings include units** (`100.0 Hz`, `0.0 dB`). No FX GUID. |
| 8 | Set by real unit | No | `fx_set_param[_by_name]` takes a raw number only; `"-3 dB"` raised a Lua error. |
| 9 | FX envelope | Pass | `envelope_add_points`, `_get_points`, `_clear_range` all worked, no read-back verification. The create-on-first-write case that fails in Daemon worked here. |
| 10 | One undo block | No | Three commands left three undo steps. `undo_group_begin/end` only labels; `project_undo_group` refused ("not made by a multi-command tool"). |
| 11 | Tempo map | Partial | `tempo_list_markers` works (140 bpm at 16 s). No beat-to-seconds conversion command. |
| 12 | Project path, media | Pass | `project_get_info` returns the `.rpp` path (`file_path`); media via `item_get_all` `source_file`. |

Tally, live: ReaperMCP 6 pass, 3 partial, 3 no. Reaper Daemon 6 pass, 4 partial (including the envelope item), 2 no.

## Comparison

| Our principle | Reaper Daemon | ReaperMCP |
| --- | --- | --- |
| GUIDs, never indices | Yes (tracks, FX in replies) | No, indices everywhere |
| One plan = one undo step | Yes, `batch` | No |
| Real-unit control | Yes | No |
| Dry run | Yes | Not seen |
| Verified, rollback-safe automation | Yes (with the creation bug) | No verification |
| Richer reads (takes, colour, path, units) | Thin | Good |
| Envelope clear | No | Yes |
| Tool profiles | No (we write our own server) | Yes (`REAPER_MCP_PROFILE`) |
| Python dependencies | Stdlib only | `mcp[cli]` package |

The three principles that are hardest to retrofit (GUID addressing, one undo step, real units) are Daemon's strengths. Daemon's gaps are additive reads and new handlers; ReaperMCP's would mean reworking addressing and undo across about 180 tools. ReaperMCP's reads (take list with active flag, colour, project file path, unit-bearing display strings, envelope clear) are good references; Apache 2.0 allows adapting them into our own handlers with attribution in a `NOTICE`.

## Measurements

Per-command round trip, independent of project size: Reaper Daemon about 57–62 ms (of which about 30 ms is the Lua defer gap, reported in each reply's `timing`); ReaperMCP about 19–31 ms. **Not like for like:** the Daemon figures include starting its Python CLI for every call, while my ReaperMCP client stayed in one process. The Lua polling floor is similar for both, so an in-process Daemon client should land near ReaperMCP's numbers.

| Project | Daemon: one-call read | Daemon: full snapshot (CLI per call) | ReaperMCP: full snapshot (in-process) |
| --- | --- | --- | --- |
| 50 tracks | 58 ms | 3.1 s (52 calls) | 1.6 s (52 calls) |
| 150 tracks | 62 ms | 9.2 s (154 calls) | 4.6 s (152 calls) |

For both, the cost is call count (one call per track or item), not data size. A one-call `agent.snapshot` handler inside REAPER (Phase 1) is required and should bring this near one round trip.

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
- Borrowing from ReaperMCP (Apache 2.0) needs attribution in a `NOTICE` file. Both bridges can be loaded in one REAPER at once, which is handy for side-by-side checks.
- Neither bridge has fixed-lane support; that is new work on our side either way.

## Addendum (2026-11-18, Phase 1): the one upstream edit

Reaper Daemon keeps its handler table as a local variable and offers no way to add handlers from outside; `reload_bridge` and the startup watchdog reload only the bridge file. To register `agent.snapshot` / `agent.apply_plan` without forking the whole transport, **`upstream/bridge/reaper_agent_bridge.lua` carries a 21-line hook** (`patches/0001-extra-handlers.patch`, inserted after `handlers.batch = command_batch`).

- Behaviour: for each path in `bridge_config.json` → `extra_handler_modules` (a per-machine, gitignored file), the bridge `loadfile`s it, expects a table with `register(ctx)`, and calls it with `ctx = { reaper, handlers, json, no_undo_block, config, log, module_path }`. A module that fails to load or register is logged and skipped.
- Our module is `lua/agent/handlers.lua`; `scripts/install_agent.py` adds or removes the path and sends `reload_bridge`.
- Merging upstream: the hunk is self-contained. If a merge conflicts, re-apply with `git apply patches/0001-extra-handlers.patch`. The patch could be offered upstream.
- No other upstream file is modified.
