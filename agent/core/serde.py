"""Strict dataclass <-> plain dict conversion (stdlib only).

`to_dict` turns dataclasses into JSON-safe structures. `from_dict(tp, value)` rebuilds typed
objects from them and rejects unknown keys and wrong primitive types, so a malformed plan or
bridge reply fails loudly instead of being half-accepted.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, get_args, get_origin, get_type_hints


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _fail(path: str, msg: str) -> typing.NoReturn:
    raise ValueError(f"{path or '<root>'}: {msg}")


def from_dict(tp: Any, value: Any, path: str = "") -> Any:
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = get_args(tp)
        if value is None and type(None) in args:
            return None
        inner = [a for a in args if a is not type(None)]
        if len(inner) != 1:
            _fail(path, "ambiguous union type")
        return from_dict(inner[0], value, path)
    if tp is Any:
        return value
    if origin is list:
        if not isinstance(value, list):
            _fail(path, f"expected list, got {type(value).__name__}")
        (item,) = get_args(tp)
        return [from_dict(item, v, f"{path}[{i}]") for i, v in enumerate(value)]
    if origin is dict:
        if value == []:  # Lua's JSON encoder writes an empty table as []
            return {}
        if not isinstance(value, dict):
            _fail(path, f"expected object, got {type(value).__name__}")
        _, vt = get_args(tp)
        return {k: from_dict(vt, v, f"{path}.{k}") for k, v in value.items()}
    if dataclasses.is_dataclass(tp):
        if not isinstance(value, dict):
            _fail(path, f"expected object, got {type(value).__name__}")
        hints = get_type_hints(tp)
        names = {f.name for f in dataclasses.fields(tp)}
        unknown = set(value) - names
        if unknown:
            _fail(path, f"unknown field(s) {sorted(unknown)}")
        kwargs = {
            f.name: from_dict(hints[f.name], value[f.name], f"{path}.{f.name}")
            for f in dataclasses.fields(tp)
            if f.name in value
        }
        try:
            return tp(**kwargs)
        except TypeError as exc:
            _fail(path, str(exc))
    if tp is bool:
        if not isinstance(value, bool):
            _fail(path, f"expected bool, got {type(value).__name__}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(path, f"expected int, got {type(value).__name__}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(path, f"expected number, got {type(value).__name__}")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            _fail(path, f"expected string, got {type(value).__name__}")
        return value
    return value
