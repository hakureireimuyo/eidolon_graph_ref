"""Final node protocol semantics shared by every compiled node.

This is intentionally *not* ``NodeDefinition``.  Definition classes describe
and compile a contract; this class interprets incoming events against that
contract at runtime.  User nodes cannot override these rules.

The Executor only orchestrates delivery and firing; every interpretation of
the orthogonal Data / Signal / Trigger state matrix lives here.
"""
from __future__ import annotations

import os

from ..model.graph import SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from ..model.ports import APPEND
from .event import Event, Kind
from .timeline import Entry, KIND_CONSUME, KIND_READINESS_FAILED

# 调试模式(REFACTOR_READINESS_VALIDATION):时间线记录 readiness 失败。
# 默认关闭——失败评估是正常图运转的一部分(唤醒≠ready),无差别记录会
# 淹没确定性时间线;EIDOLON_DEBUG=1 开启,供可视化调试与 console 追踪。
RECORD_READINESS_FAILURES = os.environ.get("EIDOLON_DEBUG") == "1"


class NodeSemantics:
    """The non-overridable Port × Event interpretation matrix."""

    def __init_subclass__(cls, **kwargs):
        raise TypeError("NodeSemantics is final; protocol behavior is not an extension point")

    @staticmethod
    def receive(inst, event: Event, node_id: str, port: str, slot: str, delivery) -> None:
        """Interpret one delivered event and update only the target port fact.

        Data and Signal remain orthogonal: a data event changes a data cache;
        a signal event changes a level; either kind can activate a trigger.
        Group membership is deliberately absent from this layer.
        ``delivery`` is the pending record for this exact (node, port, slot)
        receive — the port state links it directly, so consumption needs no
        reverse lookup.
        """

        if slot == SLOT_DATA:
            if event.kind is not Kind.DATA:
                raise ValueError("only data events may enter a data slot")
            inst.data_states[node_id][port].receive(event, delivery)
            return
        if slot == SLOT_SIGNAL:
            if event.kind is not Kind.SIGNAL:
                raise ValueError("only signal events may enter a signal slot")
            inst.signal_states[node_id][port].receive(event, delivery)
            return
        if slot == SLOT_TRIGGER:
            inst.trigger_states[node_id][port].receive(event, delivery)
            return
        raise ValueError(f"unknown slot {slot!r}")

    @classmethod
    def consume(cls, inst, state, node_id: str, port: str) -> tuple[int, ...]:
        """Mark a port state's pending deliveries as consumed.  O(k), k = 待消费投递数。

        端口直接持有本端口的 Delivery 引用，无需比对 node/port、无需扫描
        事件的其他投递（REFACTOR_EVENT_INDEXING）。返回消费的事件 id 序列。

        APPEND 缓存随消费排空:累积语义 = 「自上次消费以来的增量批次」,
        handler 每次收到的即本次新增;跨消费累积由节点 state 负责。
        """

        seq = inst.timeline.next_seq
        for delivery in state.pending_deliveries:
            delivery.consumed_seq = seq
            inst.timeline.events[delivery.event_id].consumed_by.append((seq, node_id, port))
        ids = tuple(d.event_id for d in state.pending_deliveries)
        state.facts.pending = False
        state.pending_deliveries = []
        if state.cache_strategy == APPEND:
            state.facts.value = []
        return ids

    @classmethod
    def settle_control_signals(cls, inst, node_id: str) -> None:
        """Bound signals control source selection only.  Their occurrence wakes
        this node, then is consumed as a control-state update; the level persists.
        The consumption is recorded as a KIND_CONSUME timeline entry (no fire)."""

        nt = inst.types[inst.definition.nodes[node_id].type]
        # 按声明序遍历绑定信号(set 迭代跨进程不稳定,PYTHONHASHSEED 随机化会破坏
        # KIND_CONSUME 的 seq 分配确定性);1:1 绑定不变式下无重复,dict.fromkeys 防御保序去重。
        for port in dict.fromkeys(p.signal for p in nt.data_in if p.signal):
            state = inst.signal_states[node_id][port]
            if state.facts.pending:
                ids = cls.consume(inst, state, node_id, port)
                inst.timeline.record(
                    Entry(run=inst.run_no, kind=KIND_CONSUME, dst_node=node_id, dst_port=port, consumed=ids, message="control signal settled")
                )

    @classmethod
    def handler_arguments(cls, inst, node_id: str, group) -> dict:
        """Resolve the group's handler arguments: effective() per input, plus
        the payload of any data event carried by a fired trigger."""

        data = {p: cls.effective(inst, node_id, p) for p in group.inputs}
        for t in group.triggers:
            state = inst.trigger_states[node_id][t]
            if state.has_payload:
                data[t] = state.facts.value
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
            if state.facts.pending:
                consumed.extend(cls.consume(inst, state, node_id, p))
        for t in group.triggers:
            state = inst.trigger_states[node_id][t]
            if state.facts.pending:
                consumed.extend(cls.consume(inst, state, node_id, t))
                state.has_payload = False
                state.facts.value = None
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
        return inst.signal_states[node_id][declaration.signal].facts.level is True

    @classmethod
    def dynamic(cls, inst, node_id: str, data_port: str) -> bool:
        """A data input is dynamic only when a source exists and its gate is HIGH."""

        return inst.data_states[node_id][data_port].event_driven and cls.signal_active(inst, node_id, data_port)

    @classmethod
    def data_ready(cls, inst, node_id: str, port: str) -> bool:
        """Evaluate the DATA leaf without allowing Signal to become readiness."""

        if port in inst.signal_states[node_id]:  # unbound SignalIn used as data input
            return inst.signal_states[node_id][port].facts.pending
        state = inst.data_states[node_id][port]
        return True if not cls.dynamic(inst, node_id, port) else state.facts.pending

    @classmethod
    def effective(cls, inst, node_id: str, port: str):
        """Resolve the handler argument after the static/dynamic source decision."""

        if port in inst.signal_states[node_id]:
            return inst.signal_states[node_id][port].facts.level
        state = inst.data_states[node_id][port]
        declaration = inst.types[inst.definition.nodes[node_id].type].port(port)
        if cls.dynamic(inst, node_id, port) and state.has_value:
            return state.facts.value
        return inst.configs[node_id]["ports"].get(port, declaration.default)

    @classmethod
    def group_ready(cls, inst, node_id: str, group) -> bool:
        if group.readiness is not None:
            data = lambda port: cls.data_ready(inst, node_id, port)
            trigger = lambda port: inst.trigger_states[node_id][port].facts.pending
            result = group.readiness.evaluate(data, trigger)
            if not result and RECORD_READINESS_FAILURES:
                # 调试模式:失败即记录 explain() 全文,供 console 追踪"为什么不火"
                inst.timeline.record(
                    Entry(
                        run=inst.run_no,
                        kind=KIND_READINESS_FAILED,
                        dst_node=node_id,
                        group=group.name,
                        message=group.readiness.explain(data, trigger),
                    )
                )
            return result
        data_ready = all(cls.data_ready(inst, node_id, port) for port in group.inputs)
        trigger_ready = any(inst.trigger_states[node_id][port].facts.pending for port in group.triggers) if group.triggers else True
        if not trigger_ready:
            return False
        if not group.triggers:
            # 裁定 16:无触发器组要求新事实——至少一个输入 pending 才触发。
            # 全静态回退值不构成触发,防止"永远 ready"契约借壳复活(裁定 9)。
            if not any(cls._input_state(inst, node_id, p).facts.pending for p in group.inputs):
                return False
        return data_ready
