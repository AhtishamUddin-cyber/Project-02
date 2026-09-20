"""Internal serialization helpers shared by every contract in this package.

This module is private (leading underscore) and is deliberately NOT part of
the public contract list requested for this phase — it exists only so that
every dataclass's JSON round-trip is a two-line `to_dict`/`from_dict` call
instead of ~20 repeated lines of enum/datetime/nested-dataclass handling in
every single model file. It has no external dependencies (stdlib only),
consistent with "contracts/ must be dependency-light."

Handles, generically, via each dataclass's own type hints:
  - Enum members  <-> their .value string
  - datetime      <-> ISO-8601 string
  - nested frozen dataclasses (that also mix in JSONSerializable) <-> dict
  - list / tuple (fixed-arity or variable-arity via Tuple[X, ...]) <-> JSON array
  - dict (including Enum-typed keys, which JSON requires as strings) <-> JSON object
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints


def _is_optional(tp: Any) -> bool:
    return get_origin(tp) is Union and type(None) in get_args(tp)


def _unwrap_optional(tp: Any) -> Any:
    args = [a for a in get_args(tp) if a is not type(None)]
    return args[0] if len(args) == 1 else tp


def _enum_key(k: Any) -> Any:
    return k.value if isinstance(k, Enum) else k


def encode_value(value: Any) -> Any:
    """Recursively convert a Python value into a JSON-safe primitive."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return {f.name: encode_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    if isinstance(value, dict):
        return {_enum_key(k): encode_value(v) for k, v in value.items()}
    return value


def decode_value(tp: Any, raw: Any) -> Any:
    """Reconstruct a value of annotated type `tp` from JSON-safe primitive `raw`."""
    if raw is None:
        return None
    if _is_optional(tp):
        tp = _unwrap_optional(tp)

    origin = get_origin(tp)

    if dataclasses.is_dataclass(tp):
        return tp.from_dict(raw)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(raw)
    if tp is datetime:
        return datetime.fromisoformat(raw)

    if origin is list:
        (inner,) = get_args(tp)
        return [decode_value(inner, v) for v in raw]

    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            inner = args[0]
            return tuple(decode_value(inner, v) for v in raw)
        return tuple(decode_value(a, v) for a, v in zip(args, raw))

    if origin is dict:
        key_tp, val_tp = get_args(tp)
        out = {}
        for k, v in raw.items():
            key = key_tp(k) if isinstance(key_tp, type) and issubclass(key_tp, Enum) else k
            out[key] = decode_value(val_tp, v)
        return out

    return raw


def dataclass_to_dict(obj: Any) -> dict:
    return {f.name: encode_value(getattr(obj, f.name)) for f in dataclasses.fields(obj)}


def dataclass_from_dict(cls: type, data: dict) -> Any:
    hints = get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue  # absent key -> let the dataclass's own default apply
        kwargs[f.name] = decode_value(hints[f.name], data[f.name])
    return cls(**kwargs)


class JSONSerializable:
    """Mixin providing to_dict/from_dict/to_json/from_json to a frozen dataclass.

    Not a dataclass itself, and defines no __init__ of its own, so it composes
    cleanly with @dataclass(frozen=True) on the subclass.
    """

    def to_dict(self) -> dict:
        return dataclass_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict):
        return dataclass_from_dict(cls, data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str):
        return cls.from_dict(json.loads(s))
