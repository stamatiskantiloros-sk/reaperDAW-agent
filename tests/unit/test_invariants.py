"""Cheap automatic checks for the non-negotiable rules in CLAUDE.md.

These are static scans: they read dataclass fields, op declarations and Lua source text. They
never touch REAPER.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path

import pytest

from agent.core import models, ops, plan
from agent.core.ops import OPS, OpError, op_from_dict
from tests.unit.test_core_contract import EXAMPLES

REPO = Path(__file__).resolve().parents[2]
LUA_AGENT_DIR = REPO / "lua" / "agent"

# --- rule 3: GUIDs, never indices -------------------------------------------------------------

# (class name, field name) pairs that may contain "index"/"idx"/"id" by design.
ID_LIKE_ALLOW = {
    ("Plan", "id"),  # plan identifier, not an object index
    ("OpResult", "index"),  # position in Plan.ops, not a REAPER index
    ("ApplyResult", "plan_id"),  # plan identifier, same kind as Plan.id
    ("Marker", "number"),  # REAPER's display number (documented in models.py)
}
ID_TOKENS = {"id", "ids", "idx", "index"}


def _is_id_like(name: str) -> bool:
    lowered = name.lower()
    if "index" in lowered or "idx" in lowered:
        return True
    return any(token in ID_TOKENS for token in lowered.split("_"))


def _dataclasses_in(module) -> list[type]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if dataclasses.is_dataclass(obj) and obj.__module__ == module.__name__
    ]


def _id_like_violations(modules) -> list[str]:
    found = []
    for module in modules:
        for cls in _dataclasses_in(module):
            for f in dataclasses.fields(cls):
                if _is_id_like(f.name) and (cls.__name__, f.name) not in ID_LIKE_ALLOW:
                    found.append(f"{cls.__module__}.{cls.__name__}.{f.name}")
    return found


def test_no_index_or_id_like_dataclass_fields():
    assert _dataclasses_in(models), "scan found no dataclasses; the scan is broken"
    assert _id_like_violations([models, ops, plan]) == []


def test_id_like_scan_catches_a_planted_index_field():
    # Non-vacuity check: the scanner must flag a field it is supposed to reject.
    @dataclasses.dataclass
    class Planted:
        track_index: int = 0
        idx: int = 0
        guid: str = ""

    planted = [f.name for f in dataclasses.fields(Planted) if _is_id_like(f.name)]
    assert planted == ["track_index", "idx"]


# --- rule 3 at the op boundary: every op names its object ids in `targets` ----------------------

OBJECT_ID_FIELDS = {"track", "item", "take", "fx", "before", "region"}


@pytest.mark.parametrize("op_type", sorted(OPS))
def test_every_op_declares_its_object_id_fields(op_type):
    cls = OPS[op_type]
    field_names = {f.name for f in dataclasses.fields(cls)}
    undeclared = (field_names & OBJECT_ID_FIELDS) - set(cls.targets)
    if op_type == "set_ext_state":
        # set_ext_state names its object in `target` (scope-dependent), checked in validate().
        assert "target" in field_names
        undeclared -= {"target"}
    assert not undeclared, f"{op_type} has object-id fields not listed in targets: {undeclared}"


def _with_id(example, field_name, value):
    data = example.to_dict()
    data[field_name] = value
    return data


@pytest.mark.parametrize("op_type", sorted(OPS))
@pytest.mark.parametrize("bad_value", [3, "3"], ids=["int", "str"])
def test_every_op_rejects_index_like_object_ids(op_type, bad_value):
    example = next(e for e in EXAMPLES if type(e).type == op_type)
    cls = type(example)
    fields = list(cls.targets)
    if not fields and op_type != "set_ext_state":
        pytest.skip(f"{op_type} has no object-id targets (its refs are aliases it defines)")
    if op_type == "set_ext_state":
        fields = ["target"]  # scope "track" in EXAMPLES, so target is required
    assert fields, f"{op_type} declares no targets and has no custom target check"
    for name in fields:
        with pytest.raises(OpError):
            op_from_dict(_with_id(example, name, bad_value))


# --- rule 4 and CLAUDE.md "Never delete user material": Lua deny-list -------------------------

DENY_RULES = [
    (re.compile(r"\bDeleteTrack\s*\("), "DeleteTrack("),
    (re.compile(r"\bDeleteTrackMediaItem\b"), "DeleteTrackMediaItem"),
    (re.compile(r"\bDeleteTakeFromItem\b"), "DeleteTakeFromItem"),
    (re.compile(r"\bos\.remove\b"), "os.remove"),
    (re.compile(r"\bos\.execute\b"), "os.execute"),
    (re.compile(r"\bio\.popen\b"), "io.popen"),
    (re.compile(r"\bio\.open\s*\([^)]*,\s*[\"'](?:[wa]|r\+)[bt+]*[\"']"), "io.open write mode"),
    (re.compile(r"\bExecProcess\b"), "reaper.ExecProcess"),
    (re.compile(r"SaveProject"), "SaveProject / Main_SaveProject"),
]
ENVELOPE_DELETE = re.compile(r"\bDeleteEnvelopePointRange(?:Ex)?\b")
ENVELOPE_DELETE_ALLOWED_IN = "ops_fx.lua"


def lua_violations(text: str, filename: str) -> list[str]:
    """Return "line N: rule" strings for every deny-listed call in a Lua source text.

    Whole-line `--` comments are skipped. Any other line is scanned as code.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("--"):
            continue
        for pattern, label in DENY_RULES:
            if pattern.search(line):
                found.append(f"{filename}:{lineno}: {label}")
        if ENVELOPE_DELETE.search(line) and filename != ENVELOPE_DELETE_ALLOWED_IN:
            found.append(
                f"{filename}:{lineno}: envelope point delete outside {ENVELOPE_DELETE_ALLOWED_IN}"
            )
    return found


def _lua_files() -> list[Path]:
    return sorted(LUA_AGENT_DIR.glob("*.lua")) if LUA_AGENT_DIR.is_dir() else []


def test_lua_deny_list_is_clean():
    violations = []
    for path in _lua_files():
        violations += lua_violations(path.read_text(encoding="utf-8"), path.name)
    assert violations == []


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ("reaper.DeleteTrack(tr)", "DeleteTrack("),
        ("reaper.DeleteTrackMediaItem(tr, it)", "DeleteTrackMediaItem"),
        ("os.remove(path)", "os.remove"),
        ('local f = io.open(p, "wb")', "io.open write mode"),
        ('local f = io.open(p, "a")', "io.open write mode"),
        ("reaper.Main_SaveProject(0, false)", "SaveProject"),
        ("reaper.ExecProcess(cmd, 0)", "ExecProcess"),
        ("reaper.DeleteEnvelopePointRange(env, 0, 1)", "envelope point delete"),
    ],
)
def test_deny_list_catches_planted_violations(snippet, expected):
    # Non-vacuity check: the scanner must catch each rule when it appears in code.
    hits = lua_violations(snippet, "ops_other.lua")
    assert hits and any(expected in h for h in hits), hits


@pytest.mark.parametrize(
    "snippet",
    [
        "-- reaper.DeleteTrack(tr)",
        'local f = io.open(p, "r")',
        'local f = io.open(p, "rb")',
    ],
)
def test_deny_list_allows_comments_and_read_modes(snippet):
    assert lua_violations(snippet, "ops_other.lua") == []


def test_envelope_point_delete_allowed_only_in_ops_fx():
    snippet = "reaper.DeleteEnvelopePointRangeEx(env, 0, 0, 1)"
    assert lua_violations(snippet, ENVELOPE_DELETE_ALLOWED_IN) == []
    assert lua_violations(snippet, "ops_track.lua") != []


# --- every op has a Lua handler (skipped until lua/agent/ops_*.lua exists) ---------------------


def _lua_code_text(paths: list[Path]) -> str:
    lines = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("--"):
                lines.append(line)
    return "\n".join(lines)


def test_every_op_has_a_lua_handler():
    ops_files = sorted(LUA_AGENT_DIR.glob("ops_*.lua")) if LUA_AGENT_DIR.is_dir() else []
    if not ops_files:
        pytest.skip("no lua/agent/ops_*.lua yet; Lua op handlers are not written")
    text = _lua_code_text(ops_files)
    missing = [t for t in sorted(OPS) if not re.search(rf"\bop_{t}\b", text)]
    assert missing == [], f"no op_<type> handler in lua/agent/ops_*.lua for: {missing}"


# --- OPS and EXAMPLES agree ----------------------------------------------------------------------


def test_examples_cover_every_op_type_exactly():
    example_types = [type(e).type for e in EXAMPLES]
    assert len(example_types) == len(set(example_types)), "EXAMPLES has duplicate op types"
    assert set(example_types) == set(OPS)
