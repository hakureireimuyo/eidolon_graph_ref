"""内核不变量（graph-port-capability-composition.md §6.2）逐条验证。

审查方法：每个场景记录内核不变量是否成立，而不是"程序是否符合预期"。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_TRIGGER
from eidolon_primitives import PRIMITIVES

from conftest import (
    build_pairing_graph,
    data_port,
    deliveries,
    enable_port,
    errors,
    fired,
    make_world,
    node_state,
    pairing_types,
)

TYPES = pairing_types()


def _pairing_world():
    return make_world(build_pairing_graph(), TYPES)


def _d(world, value):
    world.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, value)])


def _s(world, level):
    world.run([Injection("in_s", "data", SLOT_DATA, Kind.DATA, level)])


def test_invariant_2_port_state_without_pending_event():
    """「Port State 可以存在而没有 Pending Event」：执行消费 pending 后 value/level 保持。"""
    world = _pairing_world()
    _d(world, "D")
    _s(world, True)
    assert data_port(world, "p", "data")["pending"] is False  # pending 已消费
    assert data_port(world, "p", "data")["value"] == "D"  # 缓存值保持
    assert data_port(world, "p", "data")["qual"]["level"] is True  # 电平保持
    assert data_port(world, "p", "data")["qual"]["pending"] is False


def test_invariant_3_dynamic_port_without_event_has_no_qualification():
    """「Dynamic Port 在没有收到 Event 时不能凭空获得新的动态输入资格」：
    已连接资格槽 = 必须等实际 Signal Event（隐式条件消失，无默认事件）。"""
    world = _pairing_world()
    # 数据端口动态（已连接），资格槽已连接但从未收到事件
    _d(world, "D")
    assert deliveries(world, "p", "out") == []  # level 为 ? → 无资格 → 不执行
    qual = data_port(world, "p", "data")["qual"]
    assert qual["level"] is None and qual["pending"] is False


def test_invariant_4_signal_state_and_occurrence_independent():
    """「Signal State 与 Signal Event Occurrence 必须独立」：level 是状态、pending 是资格。
    S1→S1 同电平重复 = 两次独立资格（每次 Signal Event 都是新的激活请求）。"""
    world = _pairing_world()
    _s(world, True)
    _d(world, "a")
    _s(world, True)
    _d(world, "b")
    assert deliveries(world, "p", "out") == ["a", "b"]  # 第二次 S1 重新授权，非电平变化判断


def test_invariant_5_data_event_survives_signal_low():
    """「Data Event 不因为 Signal LOW 而消失」：LOW 只关资格，数据照常接收缓存。

    值保留（cached 不丢）；pending 即刻消费——LOW 不产生有效组合（§4 序列推导）。
    """
    world = _pairing_world()
    _s(world, False)  # 资格 LOW
    _d(world, "D")
    assert data_port(world, "p", "data")["value"] == "D"  # 数据照常缓存，不消失
    assert data_port(world, "p", "data")["pending"] is False  # LOW 不产生有效组合


def test_invariant_6_no_output_is_no_fact():
    """「Signal 不负责报告 Node 是否产生 Data Output」+「没有 Event 不会产生隐式 Event」：
    无输出 = 没有事实发生，不产生"无事件事件"；配对未完成时不执行、不产出任何事件。"""
    world = _pairing_world()
    _d(world, "D")  # 只有数据、无信号资格 → Readiness 不满足
    assert ("p", "sync") not in fired(world)  # 无 Turn
    assert deliveries(world, "p", "out") == []  # 无任何产出
    # 事件只有注入 + 上游入口的产出(Split 两输出，out2 未连线 = orphan)，
    # 没有隐式事件、没有 p 的产出
    assert len(world.timeline.events) == 3
    assert all(e.producer != "p" for e in world.timeline.events.values())


def test_invariant_8_no_implicit_events():
    """「没有 Event 不会产生隐式 Event」：事件总数 = 注入 + 节点产出，静止不产生事件。"""
    from eidolon_graph_ref.engine.timeline import KIND_DELIVER

    g = GraphDefinition("inv8")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, 1)])
    # 事件 = 注入 1 个 + sink 无产出 = 1 个；时间线只有 1 次投递 + fire + quiesce
    assert len(world.timeline.events) == 1
    assert sum(1 for e in world.timeline.entries if e.kind == KIND_DELIVER) == 1
    assert errors(world) == []


def test_data_node_cannot_write_signal():
    """数据节点永远不写信号（声明违规 → error 条目，不产出信号事件）。"""
    from eidolon_graph_ref.engine.protocol import TickOutput
    from eidolon_graph_ref.model.node_type import InputGroup, NodeType, Policy
    from eidolon_graph_ref.model.ports import DataIn, DataOut

    def tick(ctx):
        return TickOutput(signal_out={"level": True})  # 违规

    bad = NodeType(
        name="BadDataNode",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=tick,
    )
    g = GraphDefinition("bad")
    g.add_node("bad", "BadDataNode")
    world = make_world(g, {**PRIMITIVES, "BadDataNode": bad})
    world.run([Injection("bad", "in", SLOT_DATA, Kind.DATA, 1)])
    assert errors(world) == ["数据节点永远不写信号"]
    assert len(world.timeline.events) == 1  # 只有注入事件，无信号事件


def test_enable_pending_consumed_level_persists():
    """enable 是持续电平门控：pending 消费后 level 保持，HIGH 期间持续执行。"""
    g = GraphDefinition("gate")
    g.add_node("src", "Constant", value=1)
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    g.wire("gate", "level", "sink", "enable")
    world = make_world(g)
    world.run()  # src 产出 → sink.enable 未连接事件(level None) → 不执行? enable 未接事件 = 不启用
    assert node_state(world, "sink")["last"] is None
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 1)])  # gate → level HIGH → enable
    world.run()  # src 产出 → sink 执行（enable HIGH 持续生效）
    assert node_state(world, "sink")["last"] == 1
    assert enable_port(world, "sink")["level"] is True  # level 保持
