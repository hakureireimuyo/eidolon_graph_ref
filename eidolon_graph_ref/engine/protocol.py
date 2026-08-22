from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
@dataclass
class GroupContext:
    group: str
    data_in: dict[str, Any]
    state: dict[str, Any]
    config: dict[str, Any]
    assets: dict[str, Any] = field(default_factory=dict)
@dataclass
class GroupOutput:
    data_out: dict[str, Any] = field(default_factory=dict)
    signal_out: dict[str, bool] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
@dataclass
class InitContext:
    config: dict[str, Any]
    assets: dict[str, Any] = field(default_factory=dict)
