"""Group-centric node declaration ABI."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .assets import AssetIn
from .ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn
from .readiness import Readiness

@dataclass(frozen=True)
class Group:
    """One independently callable node interface."""
    name: str
    inputs: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    defaults: dict[str, Any] = field(default_factory=dict)
    handler: Any = None
    readiness: Readiness | None = None

@dataclass(frozen=True)
class NodeType:
    name: str
    data_in: tuple[DataIn, ...] = ()
    data_out: tuple[DataOut, ...] = ()
    trigger_in: tuple[TriggerIn, ...] = ()
    signal_in: tuple[SignalIn, ...] = ()
    signal_out: tuple[SignalOut, ...] = ()
    asset_in: tuple[AssetIn, ...] = ()
    state_defaults: dict[str, Any] = field(default_factory=dict)
    init_defaults: dict[str, Any] = field(default_factory=dict)
    groups: tuple[Group, ...] = ()
    tags: tuple[str, ...] = ()
    init: Any = None
    @property
    def is_signal_node(self) -> bool: return bool(self.signal_out)
    def port(self, name: str):
        for p in (*self.data_in, *self.trigger_in, *self.signal_in):
            if p.name == name: return p
        raise KeyError(f"node type {self.name!r} has no input port {name!r}")
    def out_port(self, name: str):
        for p in (*self.data_out, *self.signal_out):
            if p.name == name: return p
        raise KeyError(f"node type {self.name!r} has no output port {name!r}")
    def group(self, name: str) -> Group:
        for g in self.groups:
            if g.name == name: return g
        raise KeyError(f"node type {self.name!r} has no group {name!r}")
    def to_dict(self) -> dict:
        return {"name": self.name, "data_in": [p.__dict__.copy() for p in self.data_in],
                "data_out": [p.name for p in self.data_out], "trigger_in": [p.name for p in self.trigger_in],
                "signal_in": [p.name for p in self.signal_in], "signal_out": [p.name for p in self.signal_out],
                "asset_in": [{"name": p.name, "type": p.type.__name__ if p.type else None} for p in self.asset_in],
                "state_defaults": dict(self.state_defaults), "init_defaults": dict(self.init_defaults), "tags": list(self.tags),
                "groups": [{"name": g.name, "inputs": list(g.inputs), "triggers": list(g.triggers), "outputs": list(g.outputs), "defaults": dict(g.defaults), "has_handler": g.handler is not None, "readiness": repr(g.readiness)} for g in self.groups],
                "has_init": self.init is not None, "has_tick": False, "is_signal_node": self.is_signal_node}
