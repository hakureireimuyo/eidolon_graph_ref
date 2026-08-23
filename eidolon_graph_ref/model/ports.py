"""Port declarations for the group-centric ABI.

A port is a declaration × runtime mode pair: the declaration here is static
contract; the runtime interpretation lives in engine/port_state.py and
engine/node_semantics.py (see docs/graph-node-protocol.md §2).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

REPLACE = "replace"
APPEND = "append"


@dataclass(frozen=True)
class DataIn:
    name: str
    default: Any = None
    cache: str = REPLACE
    signal: str | None = None


@dataclass(frozen=True)
class DataOut:
    name: str


@dataclass(frozen=True)
class TriggerIn:
    name: str


@dataclass(frozen=True)
class SignalIn:
    name: str


@dataclass(frozen=True)
class SignalOut:
    name: str
