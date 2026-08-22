"""Declaration, config, and wire validation for the group-centric ABI."""
from __future__ import annotations
from dataclasses import dataclass
from .graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER, Wire
from .ports import DataIn, DataOut, SignalIn, SignalOut, TriggerIn
@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]; warnings: tuple[str, ...]
    @property
    def ok(self): return not self.errors
class ValidationError(Exception):
    def __init__(self, errors): self.errors = errors; super().__init__("; ".join(errors))
def _check_wire(src_type, src_port, dst_type, dst_port, slot):
    try: src=src_type.out_port(src_port); dst=dst_type.port(dst_port)
    except KeyError as x: return str(x)
    if isinstance(src, DataOut) and isinstance(dst, DataIn) and slot==SLOT_DATA: return None
    if isinstance(src, DataOut) and isinstance(dst, TriggerIn) and slot==SLOT_TRIGGER: return None
    if isinstance(src, SignalOut) and isinstance(dst, SignalIn) and slot==SLOT_SIGNAL: return None
    if isinstance(src, SignalOut) and isinstance(dst, TriggerIn) and slot==SLOT_TRIGGER: return None
    return f"incompatible wire {type(src).__name__} → {type(dst).__name__} at slot {slot!r}"
def resolve_slots(graph, types):
    wires=[]
    for w in graph.wires:
        slot=w.dst_slot
        if slot is None and w.dst_node in graph.nodes and graph.nodes[w.dst_node].type in types:
            try:
                dst=types[graph.nodes[w.dst_node].type].port(w.dst_port)
                slot=SLOT_DATA if isinstance(dst,DataIn) else SLOT_TRIGGER if isinstance(dst,TriggerIn) else SLOT_SIGNAL
            except KeyError: pass
        wires.append(Wire(w.src_node,w.src_port,w.dst_node,w.dst_port,slot))
    graph._wires=wires
def validate(graph, types):
    errors=[]; resolve_slots(graph,types)
    for nid,spec in graph.nodes.items():
        ntype=types.get(spec.type)
        if not ntype: errors.append(f"node {nid!r}: unknown node type {spec.type!r}"); continue
        config=spec.config
        if set(config)-{"groups","ports","init"}: errors.append(f"node {nid!r}: config requires groups/ports/init sections")
        if not isinstance(config.get("groups",{}),dict) or not isinstance(config.get("ports",{}),dict) or not isinstance(config.get("init",{}),dict): errors.append(f"node {nid!r}: config sections must be mappings"); continue
        defaults={g.name:set(g.defaults) for g in ntype.groups}
        for g,vals in config.get("groups",{}).items():
            if g not in defaults or not isinstance(vals,dict) or set(vals)-defaults.get(g,set()): errors.append(f"node {nid!r}: invalid group config {g!r}")
        if set(config.get("ports",{}))-{p.name for p in ntype.data_in}: errors.append(f"node {nid!r}: invalid port config")
        if set(config.get("init",{}))-set(ntype.init_defaults): errors.append(f"node {nid!r}: invalid init config")
    seen=set()
    for w in graph.wires:
        target=(w.dst_node,w.dst_port,w.dst_slot)
        if target in seen: errors.append(f"wire {w!r}: fan-in forbidden on {target}")
        seen.add(target)
        if w.src_node not in graph.nodes or w.dst_node not in graph.nodes: errors.append(f"wire {w!r}: unknown node"); continue
        a=types.get(graph.nodes[w.src_node].type); b=types.get(graph.nodes[w.dst_node].type)
        if a and b:
            err=_check_wire(a,w.src_port,b,w.dst_port,w.dst_slot)
            if err: errors.append(f"wire {w!r}: {err}")
    return ValidationResult(tuple(errors),())
def ensure_valid(graph,types):
    result=validate(graph,types)
    if not result.ok: raise ValidationError(list(result.errors))
    return result
