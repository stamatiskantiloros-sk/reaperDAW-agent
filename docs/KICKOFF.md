# Kickoff prompts for Claude Code

Run Claude Code inside the repo, on the machine where REAPER is installed, so it can run the integration tests against your live REAPER. Paste one prompt per phase. Start a fresh session for each phase; `CLAUDE.md` and `docs/PLAN.md` carry the context between sessions.

## Phase 0 — Bridge spike and setup

```
Read CLAUDE.md and docs/PLAN.md fully. We are doing Phase 0 only.

1. Fork https://github.com/wretcher207/reaper-daemon into this repo (keep an `upstream` remote), and read its code.
2. Help me install the bridge in my REAPER and confirm it's alive.
3. Run the bridge checklist from PLAN.md section 5 on a scratch project. For anything it can't do, show how we'd add it (named Lua handler or run_script).
4. Do a quick comparison against https://github.com/xDarkzx/Reaper-MCP on the same checklist.
5. Measure snapshot time on a 50-track and a 150-track generated project.
6. Write docs/decisions/0001-bridge.md with the results and a recommendation.

Start in plan mode. Stop at Gate 0 with the manual test steps for me.
```

## Phase 1 — Core

```
Read CLAUDE.md, docs/PLAN.md and docs/decisions/. Phase 0 is approved. Do Phase 1 (core: snapshot, plan, apply, diff, fake bridge, round-trip test).
Start in plan mode, then execute task by task, ticking PLAN.md. Stop at Gate 1 with manual test steps.
```

## Phase 2 — Housekeeping

```
Read CLAUDE.md, docs/PLAN.md and docs/decisions/. Phases 0–1 are approved. Do Phase 2 (housekeeping) using config/conventions.yaml.
Build the naming parser test table from realistic names first, including Cubase-style names (I'll add real ones from my projects to tests/fixtures/names.txt).
Start in plan mode. Stop at Gate 2 with manual test steps.
```

## Phase 3 — Plugin profiles

```
Read CLAUDE.md, docs/PLAN.md and docs/decisions/. Phases 0–2 are approved. Do Phase 3 (plugin profiles and real-unit control).
My most-used plugins are listed in profiles/MY_PLUGINS.md. Start with ReaEQ, then those.
Start in plan mode. Stop at Gate 3 with manual test steps.
```

## Phase 4 — Section automation

```
Read CLAUDE.md, docs/PLAN.md and docs/decisions/. Phases 0–3 are approved. Do Phase 4 (section automation from labelled regions).
My test song with chug/open regions is in tests/fixtures/real/ (a copy; never touch originals).
Start in plan mode. Stop at Gate 4 with manual test steps.
```

## Between phases

- Review the PR at each gate, test in REAPER on copies, then merge.
- If something feels off, tell Claude Code in plain words and ask it to add a test that captures the problem before fixing it.
- Once Phase 1 works, register the server with Claude Code itself (`claude mcp add ...`, command from the README) so you can also *use* the agent from Claude Code, not only build it.
