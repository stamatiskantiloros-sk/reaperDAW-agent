"""Syntax and load checks for the Lua sources, plus a self-test of the lua_helpers harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.lua.lua_helpers import LuaLoadError, load_lua, syntax_check

REPO = Path(__file__).resolve().parents[2]
LUA_AGENT_FILES = sorted((REPO / "lua" / "agent").glob("*.lua"))
UPSTREAM_AUTOMATION = REPO / "upstream" / "bridge" / "automation.lua"


@pytest.mark.parametrize("path", LUA_AGENT_FILES, ids=lambda p: p.name)
def test_agent_lua_parses(path):
    assert syntax_check(path) is None


def test_no_agent_lua_yet():
    if LUA_AGENT_FILES:
        pytest.skip("lua/agent/*.lua exists; per-file syntax tests cover it")
    pytest.skip("no lua/agent/*.lua yet")


def test_upstream_automation_parses_as_sanity_check_of_helper():
    assert UPSTREAM_AUTOMATION.is_file()
    assert syntax_check(UPSTREAM_AUTOMATION) is None


def test_helper_self_test_on_inline_snippet(tmp_path):
    good = tmp_path / "good.lua"
    good.write_text(
        "local M = {}\n"
        "function M.add(a, b) return a + b end\n"
        "function M.say(x) reaper.ShowConsoleMsg(x) return true end\n"
        "return M\n",
        encoding="utf-8",
    )
    assert syntax_check(good) is None

    calls = []
    module = load_lua(good, reaper={"ShowConsoleMsg": calls.append})
    assert module["add"](2, 3) == 5
    assert module["say"]("hi") is True
    assert calls == ["hi"]


def test_helper_reports_syntax_errors(tmp_path):
    bad = tmp_path / "bad.lua"
    bad.write_text("local = = 1\n", encoding="utf-8")
    assert syntax_check(bad) is not None
    with pytest.raises(LuaLoadError):
        load_lua(bad)
