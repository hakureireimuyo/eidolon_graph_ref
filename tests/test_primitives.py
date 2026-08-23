"""每个验证原语的 Given/When/Then 行为锁定 + 组合链(DSL v2 迁移后端口名)。

依据:docs/graph-node-definition-dsl.md + graph-node-protocol.md §3.0 事件解释矩阵。
"""
from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_SIGNAL, SLOT_TRIGGER

from conftest import deliveries, fired, make_world, node_state


def test_source_counts_and_emits_each_tick():
    """Given Source(step=2) → Sink. When 三次 tick. Then sink 依次收到 0, 2, 4。"""
    g = GraphDefinition("src")
    g.add_node("src", "Source", config={"groups": {"tick": {"step": 2}}})
    g.add_node("sink", "Sink")
    g.wire("src", "tick", "sink", "consume.value")
    world = make_world(g)
    for _ in range(3):
        world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert node_state(world, "src")["count"] == 6
    assert node_state(world, "sink")["last"] == 4
    assert len([f for f in fired(world) if f == ("src", "tick")]) == 3


def test_constant_emits_configured_value():
    """Given Constant(value=42). When tick. Then 产出 42,无状态。"""
    g = GraphDefinition("const")
    g.add_node("c", "Constant", config={"groups": {"tick": {"value": 42}}})
    g.add_node("sink", "Sink")
    g.wire("c", "tick", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("c", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    world.run([Injection("c", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "c", "tick") == [42, 42]
    assert node_state(world, "c") == {}


def test_sink_absorbs_without_output():
    """Given Sink. When 注入数据. Then last 更新、无任何产出事件。"""
    g = GraphDefinition("sink")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "consume.value", SLOT_DATA, Kind.DATA, "a")])
    world.run([Injection("sink", "consume.value", SLOT_DATA, Kind.DATA, "b")])
    assert node_state(world, "sink")["last"] == "b"
    assert deliveries(world, "sink", "consume") == []
    assert len(world.timeline.events) == 2  # 只有注入的两个事件


def test_probe_accumulates_log():
    """Given Probe. When 三个值到达. Then log 累积,无输出(显式状态可观察点)。"""
    g = GraphDefinition("probe")
    g.add_node("probe", "Probe")
    world = make_world(g)
    for v in (1, 2, 3):
        world.run([Injection("probe", "observe.value", SLOT_DATA, Kind.DATA, v)])
    assert node_state(world, "probe")["log"] == [1, 2, 3]


def test_buffer_put_accumulates_flush_releases():
    """Buffer 能否表达"数据暂存但不产生执行事件":put 只累积不产出;flush 触发才取出全部。"""
    g = GraphDefinition("buf")
    g.add_node("buf", "Buffer")
    g.add_node("sink", "Sink")
    g.wire("buf", "flush", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("buf", "put.item", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("buf", "put.item", SLOT_DATA, Kind.DATA, 2)])
    assert deliveries(world, "buf", "flush") == []  # 数据暂存但不产生执行事件
    world.run([Injection("buf", "flush.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "flush") == [[1, 2]]
    # 第二轮:flush 清空后继续 put,旧项不得复活(APPEND 缓存随消费排空)
    world.run([Injection("buf", "put.item", SLOT_DATA, Kind.DATA, 3)])
    world.run([Injection("buf", "flush.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "flush") == [[1, 2], [3]]
    assert node_state(world, "buf")["items"] == []
    # 空缓冲 flush:无事实发生,不产出
    world.run([Injection("buf", "flush.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "flush") == [[1, 2], [3]]


def test_join_syncs_multiple_inputs():
    """Join 明确表达多个输入之间的同步关系:a、b 齐备才执行,产出 tuple。"""
    g = GraphDefinition("join")
    g.add_node("in_a", "Split")
    g.add_node("in_b", "Split")
    g.add_node("join", "Join")
    g.add_node("sink", "Sink")
    g.wire("in_a", "fan.out1", "join", "join.a")
    g.wire("in_b", "fan.out1", "join", "join.b")
    g.wire("join", "join", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("in_a", "fan.value", SLOT_DATA, Kind.DATA, 1)])
    assert ("join", "join") not in fired(world)
    world.run([Injection("in_b", "fan.value", SLOT_DATA, Kind.DATA, 2)])
    assert ("join", "join") in fired(world)
    assert node_state(world, "sink")["last"] == (1, 2)


def test_split_emits_two_independent_events():
    """Split 验证多输出发射:一次执行产出两个独立事件。"""
    g = GraphDefinition("split")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")
    g.add_node("s2", "Sink")
    g.wire("split", "fan.out1", "s1", "consume.value")
    g.wire("split", "fan.out2", "s2", "consume.value")
    world = make_world(g)
    world.run([Injection("split", "fan.value", SLOT_DATA, Kind.DATA, "x")])
    assert node_state(world, "s1")["last"] == "x"
    assert node_state(world, "s2")["last"] == "x"
    assert len(world.timeline.events) == 3


def test_data_to_signal_modes():
    """DataToSignal:数据 → 信号显式转换(控制流构造),各模式算电平;Latch 受控放行。"""
    g = GraphDefinition("dts")
    g.add_node("dts", "DataToSignal", config={"groups": {"convert": {"mode": "gt", "threshold": 5}}})
    g.add_node("latch", "Latch")
    g.add_node("sink", "Sink")
    g.wire("dts", "convert", "latch", "release.gate", slot=SLOT_SIGNAL)
    g.wire("dts", "convert", "latch", "release.trigger", slot=SLOT_TRIGGER)
    g.wire("latch", "release", "sink", "consume.value")
    world = make_world(g)
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 3),  # 3 > 5 = LOW
        Injection("latch", "release.data", SLOT_DATA, Kind.DATA, "D"),
    ])
    assert node_state(world, "sink")["last"] is None  # LOW → 静态回退默认 → 产出 None
    world.run([
        Injection("dts", "convert.data", SLOT_DATA, Kind.DATA, 7),  # 7 > 5 = HIGH
        Injection("latch", "release.data", SLOT_DATA, Kind.DATA, "D2"),
    ])
    assert node_state(world, "sink")["last"] == "D2"  # HIGH → 动态数据放行


def test_signal_to_data_static_controlled_default():
    """SignalToData(静态 x = 端口配置默认值):每次 pass 触发都放行默认值。

    LOW 不阻塞执行——静态数据回退默认(graph-group-protocol.md 裁定 15)。
    """
    g = GraphDefinition("stod")
    g.add_node("gate", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    g.add_node("stod", "SignalToData", config={"ports": {"pass_value.x": "FACT"}})
    g.add_node("sink", "Sink")
    g.wire("gate", "convert", "stod", "pass_value.gate", slot=SLOT_SIGNAL)
    g.wire("gate", "convert", "stod", "pass_value.pass", slot=SLOT_TRIGGER)
    g.wire("stod", "pass_value", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 0)])  # LOW
    assert node_state(world, "sink")["last"] == "FACT"  # 静态值不受电平影响
    world.run([Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 1)])  # HIGH
    assert node_state(world, "sink")["last"] == "FACT"


def test_signal_to_data_dynamic_controlled_data():
    """SignalToData(动态 x = 受控数据流):HIGH 期间触发才放行缓存数据。

    LOW 期间到达的数据照常缓存(LOW 不拒数据、不清缓存)——电平恢复 HIGH 后
    与新触发配对放行(事件解释矩阵新语义)。
    """
    g = GraphDefinition("stod2")
    g.add_node("gate", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    g.add_node("stod", "SignalToData")
    g.add_node("sink", "Sink")
    g.wire("gate", "convert", "stod", "pass_value.gate", slot=SLOT_SIGNAL)
    g.wire("gate", "convert", "stod", "pass_value.pass", slot=SLOT_TRIGGER)
    g.wire("stod", "pass_value", "sink", "consume.value")
    world = make_world(g)
    world.run([Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 0)])  # LOW
    world.run([Injection("stod", "pass_value.x", SLOT_DATA, Kind.DATA, "cached")])  # LOW 期间到达:缓存
    assert node_state(world, "sink")["last"] is None  # 无新触发 → 不产出
    world.run([Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 1)])  # HIGH + 触发
    assert node_state(world, "sink")["last"] == "cached"  # 缓存数据与新触发配对放行
    world.run([
        Injection("stod", "pass_value.x", SLOT_DATA, Kind.DATA, "payload"),
        Injection("gate", "convert.data", SLOT_DATA, Kind.DATA, 2),  # HIGH + 新触发
    ])
    assert node_state(world, "sink")["last"] == "payload"


def test_validation_chain_end_to_end():
    """组合链:Source→Buffer→Join→Split→Latch→DataToSignal→SignalToData→Sink 全链路。"""
    g = GraphDefinition("chain")
    g.add_node("src", "Source")
    g.add_node("const", "Constant", config={"groups": {"tick": {"value": 10}}})
    g.add_node("buf", "Buffer")
    g.add_node("join", "Join")
    g.add_node("split", "Split")
    g.add_node("latch", "Latch")
    g.add_node("probe", "Probe")
    g.add_node("dts", "DataToSignal", config={"groups": {"convert": {"mode": "truthy"}}})
    g.add_node("stod", "SignalToData", config={"ports": {"pass_value.x": "RELEASED"}})
    g.add_node("sink", "Sink")
    g.wire("src", "tick", "buf", "put.item")
    g.wire("buf", "flush", "join", "join.a")
    g.wire("const", "tick", "join", "join.b")
    g.wire("join", "join", "split", "fan.value")
    g.wire("split", "fan.out1", "latch", "release.data")
    g.wire("split", "fan.out2", "probe", "observe.value")
    g.wire("latch", "release", "dts", "convert.data")
    g.wire("dts", "convert", "stod", "pass_value.gate", slot=SLOT_SIGNAL)
    g.wire("dts", "convert", "stod", "pass_value.pass", slot=SLOT_TRIGGER)
    g.wire("stod", "pass_value", "sink", "consume.value")
    world = make_world(g)

    world.run([
        Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True),
        Injection("const", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True),
    ])
    world.run([Injection("src", "tick.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "flush") == []

    world.run([Injection("buf", "flush.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    # join 同步 → split 扇出 → latch 缓存 / probe 记录;latch 无 trigger 不输出
    assert node_state(world, "probe")["log"] == [([0, 1], 10)]
    assert node_state(world, "sink")["last"] is None

    world.run([Injection("latch", "release.trigger", SLOT_TRIGGER, Kind.SIGNAL, True)])
    # latch 释放 → dts 电平 HIGH → stod 受控放行 → sink 吸收
    assert node_state(world, "sink")["last"] == "RELEASED"
