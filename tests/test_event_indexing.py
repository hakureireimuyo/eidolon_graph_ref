"""事件索引优化(REFACTOR_EVENT_INDEXING):pending_deliveries 直接引用。

端口状态持有本端口 Delivery 的直接引用（而非事件 id），消费路径 O(k)
无需反向查表、无需扫描事件的其他投递。本文件锁定两个不变式：
- 链接不变式：每次 receive 恰好链接一条 Delivery 到目标端口状态
- 独立性不变式：一个事件的多次投递各自独立消费，互不影响
"""

from eidolon_graph_ref.engine.event import Delivery, Event, Injection, Kind
from eidolon_graph_ref.engine.node_semantics import NodeSemantics
from eidolon_graph_ref.engine.port_state import PortInvariants, PortState
from eidolon_graph_ref.engine.timeline import Timeline
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.model.ports import APPEND, REPLACE

from conftest import fired, make_world


class _MiniInst:
    """consume() 仅依赖 inst.timeline 的最小实例。"""

    def __init__(self, events=()):
        self.timeline = Timeline()
        for event in events:
            self.timeline.archive(event)


def _data_event(eid=1, payload=42) -> Event:
    return Event(eid, 1, Kind.DATA, payload, "src", "tick")


def _delivery(eid=1, node="sink", port="in.value", slot=SLOT_DATA) -> Delivery:
    return Delivery(eid, node, port, slot, seq=1)


def _state(port_type="data", wired=False, cache_strategy=REPLACE) -> PortState:
    return PortState(
        PortInvariants(
            port_type=port_type,
            is_wired=wired,
            cache_strategy=cache_strategy if port_type == "data" else None,
        )
    )


def test_delivery_linking_consume_clears():
    """链接不变式:receive 把 Delivery 挂到端口状态;consume 标记消费并清空。"""
    event = _data_event()
    delivery = _delivery()
    event.deliveries.append(delivery)
    state = _state()
    state.receive(event, delivery)

    assert state.pending_deliveries == [delivery]  # 直接引用,同对象
    assert state.facts.pending is True

    inst = _MiniInst([event])
    seq = inst.timeline.next_seq
    ids = NodeSemantics.consume(inst, state, "sink", "in.value")

    assert delivery.consumed_seq == seq  # 消费时的 next_seq
    assert ids == (1,)
    assert state.facts.pending is False
    assert state.pending_deliveries == []  # 消费即清空


def test_fanout_deliveries_independent():
    """独立性:同一事件的两处投递各自挂各自端口;消费一处不动另一处。"""
    event = _data_event()
    d_a = _delivery(node="a", port="in.value")
    d_b = _delivery(node="b", port="in.value")
    event.deliveries.extend([d_a, d_b])
    state_a, state_b = _state(), _state()
    state_a.receive(event, d_a)
    state_b.receive(event, d_b)

    inst = _MiniInst([event])
    seq = inst.timeline.next_seq
    NodeSemantics.consume(inst, state_a, "a", "in.value")

    assert d_a.consumed_seq == seq
    assert d_b.consumed_seq is None  # 未消费侧保留
    assert event.status == "pending"  # 部分消费 → 事件整体仍 pending
    assert event.consumed_by == [(seq, "a", "in.value")]


def test_consume_returns_ids_in_receive_order():
    """consume 返回消费事件 id 序列(receive 序)——fire.consumed 的契约来源。"""
    events = [Event(i, 1, Kind.DATA, i, "host", None) for i in (1, 2, 3)]
    state = _state(cache_strategy=APPEND)
    for event in events:
        delivery = _delivery(event.id)
        event.deliveries.append(delivery)
        state.receive(event, delivery)

    inst = _MiniInst(events)
    seq = inst.timeline.next_seq
    ids = NodeSemantics.consume(inst, state, "sink", "in.value")

    assert ids == (1, 2, 3)
    assert state.facts.value == []  # APPEND 消费即排空
    assert all(d.consumed_seq == seq for e in events for d in e.deliveries)
    assert all(e.consumed_by == [(seq, "sink", "in.value")] for e in events)


def test_signal_and_trigger_states_link_deliveries():
    """Signal/Trigger 端口状态同样持有 pending_deliveries 直接引用。"""
    signal_ev = Event(1, 1, Kind.SIGNAL, True, "src", "g")
    trigger_ev = Event(2, 1, Kind.DATA, 7, "host", None)

    sig = _state("signal")
    sig_d = _delivery(1, "sink", "g", SLOT_SIGNAL)
    signal_ev.deliveries.append(sig_d)
    sig.receive(signal_ev, sig_d)
    assert sig.pending_deliveries[0].slot == SLOT_SIGNAL

    trig = _state("trigger")
    trig_d = _delivery(2, "sink", "go", SLOT_TRIGGER)
    trigger_ev.deliveries.append(trig_d)
    trig.receive(trigger_ev, trig_d)
    assert trig.pending_deliveries[0].slot == SLOT_TRIGGER
    assert trig.facts.value == 7 and trig.has_payload is True

    inst = _MiniInst([signal_ev, trigger_ev])
    NodeSemantics.consume(inst, sig, "sink", "g")
    NodeSemantics.consume(inst, trig, "sink", "go")
    assert signal_ev.status == "consumed" and trigger_ev.status == "consumed"


def test_high_fanout_1000_targets():
    """高扇出功能验收:1 源 → 1000 目标,同一事件 1000 处投递全部独立消费。"""
    g = GraphDefinition("fan1000")
    g.add_node("src", "Source")
    for i in range(1000):
        g.add_node(f"s{i}", "Sink")
        g.wire("src", "tick", f"s{i}", "consume.value")
    world = make_world(g)
    world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])

    produced = next(e for e in world.timeline.events.values() if e.producer == "src")
    assert len(produced.deliveries) == 1000
    assert produced.status == "consumed"
    assert len(produced.consumed_by) == 1000  # 每处投递一条消费记录
    assert len([f for f in fired(world) if f[0] != "src"]) == 1000
    view = world.observable_state()  # 单次快照,避免 O(n²) 断言
    for i in range(1000):
        assert view[f"s{i}"]["state"]["last"] == 0  # Source 首轮返回 0
