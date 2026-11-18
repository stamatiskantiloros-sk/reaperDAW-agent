# Delegation plan — who builds what, and how to keep tokens low

Companion to `PLAN.md` (what to build) and `KICKOFF.md` (phase prompts). This file says **how the work is split across sub-agents**. If it conflicts with `CLAUDE.md`, `CLAUDE.md` wins.

## 1. Principles

1. **Contract first, then fan out.** The expensive thinking is the data model (`Snapshot`, `Plan`, the op vocabulary, the bridge interface). The main session writes and freezes these in Phase 1 step 1.0. After that, most work is well-specified and can go to cheaper sub-agents working from the contract.
2. **Pure Python goes wide, REAPER work goes narrow.** Anything testable with the fake bridge or fixtures (classifier, naming, curves, unit parser, health report) runs in parallel. Anything that touches the live REAPER (Lua handlers, integration tests, discovery) runs **one agent at a time**: two agents on one REAPER instance will collide.
3. **Task cards, not the whole plan.** `PLAN.md` is about 6k tokens. A sub-agent needs a 300-word card: goal, files it owns, interfaces it may import, tests that define "done". `CLAUDE.md` is loaded automatically.
4. **Tests are the acceptance criteria.** The card says "done = these tests pass and `ruff` is clean". The main session checks the test result, not every line of code.
5. **Disjoint files per agent.** Each parallel agent owns one module directory and its test file. Shared files (`models.py`, `server.py`) are touched only by the main session. Parallel writers use `isolation: "worktree"` (needs the git repo from Phase 0).
6. **Short reports.** Every card ends with "Report in under 150 words: files changed, tests run and result, anything you could not do or had to assume." No code in the report.

## 2. Model assignment

| Work type | Who | Why |
| --- | --- | --- |
| Contracts, Lua `apply_plan` and undo semantics, REAPER API verification, ADRs, gate write-ups, final integration and review | **Main session** (strongest model) | Wrong here = wrong everywhere; needs judgment |
| Well-specified pure-Python modules with tests (classifier, parser, curve builder, unit conversion, planners, MCP wiring) | **Sub-agent, Sonnet** | Bounded, test-defined, cheap |
| Read-only questions about upstream code ("does ReaperMCP expose item lanes? where?") | **Explore agent, Haiku** | Returns a conclusion, not file dumps |
| Mechanical work (fixture generator, ticking boxes, YAML from discovery JSON, boilerplate tests, invariant lints) | **Sub-agent, Haiku** | Low risk, easy to verify |

Escalate a task to the main session if a sub-agent fails the same test twice.

## 3. Task breakdown

Legend: **R** = needs live REAPER (serial). **P** = pure Python (parallel-safe). Wave letters group tasks that can run at the same time.

### Phase 0 — Bridge spike (mostly serial, user in the loop)

| ID | Task | Who | Wave | Notes |
| --- | --- | --- | --- | --- |
| 0.1 | Create repo, fork Reaper Daemon, install bridge, `status` alive | Main + user | — | R, needs user's machine |
| 0.2 | Map Reaper Daemon: command set, transport, timeouts, gating switches (1 page) | Explore/Haiku | A | Reads fork code only |
| 0.3 | Answer checklist items 1–12 for ReaperMCP from its code (yes / no / how) | Explore/Sonnet | A | Table output, no live run |
| 0.4 | `scripts/make_fixtures.py`: generate 50- and 150-track `.rpp` plus a small routed/regions/tempo-change project | Haiku | A | P |
| 0.5 | Run checklist live, time snapshots, write ADR 0001 | Main | B (after A) | R |

**Gate 0.**

### Phase 1 — Core

| ID | Task | Who | Wave | Depends on |
| --- | --- | --- | --- | --- |
| 1.0 | **Freeze contracts:** `agent/core/models.py` (Snapshot, Plan, Op union with all 17 ops), `bridge_client` Protocol, snapshot-hash rule | Main | — | Gate 0 |
| 1.1 | Fake bridge (in-memory project implementing the Protocol) + snapshot hash + diff engine + unit tests | Sonnet | A | 1.0 |
| 1.2 | MCP server skeleton, `core`/`full` tool profiles via env var, tool wiring against the fake bridge | Sonnet | A | 1.0 |
| 1.3 | Invariant guard test: no index-based ops in the schema; no `DeleteTrack*` / `DeleteTake*` / file removal in `lua/agent/`; every op type has a round-trip test registered | Haiku | A | 1.0 |
| 1.4 | Lua `agent.snapshot` (one round trip, paging fallback) | Main or Sonnet | R1 | 1.0 |
| 1.5 | Lua `agent.apply_plan`: GUID resolution, one undo block, per-op results, original-value ext state | Main | R2 | 1.4 |
| 1.6 | Lua op handlers in three groups: track ops, take/lane/region/ext ops, fx/envelope ops | Sonnet, one group at a time | R3–R5 | 1.5 |
| 1.7 | Integration test: apply → undo → identical snapshot, over every op | Sonnet, reviewed by Main | R6 | 1.6 |

**Gate 1.** Wave A (1.1–1.3) runs while the R-chain (1.4 onward) is serialized on REAPER.

### Phase 2 — Housekeeping

| ID | Task | Who | Wave | Notes |
| --- | --- | --- | --- | --- |
| 2.0 | Naming test table first (30+ real names) in `tests/fixtures/names.txt`; user adds Cubase names | Haiku, user adds | — | P |
| 2a | Classifier (`agent/housekeeping/classify.py`) | Sonnet | A | P |
| 2b | Name parser + generator + take renaming | Sonnet | A | P, driven by 2.0 |
| 2c | Colours, order, folders planner with the routing safety rule | Sonnet | A | P, tests on routed fixtures |
| 2e | `health_report` | Haiku | A | P, read-only |
| 2d-i | Verify REAPER 7 fixed-lane API names against docs; prefer native "takes to lanes" action | Main | A | Don't trust memory (see `CLAUDE.md`) |
| 2d-ii | Takes/lanes planner and flags | Sonnet | B | P, after 2d-i |
| 2d-iii | Any new Lua handlers for lanes | Main or Sonnet | R | R |
| 2f | `plan_tidy` composes 2a–2e into one plan; integration round trip | Main | C | After A and B merge |

**Gate 2.**

### Phase 3 — Plugin profiles

| ID | Task | Who | Wave | Notes |
| --- | --- | --- | --- | --- |
| 3a | Unit parser and real-unit → normalized conversion (tables, interpolation, bisection) with synthetic-curve tests | Sonnet | A | P |
| 3b | Profile YAML schema, store, role matching, drafting rules ("Band N …") | Sonnet | A | P |
| 3c | Lua discovery and sampling handlers (formatted-value probing, restore, discarded undo) | Main | R | R |
| 3d | Run discovery on ReaEQ, ReaComp and the user's plugins (`profiles/MY_PLUGINS.md` must be filled first); save raw JSON | Main + user | R | R |
| 3e | Turn discovery JSON into profile YAMLs for the user to confirm | Haiku | B | P |
| 3f | Tools: `list_fx`, `describe_fx`, `set_fx_value`, `draft_plugin_profile`, `save_plugin_profile` | Sonnet | C | After 3a–3c |

**Gate 3.**

### Phase 4 — Section automation

| ID | Task | Who | Wave | Notes |
| --- | --- | --- | --- | --- |
| 4a | Curve builder: ramps through the tempo map, merges, overlaps, edges | Sonnet | A | P, the most test-heavy module |
| 4b | Section reader (regions → labels, time-selection ad-hoc section) | Haiku | A | P |
| 4c | Lua envelope writer ops hardening (ensure/clear range/bulk insert/automation mode) | Main or Sonnet | R | R |
| 4d | `plan_section_automation`, readback verification, idempotency test | Sonnet, reviewed by Main | B | After 4a–4c |

**Gate 4.**

### Phase 5 — Hardening

Run in the main session. Delegate only mechanical items: README install steps (Haiku), tool-description trimming (Sonnet), monthly upstream merge diff summary (Explore/Haiku).

## 4. Phase order (decided 2026-11-18)

Strict phase order, as in `CLAUDE.md`: no work on a phase starts before the previous gate is approved. Modules that depend only on the frozen contract (2b naming parser, 3a unit conversion, 4a curve builder) stay in their own phase's Wave A. Parallelism happens only inside a phase.

## 5. Token economy rules

1. **Fresh session per phase** (already in `KICKOFF.md`); sub-agents start cold, so briefs must be self-contained but short.
2. **Cards live in files.** Main writes `docs/tasks/<id>.md` per task from the template below; the sub-agent prompt is "do `docs/tasks/2a.md`". Cards are reviewable and reusable if the agent fails.
3. **Never paste file contents into briefs.** Name paths; the agent reads what it needs.
4. **Review by evidence.** After a sub-agent finishes: run `pytest -q <its tests>` and `ruff check`, look at `git diff --stat`, and read only the public interface and any invariant-sensitive code (GUID handling, deletion, undo block). Do not re-read whole modules.
5. **Quiet test output.** Always `pytest -q`, `| tail -30`. Failures only.
6. **Explore agents for lookups.** Questions about upstream code or REAPER docs go to a Haiku Explore agent that returns a one-paragraph answer.
7. **No re-planning in sub-agents.** Cards say "do not redesign; if the contract is wrong, stop and report".
8. **Parallel only when files are disjoint.** Otherwise merge conflicts cost more than the parallelism saves.
9. **Keep `PLAN.md` ticks in the main session only**, in one batch per merge, not by every agent.

## 6. Task card template (`docs/tasks/<id>.md`)

```markdown
# <id> — <title>
Goal: <one sentence>
Owns (may create/edit): <paths>
Read-only context: <paths, e.g. agent/core/models.py, config/conventions.yaml §families>
Do not touch: everything else, especially models.py and upstream files.
Behaviour: <5–10 bullets copied from PLAN.md for this task only>
Done when: <test files + commands, e.g. `pytest -q tests/unit/test_classify.py` passes; `ruff check agent/housekeeping`>
Rules: CLAUDE.md applies. GUIDs not indices. Never delete user material. No new dependencies without asking.
If blocked or the contract looks wrong: stop and report; do not redesign.
Report (under 150 words): files changed, test result, assumptions, open issues.
```

## 7. Setup checklist before Phase 0

- [ ] Decide the repo root: this `reaper-agent-kit/` folder becomes the repo. The copy of `PLAN.md` one level up is identical to `docs/PLAN.md`; keep only the one in `docs/`.
- [ ] Fill in `profiles/MY_PLUGINS.md` (needed by task 3d, but cheap to do now).
- [ ] Confirm REAPER 7.x is installed and a scratch project can be used.
- [x] Early starts before gates: decided no, keep phase order.
