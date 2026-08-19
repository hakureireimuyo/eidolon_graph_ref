"""事件模型：身份、生命周期、谁生产谁消费、档案保留。

用户裁定（2026-08-19）：事件有身份、有生命周期（produced → delivered → consumed）、
记录谁生产谁消费；被消费后的事件暂时保留在档案中，是传播分析/追踪的底层基础。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.engine.timeline import KIND_FIRE
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_TRIGGER

from conftest import fired, make_world, node_state


def test_event_identity_and_producer():
    """事件有独立身份；记录生产者与产出端口；宿主注入 producer=None。"""
    g = GraphDefinition("id")
    g.add_node("src", "Source")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "x")])
    evs = list(world.timeline.events.values())
    assert [e.id for e in evs] == [1, 2]
    injected, produced = evs
    assert injected.producer is None  # 宿主注入
    assert injected.port == "in"
    assert produced.producer == "src"  # 节点产出
    assert produced.port == "out"


def test_event_lifecycle_produced_delivered_consumed():
    """生命周期：produced → delivered(pending) → consumed；消费后保留在档案中。"""
    g = GraphDefinition("life")
    g.add_node("src", "Source")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    world = make_world(g)
    world.run()
    produced = world.timeline.events[1]
    assert produced.status == "consumed"  # 全部投递已被消费
    assert len(produced.deliveries) == 1
    d = produced.deliveries[0]
    assert (d.node, d.port, d.slot) == ("sink", "in", "data")
    assert d.consumed_seq is not None
    # 消费记录：谁在哪个 seq 消费
    fire_seq = next(e.seq for e in world.timeline.entries if e.kind == KIND_FIRE and e.dst_node == "sink")
    assert produced.consumed_by == [(fire_seq, "sink", "in")]
    assert d.consumed_seq == fire_seq


def test_fanout_event_multiple_deliveries_and_consumers():
    """扇出 = 一个事件多次投递，每次投递独立更新端口状态、独立消费记录。"""
    g = GraphDefinition("fan")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")
    g.add_node("s2", "Sink")
    g.wire("split", "out1", "s1", "in")
    g.wire("split", "out2", "s2", "in")
    world = make_world(g)
    world.run([Injection("split", "in", SLOT_DATA, Kind.DATA, 7)])
    ev_out1 = next(e for e in world.timeline.events.values() if e.port == "out1")
    assert len(ev_out1.deliveries) == 1  # out1 只连了 s1
    assert ev_out1.deliveries[0].node == "s1"
    assert ev_out1.status == "consumed"
    assert {c[1] for c in ev_out1.consumed_by} == {"s1"}


def test_orphan_event_when_output_unwired():
    """输出端口未连线：事件照常产生、进入档案，status=orphan（无投递）。"""
    g = GraphDefinition("orphan")
    g.add_node("src", "Source")  # out 未连线
    world = make_world(g)
    world.run()
    ev = world.timeline.events[1]
    assert ev.status == "orphan"
    assert ev.deliveries == []
    assert ev.consumed_by == []


def test_multiple_unconsumed_events_consumed_together():
    """堆积的多个事件在同一次执行中一并消费（pending 是布尔，事件身份逐一保留）。"""
    g = GraphDefinition("multi")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run(
        [
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 1),
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 2),
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 3),
        ]
    )
    fire_seq = next(e.seq for e in world.timeline.entries if e.kind == KIND_FIRE)
    assert all(e.status == "consumed" for e in world.timeline.events.values())
    assert all(e.consumed_by[0][0] == fire_seq for e in world.timeline.events.values())


def test_signal_event_dual_role_consumption():
    """信号的两重语义分别消费：同一信号事件扇出到资格槽 + 触发端口，
    一次执行消费两处投递（level 状态与 occurrence 激活各自成对）。"""
    from eidolon_graph_ref.model.graph import SLOT_QUAL, SLOT_TRIGGER

    g = GraphDefinition("dual")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("stod", "SignalToData", x="FACT")
    g.add_node("sink", "Sink")
    g.wire("gate", "level", "stod", "x", slot=SLOT_QUAL)
    g.wire("gate", "level", "stod", "pass", slot=SLOT_TRIGGER)
    g.wire("stod", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 1)])
    signal_ev = next(e for e in world.timeline.events.values() if e.producer == "gate")
    assert len(signal_ev.deliveries) == 2  # 一次产出，两处投递
    assert {d.port for d in signal_ev.deliveries} == {"x", "pass"}
    assert signal_ev.status == "consumed"
    # 同一次 fire 消费：资格槽(x)与触发端口(pass)各有一条消费记录
    assert {(c[1], c[2]) for c in signal_ev.consumed_by} == {("stod", "x"), ("stod", "pass")}
    assert node_state(world, "sink")["last"] == "FACT"


def test_archive_retains_consumed_events_across_epochs():
    """被消费后的事件暂时保留：跨 epoch 档案完整，供传播分析与追踪。"""
    g = GraphDefinition("retain")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, 2)])
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, 3)])
    assert len(world.timeline.events) == 3
    assert [e.run for e in world.timeline.events.values()] == [1, 2, 3]
    assert all(e.status == "consumed" for e in world.timeline.events.values())


def test_fanout_shares_payload_reference():
    """扇出共享载荷引用（内核零复制）：所有下游收到同一个 Python 对象。

    这是「节点不得原地修改输入」约定的动机与锚点：任何分支的原地修改
    会被其他分支看到，形成隐藏通道、破坏确定性（约定见 README）。
    本测试锁定"共享引用"这一内核事实，防实现漂移（如无意中改为复制）。
    """
    g = GraphDefinition("share")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")
    g.add_node("s2", "Sink")
    g.wire("split", "out1", "s1", "in")
    g.wire("split", "out2", "s2", "in")
    world = make_world(g)
    payload = ["mutable"]
    world.run([Injection("split", "in", SLOT_DATA, Kind.DATA, payload)])
    out1 = next(e for e in world.timeline.events.values() if e.port == "out1")
    out2 = next(e for e in world.timeline.events.values() if e.port == "out2")
    # 同一载荷对象贯穿：注入 → split 两输出 → 两个 Sink 的 state.last。
    # 注意 observable_state() 的观察视图是深拷贝（保护单写者不变量），
    # 共享引用断言必须取内部世界事实 node_states。
    assert out1.payload is payload
    assert out2.payload is payload
    assert world.node_states["s1"]["last"] is payload
    assert world.node_states["s2"]["last"] is payload
