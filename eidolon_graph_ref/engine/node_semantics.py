"""Final node protocol semantics shared by every compiled node.

This is intentionally *not* ``NodeDefinition``.  Definition classes describe
and compile a contract; this class interprets incoming events against that
contract at runtime.  User nodes cannot override these rules.

The Executor only orchestrates delivery and firing; every interpretation of
the orthogonal Data / Signal / Trigger state matrix lives here.
"""
from __future__ import annotations

from ..model.graph import SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from .event import Event, Kind


class NodeSemantics:
    """The non-overridable Port × Event interpretation matrix."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("NodeSemantics is final; protocol behavior is not an extension point")

    @staticmethod
    def receive(inst, event: Event, node_id: str, port: str, slot: str) -> None:
        """Interpret one delivered event and update only the target port fact.

        Data and Signal remain orthogonal: a data event changes a data cache;
        a signal event changes a level; either kind can activate a trigger.
        Group membership is deliberately absent from this layer.
        """

        if slot == SLOT_DATA:
            if event.kind is not Kind.DATA:
                raise ValueError("only data events may enter a data slot")
            inst.data_states[node_id][port].receive(event)
            return
        if slot == SLOT_SIGNAL:
            if event.kind is not Kind.SIGNAL:
                raise ValueError("only signal events may enter a signal slot")
            inst.signal_states[node_id][port].receive(event)
            return
        if slot == SLOT_TRIGGER:
            inst.trigger_states[node_id][port].receive(event)
            return
        raise ValueError(f"unknown slot {slot!r}")

    @classmethod
    def consume(cls, inst, state, node_id: str, port: str) -> None:
        """Mark a port state's pending events as consumed by this node and port."""

        seq = inst.timeline.next_seq
        for eid in state.pending_events:
            event = inst.timeline.events[eid]
            for delivery in event.deliveries:
                if delivery.node == node_id and delivery.port == port and delivery.consumed_seq is None:
                    delivery.consumed_seq = seq
            event.consumed_by.append((seq, node_id, port))
        state.pending = False
        state.pending_events = []

    @classmethod
    def settle_control_signals(cls, inst, node_id: str) -> None:
        """Bound signals control source selection only.  Their occurrence wakes
        this node, then is consumed as a control-state update; the level persists."""

        nt = inst.types[inst.definition.nodes[node_id].type]
        for port in {p.signal for p in nt.data_in if p.signal}:
            state = inst.signal_states[node_id][port]
            if state.pending:
                cls.consume(inst, state, node_id, port)

    @classmethod
    def handler_arguments(cls, inst, node_id: str, group) -> dict:
        """Resolve the group's handler arguments: effective() per input, plus
        the payload of any data event carried by a fired trigger."""

        data = {p: cls.effective(inst, node_id, p) for p in group.inputs}
        for t in group.triggers:
            state = inst.trigger_states[node_id][t]
            if state.has_payload:
                data[t] = state.payload
        return data

    @classmethod
    def consume_group(cls, inst, node_id: str, group) -> tuple:
        """Consume every input and trigger the fired group was waiting on.

        Inputs may be DataIn states or unbound SignalIn states; triggers also
        drop their payload.  Returns the consumed event ids.
        """

        consumed = []
        for p in group.inputs:
            state = cls._input_state(inst, node_id, p)
            if state.pending:
                consumed.extend(state.pending_events)
                cls.consume(inst, state, node_id, p)
        for t in group.triggers:
            state = inst.trigger_states[node_id][t]
            if state.pending:
                consumed.extend(state.pending_events)
                cls.consume(inst, state, node_id, t)
                state.has_payload = False
                state.payload = None
        return tuple(consumed)

    @staticmethod
    def _input_state(inst, node_id: str, port: str):
        """The runtime state behind a group input: its DataIn state, or the
        state of an unbound SignalIn used as a plain data input."""

        if port in inst.signal_states[node_id]:
            return inst.signal_states[node_id][port]
        return inst.data_states[node_id][port]

    @staticmethod
    def signal_active(inst, node_id: str, data_port: str) -> bool:
        """Return the data-source selector state for a declared DataIn."""

        declaration = inst.types[inst.definition.nodes[node_id].type].port(data_port)
        if declaration.signal is None:
            return True
        if (node_id, declaration.signal, SLOT_SIGNAL) not in inst.in_index:
            return True
        return inst.signal_states[node_id][declaration.signal].level is True

    @classmethod
    def dynamic(cls, inst, node_id: str, data_port: str) -> bool:
        """A data input is dynamic only when a source exists and its gate is HIGH."""

        return inst.data_states[node_id][data_port].event_driven and cls.signal_active(inst, node_id, data_port)

    @classmethod
    def data_ready(cls, inst, node_id: str, port: str) -> bool:
        """Evaluate the DATA leaf without allowing Signal to become readiness."""

        if port in inst.signal_states[node_id]:  # unbound SignalIn used as data input
            return inst.signal_states[node_id][port].pending
        state = inst.data_states[node_id][port]
        return True if not cls.dynamic(inst, node_id, port) else state.pending

    @classmethod
    def effective(cls, inst, node_id: str, port: str):
        """Resolve the handler argument after the static/dynamic source decision."""

        if port in inst.signal_states[node_id]:
            return inst.signal_states[node_id][port].level
        state = inst.data_states[node_id][port]
        declaration = inst.types[inst.definition.nodes[node_id].type].port(port)
        if cls.dynamic(inst, node_id, port) and state.has_value:
            return state.value
        return inst.configs[node_id]["ports"].get(port, declaration.default)

    @classmethod
    def group_ready(cls, inst, node_id: str, group) -> bool:
        if group.readiness is not None:
            return group.readiness.evaluate(
                lambda port: cls.data_ready(inst, node_id, port),
                lambda port: inst.trigger_states[node_id][port].pending,
            )
        data_ready = all(cls.data_ready(inst, node_id, port) for port in group.inputs)
        trigger_ready = any(inst.trigger_states[node_id][port].pending for port in group.triggers) if group.triggers else True
        if not trigger_ready:
            return False
        if not group.triggers:
            # 裁定 16:无触发器组要求新事实——至少一个输入 pending 才触发。
            # 全静态回退值不构成触发,防止"永远 ready"契约借壳复活(裁定 9)。
            if not any(cls._input_state(inst, node_id, p).pending for p in group.inputs):
                return False
        return data_ready
