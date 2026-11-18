# reaper-agent

An MCP server that lets an AI agent tidy REAPER projects and write section-based FX automation, safely: every change is a reviewable **plan**, applied as **one undo step**, addressed by **GUIDs**. Status: Phase 1 (core: snapshot, plan, apply, undo). See `docs/PLAN.md` for the roadmap and `docs/decisions/` for the design decisions.

Built on a vendored copy of [Reaper Daemon](https://github.com/wretcher207/reaper-daemon) (MIT) under `upstream/`, with one small documented patch (`patches/0001-extra-handlers.patch`).

## Setup (macOS; REAPER 7.x)

1. `uv sync` (Python 3.10+; dev tools only, the runtime has no dependencies).
2. Install the Reaper Daemon auto-loader and restart REAPER once. Write gates (render, save, preferences, scripts) stay off:
   `python3 upstream/setup/install.py --no-disk-writes`
3. Register our handlers and reload the bridge: `uv run python scripts/install_agent.py`
   (undo with `--uninstall`; the Daemon installer also has `--uninstall`).
4. Check: `uv run python -c "from agent.bridge_client.daemon import DaemonBridge as B; print(B().status())"`

## Use from Claude Code

```bash
claude mcp add reaper-agent -- uv run --directory "$(pwd)" python -m agent.server
```

Tools (profile `core`, set `REAPER_AGENT_PROFILE=full` for more): `get_project_overview`, `get_snapshot`, `plan_rename_track`, `apply_plan`, `discard_plan`, `undo_last_plan`. A plan changes nothing until `apply_plan`; `Ctrl+Z` in REAPER undoes it in one step.

## Tests

- `uv run pytest tests/unit tests/lua -q`: no REAPER needed.
- `uv run pytest tests/integration -q`: needs REAPER running with the bridge and our handlers; opens a fresh copy of a generated fixture in a **new project tab** (macOS) and leaves it open for you to close without saving. Never run it with unsaved work in the active tab you care about.
- Fixtures: `uv run python scripts/make_fixtures.py` writes synthetic projects to `tests/fixtures/generated/` (gitignored).

## Safety notes

The repo is public. Keep real projects, audio and personal paths out of it. `upstream/bridge/bridge_config.json` (machine-specific) is gitignored.
