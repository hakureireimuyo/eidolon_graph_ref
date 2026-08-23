"""GraphInstance: the only formal build entry point and runtime instance.

Raw construction is rejected at the constructor level (`_internal` guard);
`GraphInstance.build()` is the single sanctioned pipeline:
validate → resolve assets (declaration must be satisfied) → init hooks →
index build → port initial states.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from ..model.assets import AssetRef
from ..model.graph import SLOT_DATA, SLOT_SIGNAL, GraphDefinition
from ..model.ports import APPEND
from .executor import Executor
from .port_state import DataPortState, SignalPortState, TriggerPortState
from .protocol import InitContext
from .timeline import Timeline


class AssetResolver(Protocol):
    def resolve(self, ref: AssetRef) -> Any: ...


@dataclass(frozen=True)
class BuildReport:
    ok: bool
    errors: tuple[str, ...]
    instance: "GraphInstance | None" = None


class GraphInstance:
    def __init__(self, definition, types, assets=None, init_states=None, asset_refs=None, *, _internal=False):
        if not _internal:
            raise TypeError("use GraphInstance.build()")
        self.definition = definition
        self.types = types
        self.run_no = 0
        self.timeline = Timeline()
        self.assets = assets or {}
        self.asset_refs = asset_refs or {}
        self._init_states = init_states or {}
        self.node_states = {}
        self.configs = {}
        self.data_states = {}
        self.signal_states = {}
        self.trigger_states = {}
        self.in_index = {}
        self.out_index = {}
        self._build()

    @classmethod
    def build(cls, definition, types, asset_resolver=None):
        from ..model.validate import validate

        errors = list(validate(definition, types).errors)
        if errors:
            return BuildReport(False, tuple(errors))
        assets = {}
        init_states = {}
        asset_refs = {}
        for nid, spec in definition.nodes.items():
            nt = types[spec.type]
            for a in nt.asset_in:
                ref = definition.asset_bindings.get((nid, a.name))
                if ref is None:
                    errors.append(f"node {nid!r}: asset slot {a.name!r} is not bound")
                    continue
                if asset_resolver is None:
                    errors.append(f"node {nid!r}: no asset resolver")
                    continue
                try:
                    cap = asset_resolver.resolve(ref)
                except Exception as e:
                    errors.append(f"node {nid!r}: asset resolve failed: {e}")
                    continue
                # 构建期类型验证(graph-assets.md §8:lookup → resolve → isinstance → 注入;
                # §2-5:声明类型即 Capability 接口,构建期类型检查即不变量执行点)
                if a.type is not None:
                    try:
                        ok = isinstance(cap, a.type)
                    except Exception as e:
                        errors.append(f"node {nid!r}: asset slot {a.name!r}: type check failed: {e}")
                        continue
                    if not ok:
                        errors.append(
                            f"node {nid!r}: asset slot {a.name!r} resolved to {type(cap).__name__}, expected {a.type.__name__}"
                        )
                        continue
                assets.setdefault(nid, {})[a.name] = cap
                asset_refs.setdefault(nid, {})[a.name] = ref.asset_id
            if nt.init:
                try:
                    delta = nt.init(InitContext({**nt.init_defaults, **spec.config.get("init", {})}, dict(assets.get(nid, {})))) or {}
                    unknown = set(delta) - set(nt.state_defaults)  # §7:未知状态字段 = 构建期错误
                    if unknown:
                        errors.append(f"node {nid!r}: init returned unknown state fields: {sorted(unknown)}")
                        continue
                    init_states[nid] = {**deepcopy(nt.state_defaults), **deepcopy(delta)}
                except Exception as e:
                    errors.append(f"node {nid!r}: init raised {type(e).__name__}: {e}")
        if errors:
            return BuildReport(False, tuple(errors))
        return BuildReport(True, (), cls(definition, types, assets, init_states, asset_refs, _internal=True))

    def _build(self):
        for w in self.definition.wires:
            self.in_index[(w.dst_node, w.dst_port, w.dst_slot)] = w
            self.out_index.setdefault((w.src_node, w.src_port), []).append(w)
        for nid, spec in self.definition.nodes.items():
            nt = self.types[spec.type]
            self.configs[nid] = {
                "groups": dict(spec.config.get("groups", {})),
                "ports": dict(spec.config.get("ports", {})),
                "init": dict(spec.config.get("init", {})),
            }
            self.node_states[nid] = deepcopy(self._init_states.get(nid, nt.state_defaults))
            self.data_states[nid] = {}
            self.signal_states[nid] = {s.name: SignalPortState() for s in nt.signal_in}
            self.trigger_states[nid] = {t.name: TriggerPortState() for t in nt.trigger_in}
            for p in nt.data_in:
                wired = (nid, p.name, SLOT_DATA) in self.in_index
                value = self.configs[nid]["ports"].get(p.name, p.default)
                value = [] if p.cache == APPEND and value is None else value
                self.data_states[nid][p.name] = DataPortState(p.cache, value, not wired, False, [], wired)

    def run(self, injections=None):
        Executor(self.types).run(self, injections or [])

    def observable_state(self):
        return {
            nid: {
                "type": self.definition.nodes[nid].type,
                "state": deepcopy(self.node_states[nid]),
                "config": deepcopy(self.configs[nid]),
                "assets": {slot: {"ref": aid, "resolved": True} for slot, aid in self.asset_refs.get(nid, {}).items()},
                "data_in": {n: {"value": s.value, "has_value": s.has_value, "pending": s.pending} for n, s in self.data_states[nid].items()},
                "trigger_in": {n: {"pending": s.pending, "payload": s.payload if s.has_payload else None} for n, s in self.trigger_states[nid].items()},
                "signal_in": {n: {"level": s.level, "pending": s.pending} for n, s in self.signal_states[nid].items()},
            }
            for nid in self.definition.node_order()
        }
