"""Helpers for testing Lua files in-process with lupa (no REAPER needed).

`load_lua` runs a Lua file and returns the module table it returns. `syntax_check` only parses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lupa


class LuaLoadError(RuntimeError):
    """The Lua file could not be loaded (syntax error or unreadable)."""


def _runtime() -> lupa.LuaRuntime:
    return lupa.LuaRuntime(unpack_returned_tuples=True)


_LOADFILE_PAIR = "return function(p) local f, e = loadfile(p) return f, e end"


def _loadfile(runtime: lupa.LuaRuntime, path: str | Path) -> tuple[Any, Any]:
    """Compile a Lua file without running it. Returns (chunk, err): one of them is None."""
    wrapper = runtime.execute(_LOADFILE_PAIR)
    result = wrapper(str(path))
    if isinstance(result, tuple):
        return result[0], result[1] if len(result) > 1 else None
    return result, None  # a lone value: the chunk, no error


def _to_lua(runtime: lupa.LuaRuntime, value: Any) -> Any:
    if isinstance(value, dict):
        return runtime.table_from({k: _to_lua(runtime, v) for k, v in value.items()})
    return value


def load_lua(path: str | Path, reaper: dict[str, Any] | None = None) -> Any:
    """Run a Lua file and return what it returns.

    `reaper` is an optional fake `reaper` global: a dict of name -> Python callable (or nested
    dict), exposed to Lua as a table. Raises LuaLoadError on a syntax error.
    """
    runtime = _runtime()
    if reaper is not None:
        runtime.globals().reaper = _to_lua(runtime, reaper)
    chunk, err = _loadfile(runtime, path)
    if chunk is None:
        raise LuaLoadError(str(err))
    return chunk()


def syntax_check(path: str | Path) -> str | None:
    """Parse a Lua file without executing it. Returns None if it parses, else the error text."""
    _, err = _loadfile(_runtime(), path)
    if err is not None:
        return str(err)
    return None
