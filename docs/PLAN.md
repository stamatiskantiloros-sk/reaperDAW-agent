# reaper-agent — Implementation Plan

Version 1 · 18 Nov 2026 · Owner: Stamatis Kantiloros

This plan is the working contract for building `reaper-agent`. Claude Code executes it phase by phase, ticks the checkboxes as tasks land, and stops at every gate for a manual test in REAPER. The human-facing spec lives in the "Reaper DAW Agent — Spec" doc; this file restates everything the build needs, so it is self-contained.

---

## 1. Scope

**In scope (v1):**

1. **Housekeeping:** classify tracks into instrument families, rename them to a scheme, colour them, order and fold them, tidy takes and lanes, and report project health. All of it is rule-based and reversible.
2. **Automation:** write FX-parameter automation per section. The sections come from REAPER regions the user marks (e.g. `chug`, `open`). This works on any plugin, native or third party, with values given in real units ("-4.5 dB", "120 Hz").

**Out of scope (later):** automatic section detection, tone matching against a reference, two-mic alignment, render-and-measure loop, and links to gpscore, the drum humanizer and the Cubase converter.

**Interface kept stable for later:** sections are always REAPER regions with a label. Section detection will later write regions in the same format, so the automation module never changes.

## 2. Decisions this plan rests on

| Topic | Decision |
| --- | --- |
| Bridge | Fork **Reaper Daemon** (MIT, Lua bridge inside REAPER + Python outside, file-based IPC, no pip deps, macOS/Windows/Linux, every change in an undo block, FX values in real units, transactional automation writes with rollback, gated `run_script` escape hatch to the full ReaScript API). Extend it with what it lacks: folders, take lanes, comping, rule-based housekeeping. Phase 0 checks this hands-on against **ReaperMCP** (xDarkzx; about 180 tools, Apache 2.0) before committing. |
| Platforms | Any machine that runs REAPER 7. No OS-specific code paths beyond file locations. |
| LLM backend | Any MCP client: Claude (Code, Desktop, API) or a local model through an MCP-capable client. The intelligence lives in deterministic tools; the model only orchestrates. A `core` tool profile of 30 tools or fewer serves models with tool-count limits. |
| Plugins | All formats (VST2, VST3, AU, CLAP, JS, REAPER native). Generic discovery first; per-plugin profiles grow over time, starting with ReaEQ and the user's most-used plugins, Toontrack included. |
| Conventions | Evidence-based default naming and colour scheme in `config/conventions.yaml` (section 7). |
| Repo | Private GitHub repo `reaper-agent`. |

## 3. Architecture

```
MCP client (Claude Code / Desktop / local LLM)
        │  MCP (stdio)
        ▼
reaper-agent MCP server (Python)          ← our code: tools, rules, plans, profiles
   ├── core/        snapshot · plan · apply · diff · undo
   ├── housekeeping/ classify · naming · colours · order/folders · takes/lanes · health
   ├── automation/  sections · curves · unit conversion · envelope writer
   ├── profiles/    plugin profile store + discovery
   └── bridge client ──► Reaper Daemon file bridge (forked, kept close to upstream)
                              │  JSON command files in an inbox folder
                              ▼
                         Lua bridge inside REAPER (+ our Lua handlers in lua/agent/)
                              │  ReaScript API
                              ▼
                            REAPER project
```

**Design principles**

1. **Plan, then apply.** Every mutating tool first returns a *plan*: a list of atomic operations with a human-readable summary. Nothing changes until `apply_plan(plan_id)` runs. Dry run is the default.
2. **One plan = one undo step.** The whole plan executes inside one Lua call, wrapped in `Undo_BeginBlock2` / `Undo_EndBlock2` with `PreventUIRefresh`. One Ctrl+Z reverts it.
3. **GUIDs, never indices.** Track, item and take indices shift when anything moves. Every op addresses objects by GUID, resolved to pointers inside Lua at apply time.
4. **Stale-plan protection.** A plan stores the hash of the snapshot it was computed from. `apply_plan` refuses if the project changed since, and the agent re-plans.
5. **Verify after apply.** Re-snapshot, diff against the plan's expected result, and report mismatches.
6. **Never delete.** Takes, media, tracks and envelopes are flagged, never removed, unless the user explicitly asks in a separate plan.
7. **Deterministic core, thin LLM layer.** Classification, naming, curve building and unit conversion are plain Python with tests. Tool descriptions stay short. This is what makes smaller local models viable.
8. **Keep the fork mergeable.** Upstream files stay untouched where possible; our code lives in `agent/` and `lua/agent/`. Upstream remote kept; merge monthly.

## 4. Repository layout (target)

```
reaper-agent/
├── CLAUDE.md                  # rules for Claude Code
├── docs/
│   ├── PLAN.md                # this file (living; tick boxes)
│   ├── KICKOFF.md             # prompts per phase
│   └── decisions/             # ADRs, e.g. 0001-bridge.md
├── config/
│   └── conventions.yaml       # families, colours, naming, order, classifier keywords
├── profiles/
│   └── plugins/               # one YAML per plugin profile
├── agent/                     # our Python package (MCP tools + logic)
│   ├── server.py
│   ├── core/  housekeeping/  automation/  profiles/  bridge_client/
├── lua/agent/                 # our Lua command handlers loaded by the bridge
├── upstream/ or (fork root)   # Reaper Daemon files, minimally modified
├── tests/
│   ├── unit/                  # no REAPER needed (fake bridge)
│   ├── integration/           # needs REAPER running
│   └── fixtures/              # small generated .rpp projects + JSON snapshots
└── scripts/                   # fixture generator, install helpers
```

The exact placement of the fork (root vs `upstream/`) is decided in Phase 0 after reading its code.

## 5. Phases

Effort is in Claude Code working sessions (S ≈ 1–2, M ≈ 3–5, L ≈ 6+). Each phase ends with a **gate**: Claude Code stops, writes the manual test steps, and waits for the user's OK.

### Phase 0 — Bridge spike and repo setup (S)

Goal: prove the bridge choice on a real REAPER install before building on it.

- [ ] Create the private repo, add this kit, fork Reaper Daemon into it (keep `upstream` remote).
- [ ] Install the bridge on the user's machine; `status` shows it alive.
- [ ] Read the fork's code; write a short map of its command set, transport, timeouts and gating switches into `docs/decisions/0001-bridge.md`.
- [ ] Run the **bridge checklist** below on a scratch project, for Reaper Daemon and (quickly) ReaperMCP.
- [ ] Measure: snapshot time for a 50-track and a 150-track project; round-trip latency per command.
- [ ] Record the decision (fork Reaper Daemon, or switch) with the checklist results in ADR 0001.

**Bridge checklist** (each must pass, or have a clear way to add it):

| # | Operation | Needed for |
| --- | --- | --- |
| 1 | Read all tracks with GUID, name, colour, folder depth, parent | snapshot |
| 2 | Rename and recolour a track | housekeeping |
| 3 | Change folder depth / move a track | housekeeping |
| 4 | Read items, takes, take names, active take | takes |
| 5 | Fixed item lanes: read/set lane mode, lane of an item, lane names, playing lane | lanes/comping |
| 6 | Read and add regions and markers | sections |
| 7 | List FX on a track; list params with names, normalized and formatted values | profiles |
| 8 | Set a param by real unit on ReaEQ and on one third-party plugin | automation |
| 9 | Create an FX-param envelope; insert, clear and read points; set point shape | automation |
| 10 | Run 1–9 inside one undo block; one Ctrl+Z reverts all | safety |
| 11 | Read tempo map; convert beats ↔ seconds | ramps |
| 12 | Read project path and media file list | cleanup report |

Gate 0: user sees the checklist results and the ADR, and agrees on the bridge.

### Phase 1 — Core: snapshot, plan, apply (M)

Goal: the safety machinery every later feature uses.

- [ ] `ProjectSnapshot` model (pydantic or dataclasses): project info, tempo map, tracks (GUID, name, colour, folder depth, parent GUID, sends, FX list, envelope list, automation mode, lane mode), items (GUID, position, length, lane), takes (GUID, name, source file, offline flag), regions/markers. Paged reads for large projects.
- [ ] One Lua handler `agent.snapshot` returns the whole model in one round trip (fall back to paging past a size limit).
- [ ] `Plan` model: id, created, base snapshot hash, ordered ops, summary text, expected post-state for the touched fields.
- [ ] Op vocabulary (v1): `set_track_name`, `set_track_color`, `set_folder_depth`, `move_track`, `insert_track`, `set_take_name`, `set_item_lane`, `set_track_lane_mode`, `set_lane_name`, `add_region`, `set_region`, `set_ext_state`, `set_fx_param`, `ensure_fx_envelope`, `clear_envelope_range`, `insert_envelope_points`, `set_track_automation_mode`.
- [ ] Lua handler `agent.apply_plan`: resolves GUIDs, runs ops in order inside one undo block named after the plan summary, returns per-op results; on any error, aborts and reports which op failed (and relies on undo to revert).
- [ ] Original values are saved before changing them (track ext state `P_EXT:reaper_agent:orig_name` etc.) so housekeeping is reversible even after saving.
- [ ] Diff engine: snapshot vs snapshot → readable change list.
- [ ] Fake bridge (in-memory project) implementing the same command interface, for unit tests.
- [ ] MCP tools: `get_project_overview`, `get_snapshot(section)`, `apply_plan(plan_id)`, `discard_plan(plan_id)`, `undo_last_plan`.
- [ ] Tool profiles: `core` and `full`, selected by env var.

Tests: plan building and diffing on fixtures (unit); **apply → undo → snapshot identical** on real REAPER (integration). This round-trip test is the most important test in the repo.

Gate 1: user applies a trivial rename plan and undoes it in one step.

### Phase 2 — Housekeeping v1 (M–L)

Goal: one call turns a messy project (e.g. a converted Cubase session) into a tidy one.

**2a. Classification**

- [ ] Classifier assigns each track a family (`DRM BAS GTR KEY VOX FX BUS REF`) plus confidence, from these signals, in order of weight:
  1. explicit user override stored in track ext state,
  2. instrument plugin on the track (e.g. Superior Drummer / EZdrummer → DRM, EZbass → BAS, EZkeys → KEY; amp sims → GTR or BAS by name),
  3. keywords in the track name (from `conventions.yaml`),
  4. keywords in item source file names,
  5. parent folder's family,
  6. routing (a track that only receives sends and has a reverb/delay → FX).
- [ ] Folder tracks take the majority family of their children (so `BUS Guitars` stays GTR, not BUS).
- [ ] Below the confidence threshold → mark `unknown`; the agent asks the user, then stores the answer as an override.
- [ ] Tool: `classify_tracks()` → table of track, family, confidence, reason.

**2b. Naming**

- [ ] Parser extracts part (Kick, Snare, Rhythm, Lead, Clean…), source (Close, Room, DI, In, Out, OH…) and side (L, R, C) from the existing name using the vocabulary in `conventions.yaml`.
- [ ] Generator builds `<CODE> <Part> <Source> <Side>`; parts it can't parse are kept verbatim after the code (`GTR <original>`), never dropped.
- [ ] Duplicate names get a numeric suffix (`GTR Rhythm Close L 2`).
- [ ] Takes renamed `<track name> T<n>` (only when the take name is empty or a raw file name; user-named takes are kept).

**2c. Colours, order, folders**

- [ ] Colour per family from config, converted with `ColorToNative` per OS.
- [ ] Target order: family order from config, stable within a family (keeps the user's relative order).
- [ ] Folders: one folder track per family with 2 or more tracks, named `BUS <Family>`, created at unity with no FX.
- [ ] **Routing safety rule:** folder parents pass audio, so foldering can change the mix. Only fold a track if it currently sends to master directly and has no sends to another bus or folder. Otherwise leave it in place and list it in the report.
- [ ] Existing folders are kept; the agent only adds or reorders.

**2d. Takes and lanes (REAPER 7)**

- [ ] Report items with more than one take.
- [ ] Option: move takes to fixed item lanes, one lane per take, lane named after the take; the active take's lane plays. (Verify the exact lane attributes in Phase 0: track lane mode, item lane index, lane names, lane-plays flags; prefer REAPER's native "takes to lanes" action if it exists.)
- [ ] Flag, never delete: empty takes, offline sources, takes whose source peak is below a threshold.

**2e. Health report** (read-only)

- [ ] Default-named tracks ("Track 12"), uncoloured tracks, empty tracks, tracks with no output.
- [ ] Offline media, missing plugins, sample-rate mismatches.
- [ ] Envelopes left in write, latch or touch mode.
- [ ] Media files in the project folder that no item uses (report only).
- [ ] Items with many takes; unlabelled regions.

**Tools:** `classify_tracks`, `plan_tidy(steps=[...], dry_run=True)`, `health_report`, `set_track_family(track, family)` (override). `plan_tidy` returns one plan covering the selected steps; `apply_plan` executes it.

Tests: parser and generator table tests (30+ real-world names, including Cubase-style names like "Audio 07", "Gtr DI_02"); classifier tests on fixtures; foldering safety tests on routed fixtures; integration round trip.

Gate 2: user runs `health_report` and `plan_tidy` on 2 converted Cubase projects and a recent REAPER project, applies, checks, undoes.

### Phase 3 — Plugin profiles and real-unit control (M)

Goal: set any parameter of any plugin by meaning and real unit.

- [ ] **Discovery:** for a given FX instance, read every parameter: index, name, normalized value, formatted value, min/max. Skip obviously internal params (e.g. "Bypass", "Wet", MIDI CC slots) via a filter list.
- [ ] **Unit model:** parse formatted strings into number + unit (`dB`, `Hz`/`kHz`, `ms`/`s`, `%`, `:1`, `Q`), with special tokens (`-inf`, `Off`). Non-numeric or non-monotonic params are treated as enums (label → normalized value table).
- [ ] **Conversion (real unit → normalized):**
  1. If the plugin supports formatting a hypothetical value without changing state (`TrackFX_FormatParamValueNormalized`), sample about 200 points, build a monotonic table, invert by interpolation, refine by bisection (≤ 12 steps), confirm the formatted value.
  2. Otherwise, sample by setting and reading the param, restoring the original value, inside an undo block that is discarded.
  3. Cache tables per plugin + param in `profiles/cache/` (keyed by plugin identifier and version string when available).
  - Reuse Reaper Daemon's existing formatted-value setter where it already does this; extend rather than duplicate.
- [ ] **Profiles:** YAML per plugin mapping semantic roles to params, e.g.

  ```yaml
  plugin: "VST3: Pro-Q 3 (FabFilter)"   # as REAPER names it
  match: ["Pro-Q 3"]
  roles:
    band.1.freq:  { param: "Band 1 Frequency", unit: Hz }
    band.1.gain:  { param: "Band 1 Gain", unit: dB }
    band.1.q:     { param: "Band 1 Q" }
    band.1.on:    { param: "Band 1 Enabled", enum: { off: 0.0, on: 1.0 } }
  notes: "Built from discovery on 2026-xx-xx; verified by user."
  ```

- [ ] **Profile drafting:** for an unprofiled plugin, the agent drafts a profile from discovered names (pattern rules: "Band N …", "N-Freq", "Gain", "Frequency"), shows it, and saves it after the user confirms. Profiles grow with use.
- [ ] Ship profiles for ReaEQ and ReaComp, plus the user's top plugins from their list (Toontrack instruments included; profile only what their hosts expose as automatable).
- [ ] **Tools:** `list_fx(track)`, `describe_fx(track, fx)` (roles if profiled, else filtered raw params), `set_fx_value(track, fx, role_or_param, "-3 dB", dry_run)`, `draft_plugin_profile(track, fx)`, `save_plugin_profile(...)`.

Edge cases to handle: plugins exposing hundreds of params (filter and page); params whose formatted value doesn't change until the UI updates; plugins with no formatted values (fall back to normalized and say so); AU vs VST3 naming differences of the same plugin (match list).

Tests: unit-parser tests; conversion tests on synthetic curves (linear, log, dB, stepped); integration on ReaEQ and one third-party plugin.

Gate 3: user sets "low shelf −3 dB at 120 Hz" on ReaEQ and on one of their own EQs by plain request.

### Phase 4 — Section automation v1 (M)

Goal: "deeper low cut on chug regions, lighter on open regions" becomes an envelope in one approved step.

- [ ] **Sections:** read regions; a region's label is its name, lower-cased and trimmed of numbers (`Chug 2` → `chug`). Also accept the current time selection as an ad-hoc section.
- [ ] **Request model:** track, FX, target (role or param), `values_by_label` (e.g. `{chug: "-6 dB", open: "-2 dB"}`), `default` value outside labelled regions (or "leave as is"), ramp length (ms or note value like `1/16`), ramp placement (`before` boundary, default; `centered`; `after`), point shape (linear default; bezier optional).
- [ ] **Curve builder (pure Python):** piecewise-constant target from sections → point list with ramps; note-value ramps converted to seconds through the tempo map (beats ↔ time) so ramps follow tempo changes; adjacent sections with equal values merged; overlapping regions resolved by the later-starting region, reported as a warning.
- [ ] **Writer:** ensure the FX-param envelope exists and is active and visible; clear the affected range only; insert points in bulk, sort once; set the track's automation mode to read if it is trim/read; all inside the plan's undo block.
- [ ] **Verification:** read the envelope back; check every point within tolerance of the requested real-unit value (via the conversion tables).
- [ ] **Idempotent:** the same request on the same project produces the same points, so revisions ("1 dB less on open") are a re-run with new values, not a stack of edits.
- [ ] **Existing automation:** points outside the affected range are preserved; if points exist inside it, the plan says they will be replaced.
- [ ] **Tools:** `list_sections()`, `plan_section_automation(request)`, then `apply_plan`.

Tests: curve builder unit tests (ramps across tempo changes, merges, overlaps, edges at project start/end); integration on a fixture with regions; idempotency test (apply twice → identical envelope).

Gate 4: on one of the user's songs with `chug` / `open` regions, the agent writes the low-cut automation on their EQ and the user approves it by ear.

### Phase 5 — Hardening and daily use (ongoing)

- [ ] Run housekeeping on 5–10 real projects; turn every surprise into a test.
- [ ] Grow plugin profiles as the user works.
- [ ] Measure and trim tool descriptions; check the `core` profile with a local model.
- [ ] README with install steps for macOS, Windows and Linux.
- [ ] Monthly upstream merge from Reaper Daemon.

## 6. Testing strategy

| Layer | Runs where | What it proves |
| --- | --- | --- |
| Unit | anywhere, CI | parsers, classifier, naming, curves, unit conversion, plan building (fake bridge) |
| Fixture snapshots | anywhere, CI | plans against stored JSON snapshots of real-shaped projects |
| Integration | machine with REAPER running | ops really work; **apply → undo → identical snapshot** |
| Manual gate | user, on copies of real projects | it does what a musician expects |

- Fixtures are small synthetic `.rpp` projects generated by a script (named tracks, empty or MIDI items, regions, a tempo change, folder and send setups). No large audio in the repo.
- Integration tests are marked and skipped when the bridge isn't reachable.
- CI (GitHub Actions) runs unit and fixture tests only.

## 7. Conventions (defaults in `config/conventions.yaml`)

**Colour:** one colour per instrument family, at most 8 families, Okabe–Ito palette plus grey, same mapping in every project. Rationale: colour coding speeds up visual search only with few, clearly distinct colours used consistently; search slows as more items share a colour and as colours look alike; past about 8 categories colours become hard to tell apart.

| Order | Family | Code | Colour |
| --- | --- | --- | --- |
| 1 | Drums and percussion | DRM | #0072B2 blue |
| 2 | Bass | BAS | #CC79A7 reddish purple |
| 3 | Guitars | GTR | #009E73 bluish green |
| 4 | Keys and synths | KEY | #E69F00 orange |
| 5 | Vocals | VOX | #F0E442 yellow |
| 6 | FX returns | FX | #56B4E9 sky blue |
| 7 | Other buses (not folders) | BUS | #D55E00 vermillion |
| 8 | Reference and utility | REF | #999999 grey |

Folder tracks take their family's colour, not BUS, so a folder reads as its group. BUS is for summing buses that are not folders.

**Naming:** `<CODE> <Part> <Source> <Side>`; family first, fixed word order, short codes, so names sort and scan by group. Examples: `DRM Kick In`, `DRM OH L`, `GTR Rhythm Close L`, `GTR Rhythm Room L`, `BAS DI`, `VOX Lead`, `FX Plate`, `BUS Guitars`. Takes: `<track name> T<n>`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Index drift breaks ops | GUIDs everywhere; resolve inside Lua at apply time |
| Project changed between plan and apply | Base-hash check; re-plan |
| Large plans are slow or flood undo | One Lua call per plan, `PreventUIRefresh`, bulk point insert with a single sort |
| Foldering changes the mix | Routing safety rule; report instead of fold |
| Plugin params without formatted values or with odd ranges | Enum/normalized fallback; tell the user; profile by hand |
| Fork drifts from upstream | Our code in separate dirs; monthly merge; upstream changes reviewed |
| File bridge latency or timeouts on big projects | Measured in Phase 0; paging; one-call snapshot handler |
| `run_script` is powerful | Keep it gated off in normal use; features are named Lua handlers; escape hatch for development only |
| REAPER version differences (lanes are REAPER 7+) | Check version on connect; require 7.x; feature-flag lanes |
| Local models struggle with many tools | `core` profile ≤ 30 tools; high-level tools; short descriptions |

## 9. Definition of done (v1)

- A converted Cubase project goes from messy to tidy in one approved plan, reversible in one undo.
- Section automation on any profiled plugin from labelled regions, verified by readback.
- Every mutating tool has a dry run, a plan, an undo test and an integration test.
- Works through Claude Code and Claude Desktop; `core` profile tried with one local model.
