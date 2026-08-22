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
class DataOut: name: str
@dataclass(frozen=True)
class TriggerIn: name: str
@dataclass(frozen=True)
class SignalIn: name: str
@dataclass(frozen=True)
class SignalOut: name: str
def data_in_names(ports: list[DataIn]) -> list[str]: return [p.name for p in ports]
