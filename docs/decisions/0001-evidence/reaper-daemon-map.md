# Reaper Daemon: code map (read-only survey)

Source: `daemon/src/` (shallow clone, MIT, v3.23.1 per `bridge/reaper_agent_bridge.lua:2`). All paths below are relative to `daemon/src/`. Nothing was run or imported.

## 1. File layout

| Role | File | Lines | Notes |
|---|---|---|---|
| Core, Lua (in REAPER) | `bridge/reaper_agent_bridge.lua` | 5696 | Queue poller, gates, dispatch, ~76 handlers |
| Core, Lua | `bridge/automation.lua` | 805 | FX-param envelopes |
| Core, Lua | `bridge/tempo_and_actions.lua` | 494 | Tempo markers, actions, `run_script` |
| Core, Lua | `bridge/json.lua` | 284 | Vendored JSON |
| Core, Lua | `bridge/reaper_focus.lua` | 152 | Focus helper |
| Core, Python (CLI) | `reaperd.py` | 2155 | Stdlib CLI and queue client |
| Core, Python (MCP) | `reaper_mcp.py` | 2550 | Stdlib JSON-RPC stdio server, 43 tools |
| Core, Python (support) | `verifyloop.py` (1496), `watchdog.py` (455), `setup/install.py` (425) | | `reaper_mcp.py` imports `verifyloop` and `reaperd` at import time (`reaper_mcp.py:42-43`) |
| Extra | `bridge/reaper_daemon_console.lua` (1199), `console_sidecar.py` (2326) | | In-REAPER chat panel, clone-only |
| Extra | `drum_*.py`, `transcription_worker.py`, `songplan.py`, `instruments*.py`, `mix_recipes.py`, `performance_audition.py`, `skills/*` | | Drums, guitar, song plan, transcription. Imported lazily by MCP handlers |
| Extra | `setup/requirements-transcription.txt` | | Torch, demucs, librosa and others, optional |
| Tests | `bridge/test_*.lua`, `tests/` | | Not reviewed in depth |

Core is about 7.4k lines of Lua and about 4.7k lines of Python (plus about 2.4k of support Python). Repo docs contain agent directives in `AGENTS.md`. They were not followed and are not relevant to this survey.

## 2. Transport

| Item | Where / value |
|---|---|
| Lua runs | Deferred script loaded by REAPER's `Scripts/__startup.lua` via `dofile` (`setup/install.py:74-109`, `:301-342`). Loop uses `reaper.defer` (`bridge/reaper_agent_bridge.lua:5563`). |
| Bridge root (inbox/outbox) | The clone folder, one level above `bridge/`, on every OS (`reaper_agent_bridge.lua:197-206`, `:286-293`; `reaperd.py:45-48`). Folders: `inbox/ processing/ outbox/ failed/ archive/ logs/`. |
| REAPER resource dir (only the startup hook lives here) | macOS `~/Library/Application Support/REAPER`; Windows `%APPDATA%\REAPER`; Linux `$XDG_CONFIG_HOME/REAPER` (`reaperd.py:56-72`; `setup/install.py:50`). |
| Config | `bridge/bridge_config.json`, created on first run with defaults (`reaper_agent_bridge.lua:171`, `:216-237`). |
| Command envelope | `{id, version:3, type, created_by, created_at, dry_run, payload, token?}` (`bridge/command_schema.md:10-40`). `id` must match `[A-Za-z0-9_.-]+` (`reaperd.py:49`). |
| Reply | `outbox/<id>.json`, written by `write_result` (`reaper_agent_bridge.lua:5351`). Shape `ok` plus `data` or `error` (`command_schema.md:40-50`). |
| Queue flow (Lua) | Atomic write to `*.tmp` then rename into `inbox/` (`reaperd.py:260-263`). Lua moves to `processing/`, runs, archives or fails (`reaper_agent_bridge.lua:5378-5423`). Startup triage for stranded files (`:5657-5676`). |
| Lua poll interval | 0.25 s idle (`:211`, `:399`). Drops to 0.05 s for 2 s after activity (`:5536-5538`). Per-tick drain budget (`:5483-5547`). |
| Python wait | Reply poll 5 ms for the first second, then 25 ms (`reaperd.py:282`, `:292`). |
| Timeouts | `send_command` default 30 000 ms (`reaperd.py:218`). `send_type` 10 000 ms (`:335`). Render-class commands get longer budgets (`:314`). MCP `DEFAULT_TIMEOUT_MS` 15 000 (`reaper_mcp.py:75`). On timeout the command file is withdrawn (`reaperd.py:293-302`). |
| Stale-reply guard | Old `outbox/<id>.json` removed before send, fails closed if it cannot be removed (`reaperd.py:245-258`). |
| Singleton | Lock/heartbeat so one bridge owns the inbox (`reaper_agent_bridge.lua:300-385`). |
| Auth | Optional `auth_token`, off by default. Checked in Lua (`:5401`, `:243-249`), added by CLI from config (`reaperd.py:440-446`). Config is world-readable (per the script's own note at `:243-248`). |
| Install | `python3 setup/install.py` (flags `--allow-audio-writes`, `--allow-project-save`, `--allow-preference-writes`, `--allow-disk-writes`, `--allow-script-execution`), then restart REAPER (`INSTALL.md:82-141`). |

## 3. Command list (`handlers` table, `reaper_agent_bridge.lua:4112`-`5326`, plus 7 loop-registered at `:4610`)

Mutating unless marked (R). Track, tempo, FX-envelope and marker groups are the ones you asked about.

**Tracks**
| Command | Purpose |
|---|---|
| add_track, delete_track, rename_track, select_track | Track CRUD and selection (`:4619-4622`) |
| set_track_volume, set_track_pan, mute_track, solo_track, arm_track, set_track_color | Track props (`:4625-4630`). Colour set via `I_CUSTOMCOLOR` (`:1627-1633`) |
| create_send, set_master_send | Sends (`:4635-4636`) |
| insert_track_template | Insert template (`:4547`) |
| snapshot_track_state (R), restore_track_state | Track state to a file (`:4639-4640`) |
| get_track_routing (R), get_selected_track (R) | Read (`:4612`, `:4615`) |
| Folder depth | Read-only: `I_FOLDERDEPTH` in `get_mix_snapshot` (`:3994`) and track listing (`:683`). No folder or track-move command. Schema says folder routing is unsupported (`command_schema.md:553-554`). |

**Items, takes, MIDI**
| Command | Purpose |
|---|---|
| get_items (R) | Items per track, source file, take FX offline/bypass (`:4929`; `command_schema.md:313-331`) |
| delete_items_in_range | Delete items (`:4664`) |
| insert_midi_file, insert_midi_events, insert_track_template | MIDI in (`:4667`, `:4198`) |
| get_midi_notes (R), set_note_velocities, set_note_positions, apply_note_edits | Note edits by position and pitch (`:4668-4690`; `command_schema.md:938-1091`) |
| get_audio_timing (R), apply_audio_timing | Native stretch markers on take (`:5093`, `:5107`) |
| insert_drum_transcription, get_transcription_source (R) | Transcription (`:5092`, `:5172`) |
| Lanes / take switching | None. No lane or take create/select commands. |

**Regions and markers**
| Command | Purpose |
|---|---|
| add_marker, add_region, delete_marker | (`:4661-4663`). `delete_marker` also takes `is_region` |
| Read | Markers and regions appear in `get_context` (`command_schema.md:122`) |

**Tempo map**
| Command | Purpose |
|---|---|
| set_tempo (base tempo) | (`:4527`) |
| get_tempo_markers (R), set_tempo_marker, delete_tempo_marker | Tempo and time-sig markers (`:4532-4534`; `command_schema.md:212-238`) |

**FX (chains and params)**
| Command | Purpose |
|---|---|
| get_context (R), scan_fx (R), get_fx_parameters (R) | Discovery (`:4515-4517`) |
| add_fx, add_fx_chain, save_fx_chain, remove_fx, bypass_fx, move_fx | Chain edits (`:4648-4653`) |
| set_fx_param | Set by `normalized_value`, `relative`, or `formatted_value`. Formatted values use a binary search with real writes and rollback (`command_schema.md:681-700`; `:1716`). Enum params rejected. |
| get_fx_preset (R), set_fx_preset, link_fx_midi_cc, configure_midi_input, get_midi_inputs (R), save_project_as | Host presets, MIDI CC links (`:4610`) |

**FX-param envelopes (automation)**
| Command | Purpose |
|---|---|
| write_fx_param_automation | Create or insert points with per-point `shape`. Times as `time`, `seconds` or `bar`. Verifies by reread (`command_schema.md:701-758`; `:4655`) |
| get_fx_param_automation (R) | Read envelope without creating it (`:4656`; `command_schema.md:784-807`) |
| set_fx_param_automation_state | Only ACT, VIS, ARM via `SetEnvelopeInfo_Value` (`:4657`; `automation.lua:548-600`) |
| apply_automation_transaction | Several writes, one undo block, rollback on failure (`:4658`; `command_schema.md:759-783`) |
| Clear / delete | No delete. Envelopes created by a failed transaction are emptied, and REAPER undo removes them (`command_schema.md:739-741`) |

**Actions, scripts, render, project**
| Command | Purpose |
|---|---|
| play, stop, pause, record, set_cursor, set_time_selection | Transport (`:4521-4526`) |
| render (gated), capture_track_audio (gated), get_render_settings (R), set_render_settings | Render and capture (`:4528`, `:4611`, `:5270-5292`) |
| save_project (gated) | (`:4529`) |
| get_capture_preflight (R) | (`:4616`) |
| get_items, set_all_media_online, set_media_offline_when_inactive (gated) | Media online state (`:4837`, `:4867`) |
| new_project_tab | (`:4587`) |
| list_actions (R), run_action, run_script (gated) | (`:4537-4539`) |
| preview_change, commit_preview, cancel_preview | Temporary preview with its own undo (`:2473-2630`, `:4643-4645`) |
| batch | Several commands in one undo block (`:4467`) |
| reload_bridge | Reload config and scripts (`:5323`) |
| get_mix_snapshot (R), capture_mix_recipe (R), rebuild_mix_recipe, discover_drum_map (R) | Mix and drums (`:4512-4518`) |

**Project path and media list**
- Project path: returned by `save_project` (`:3603`) and by transcription source (`:5086`). I did not confirm every `get_context` field.
- Media list: `get_items` gives `source_file` and `source_state` per item (`command_schema.md:313-331`).

MCP exposes these as 43 tools (`reaper_mcp.py:1351` onward). The `track` tool takes 10 actions (`:405-410`), `fx` takes add/remove/bypass/move (`:435`), `markers` takes 3 (`:485`), and `transport` takes 10 (`:1411-1426`). `raw_command` passes any command type (`:2028`, `:535`). `run_script` is its own tool (`:2073`, `:566`).

## 4. Gating

| Gate | Covers | Enforced at |
|---|---|---|
| `allow_audio_writes` | render, capture_track_audio | `reaper_agent_bridge.lua:270-273` (`gate.allows`), `:3523`, `:3613` |
| `allow_project_save` | save_project, save_project_as | `:3591`, `:4257` |
| `allow_preference_writes` | set_media_offline_when_inactive | `:4875` |
| `allow_risk_level_3` | Fallback for the three keys above only. Default `false`. | `:270-273`, `:212` |
| `allow_script_execution` | run_script. Strict key, no fallback. | `tempo_and_actions.lua:184-196` |

- Default config is closed (`reaper_agent_bridge.lua:208-213`).
- `setup/install.py` writes the flags and prints gate status (`:165-181`).
- Gate refusals name the key and the fix (`:277-283`).
- `raw_command` is not a separate gate. It relies on the same Lua checks.

**Dry-run:** honored for every mutating command in the envelope or in `payload` (`reaper_agent_bridge.lua:4425-4431`). Reads ignore it. Commands that skip undo handle `dry_run` in their own handler (examples: `:2478`, `:2544`, `:4098`, `:4555`, `:4838`, `:4886`, `:5291`). `insert_drum_transcription` honours `validate_only` too (`:4426-4427`). `run_script` falls through the generic path, so dry-run returns `would_run` (`:4429`).

## 5. Undo blocks

- **Per command:** `run_command` opens one block around each mutating command, labelled `command.undo_label` or `"Agent: <type>"` (`reaper_agent_bridge.lua:4412-4451`, specifically `:4437-4449`). The block closes even on error (`pcall`).
- **Exempt:** `NO_UNDO_BLOCK` (`:4358-~4410`). Reads, preferences, preview state, and commands with their own blocks.
- **Batch:** one block for the whole set (`:4467-4505`). Sub-commands run with `in_batch` and open no nested block. `stop_on_error` defaults to true. A failure closes the block and leaves partial changes (`rolled_back: false`). Nested batches and `rebuild_mix_recipe` inside a batch are rejected (`:4467-4480`).
- **Self-managed:** `commit_preview` (`:2619-2622`), `rebuild_mix_recipe` (`:2831-2852`), automation transactions (`automation.lua`, one block per command). The tool-level `batch` in MCP uses the same path.

## 6. Python dependencies and MCP entry point

- **Core:** stdlib only (`reaper_mcp.py:5-6` docstring; imports at `:29-43`). `reaperd.py` imports are stdlib too (`reaperd.py:30-41`). Optional packages are loaded lazily per feature.
- **Optional:** `setup/requirements-transcription.txt` (Torch, demucs and so on) for transcription only. The Post Mortem CLI (`postmortem`, or `POSTMORTEM_CMD`) for `analyze_track` and `compare_tracks` (`reaper_mcp.py:1092`; docs/mcp.md).
- **Entry point:** `python reaper_mcp.py` on stdio, newline-delimited JSON-RPC 2.0 (`reaper_mcp.py:2520-2549`). Protocol versions `:72-73`. Install line in the docstring at `:11-15` (`claude mcp add reaper -- python ...`).
- **Tool count and profiles:** 43 tools. There are **no profiles or tool-set switches**. `tools/list` returns every entry in `TOOLS` (`reaper_mcp.py:2496-2501`). The only per-tool gate is `_NO_BRIDGE_TOOLS`, which lets 4 tools run without a clone (`:2458-2459`). `REAPER_DAEMON_CONSOLE_MODE` clamps capture length, not tool visibility (`:174-190`).
- **Env vars:** `REAPER_DAEMON_ROOT` (`reaper_mcp.py:46`, `reaperd.py:45-48`), `REAPER_RESOURCE_PATH` (`setup/install.py`).

## Fork decision notes

1. **Architecture is simple and portable.** Plain files in a folder, a Lua defer loop, and stdlib Python. No sockets, no installs beyond the REAPER startup hook. Latency is about 0.25 s idle, 0.05 s when active.
2. **Safety is well built but has a few sharp edges.** Undo per command or per batch, dry-run, and default-closed gates for disk writes. But `raw_command` and `run_script` give a full Lua escape behind one flag, and the shared secret is off by default.
3. **Gaps for a DAW-control fork:** no folder or track-move, no take or lane management, no envelope deletion, and all 43 MCP tools are always advertised. The Lua bridge is one 5.7k-line file, and the MCP schemas are hand-written.

## Could not determine

- Exact `get_context` field list (not read in full).
- Whether every mutating handler in the 83 total passes `dry_run` in its own branch (I checked the pattern, not each one).
- Runtime behaviour. Nothing was executed, by design.
