"""执行模型：epoch 语义、传播顺序、预算、门控、异常。

依据：graph-execution-model.md §4-5
- 注入序 + 数据流因果序；队列排空即静止
- 每组每 epoch 至多执行一次（NodeTurn 预算）；反馈环跨轮迭代，不递归展开
- 投递深度优先；门控电平变化插队即时结算
- 同节点前组产出回连后序组当轮可见（顺序即语义）
- 节点异常：不产出任何输出 + 错误事件进日志
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER
from eidolon_graph_ref.primitives import PRIMITIVES

from conftest import deliveries, errors, fired, make_world, node_state, quiesces


def test_epoch_injections_before_seeding():
    """注入目标按注入序、源节点按声明序；同一 epoch 内注入传播先于源播种。"""
    g = GraphDefinition("order")
    g.add_node("src", "Source", step=0)
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "injected")])
    # sink 只执行一次（注入先触发），源播种的 0 到达时 sink 已无本组预算
    assert node_state(world, "sink")["last"] == "injected"
    assert quiesces(world) == 1


def test_group_fires_at_most_once_per_epoch():
    """每组每轮至多一次：多事件堆积只产生一次执行。"""
    g = GraphDefinition("budget")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run(
        [
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 1),
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 2),
            Injection("sink", "in", SLOT_DATA, Kind.DATA, 3),
        ]
    )
    assert len([f for f in fired(world, 1) if f[0] == "sink"]) == 1
    assert node_state(world, "sink")["last"] == 3  # 执行一次，读到最新缓存


def test_feedback_loop_iterates_across_epochs():
    """反馈环 = 跨运行迭代的状态机闭环：一个 epoch 内不递归展开、不挂起。"""
    g = GraphDefinition("loop")
    g.add_node("a", "Sink")  # a: 吸收
    g.add_node("loop", "Split")  # loop: in → out1/out2
    g.wire("loop", "out1", "loop", "in")  # 回连自身
    g.wire("loop", "out2", "a", "in")
    world = make_world(g)
    world.run([Injection("loop", "in", SLOT_DATA, Kind.DATA, 1)])
    # loop 组每 epoch 至多一次：回连事件唤醒 loop，但预算已消耗 → 本 epoch 不再执行
    assert len([f for f in fired(world, 1) if f[0] == "loop"]) == 1
    # 回连数据 pending 保留，下一 epoch 新注入后再次迭代
    world.run([Injection("loop", "in", SLOT_DATA, Kind.DATA, 2)])
    assert len([f for f in fired(world, 2) if f[0] == "loop"]) == 1
    assert node_state(world, "a")["last"] == 2


def test_fanout_one_event_many_deliveries():
    """扇出：一个输出事件投递到多个下游（一次产出，多次投递，各自独立消费）。"""
    g = GraphDefinition("fanout")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")
    g.add_node("s2", "Sink")
    g.wire("split", "out1", "s1", "in")
    g.wire("split", "out2", "s2", "in")
    world = make_world(g)
    world.run([Injection("split", "in", SLOT_DATA, Kind.DATA, 7)])
    assert node_state(world, "s1")["last"] == 7
    assert node_state(world, "s2")["last"] == 7
    assert len(world.timeline.events) == 3  # 注入 + out1 + out2


def test_enable_gating_low_blocks_execution_but_keeps_data():
    """门控 inactive：整节点不执行 → 无任何输出事件；数据照常接收缓存（节点暂停、数据不丢）。"""
    g = GraphDefinition("gate")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("sink", "Sink")
    g.wire("gate", "level", "sink", "enable")
    world = make_world(g)
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 0)])  # level LOW
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "held")])  # LOW 期间数据到达
    assert node_state(world, "sink")["last"] is None  # 不执行
    from conftest import data_port

    assert data_port(world, "sink", "in")["pending"] is True  # 数据照常缓存
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 1)])  # level HIGH → 恢复资格
    assert node_state(world, "sink")["last"] == "held"  # 资格恢复后照常参与执行


def test_sources_seed_every_epoch_in_declaration_order():
    """源节点每 epoch 播种执行一次（group=step），按声明序。"""
    g = GraphDefinition("seeds")
    g.add_node("src", "Source")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    world = make_world(g)
    world.run()
    world.run()
    world.run()
    assert node_state(world, "sink")["last"] == 2
    assert len([f for f in fired(world) if f == ("src", "step")]) == 3


def test_same_node_later_group_sees_earlier_group_same_epoch():
    """前组产出经外部连线回连时，同节点后序组当轮可见（Buffer put → flush 同轮）。"""
    g = GraphDefinition("same-node")
    g.add_node("buf", "Buffer")
    g.add_node("sink", "Sink")
    g.wire("buf", "out", "sink", "in")
    world = make_world(g)
    world.run(
        [
            Injection("buf", "put", SLOT_DATA, Kind.DATA, 1),
            Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True),
        ]
    )
    # 同轮 put 先执行（组声明序），flush 取到本轮数据
    assert deliveries(world, "buf", "out") == [[1]]
    assert node_state(world, "sink")["last"] == [1]


def test_exception_no_output_error_entry_pending_preserved():
    """节点异常：不产出任何输出 + 错误事件进日志；pending 保留，下次唤醒重试。"""
    from eidolon_graph_ref.engine.protocol import TickOutput
    from eidolon_graph_ref.model.node_type import InputGroup, NodeType, Policy
    from eidolon_graph_ref.model.ports import DataIn, DataOut

    def boom(ctx):
        raise RuntimeError("boom")

    bad = NodeType(
        name="Boom",
        data_in=(DataIn("in"),),
        data_out=(DataOut("out"),),
        groups=(InputGroup("go", inputs=("in",), policy=Policy.ON_ANY_DATA),),
        tick=boom,
    )
    g = GraphDefinition("boom")
    g.add_node("bad", "Boom")
    g.add_node("sink", "Sink")
    g.wire("bad", "out", "sink", "in")
    world = make_world(g, {**PRIMITIVES, "Boom": bad})
    # 同一 epoch 注入两个事件：异常后 pending 保留，但本组预算已消耗 → 只尝试一次
    world.run(
        [
            Injection("bad", "in", SLOT_DATA, Kind.DATA, 1),
            Injection("bad", "in", SLOT_DATA, Kind.DATA, 2),
        ]
    )
    assert errors(world) == ["RuntimeError: boom"]  # 只记录一次异常（每组每轮至多一次）
    assert deliveries(world, "bad", "out") == []  # 不产出任何输出
    from conftest import data_port

    assert data_port(world, "bad", "in")["pending"] is True  # pending 保留，等待下次唤醒重试


def test_qual_low_signal_node_level_change_immediate():
    """门控电平变化插队即时结算：同一 epoch 内 LOW→HIGH 后数据立即放行。"""
    g = GraphDefinition("immediate")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("sink", "Sink")
    g.wire("gate", "level", "sink", "enable")
    world = make_world(g)
    world.run(
        [
            Injection("sink", "in", SLOT_DATA, Kind.DATA, "x"),
            Injection("gate", "data", SLOT_DATA, Kind.DATA, 1),  # HIGH 与数据同轮到达
        ]
    )
    assert node_state(world, "sink")["last"] == "x"  # 电平变化即时结算，无需下一轮
