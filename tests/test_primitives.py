"""每个验证原语的 Given/When/Then 行为锁定 + 组合链。

依据：《ChatGPT-架构验证性重写-20260819-1140.md》原语清单。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_SIGNAL, SLOT_TRIGGER

from conftest import deliveries, fired, make_world, node_state


def test_source_counts_and_emits_each_epoch():
    """Given Source(step=2) → Sink. When 运行三个 epoch. Then sink 依次收到 0, 2, 4。"""
    g = GraphDefinition("src")
    g.add_node("src", "Source", step=2)
    g.add_node("sink", "Sink")
    g.wire("src", "out", "sink", "in")
    world = make_world(g)
    world.run()
    world.run()
    world.run()
    assert node_state(world, "src")["count"] == 6
    assert node_state(world, "sink")["last"] == 4
    assert len([f for f in fired(world) if f == ("src", "step")]) == 3


def test_constant_emits_configured_value():
    """Given Constant(value=42). When 运行. Then 产出 42，无状态。"""
    g = GraphDefinition("const")
    g.add_node("c", "Constant", value=42)
    g.add_node("sink", "Sink")
    g.wire("c", "out", "sink", "in")
    world = make_world(g)
    world.run()
    world.run()
    assert deliveries(world, "c", "out") == [42, 42]
    assert node_state(world, "c") == {}


def test_sink_absorbs_without_output():
    """Given Sink. When 注入数据. Then last 更新、无任何产出事件。"""
    g = GraphDefinition("sink")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "a")])
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "b")])
    assert node_state(world, "sink")["last"] == "b"
    assert deliveries(world, "sink", "out") == []
    assert len(world.timeline.events) == 2  # 只有注入的两个事件


def test_probe_accumulates_log():
    """Given Probe. When 三个值到达. Then log 累积，无输出（显式状态可观察点）。"""
    g = GraphDefinition("probe")
    g.add_node("probe", "Probe")
    world = make_world(g)
    world.run([Injection("probe", "in", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("probe", "in", SLOT_DATA, Kind.DATA, 2)])
    world.run([Injection("probe", "in", SLOT_DATA, Kind.DATA, 3)])
    assert node_state(world, "probe")["log"] == [1, 2, 3]


def test_buffer_put_accumulates_flush_releases():
    """Buffer 能否表达"数据暂存但不产生执行事件"：put 只累积不产出；flush 触发才取出全部。"""
    g = GraphDefinition("buf")
    g.add_node("buf", "Buffer")
    g.add_node("sink", "Sink")
    g.wire("buf", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("buf", "put", SLOT_DATA, Kind.DATA, 1)])
    world.run([Injection("buf", "put", SLOT_DATA, Kind.DATA, 2)])
    assert deliveries(world, "buf", "out") == []  # 数据暂存但不产生执行事件
    world.run([Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "out") == [[1, 2]]
    assert node_state(world, "buf")["items"] == []
    # 空缓冲 flush：无事实发生，不产出
    world.run([Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "buf", "out") == [[1, 2]]


def test_join_syncs_multiple_inputs():
    """Join 明确表达多个输入之间的同步关系：a、b 齐备才执行，产出 tuple。"""
    g = GraphDefinition("join")
    g.add_node("in_a", "Split")
    g.add_node("in_b", "Split")
    g.add_node("join", "Join")
    g.add_node("sink", "Sink")
    g.wire("in_a", "out1", "join", "a")
    g.wire("in_b", "out1", "join", "b")
    g.wire("join", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("in_a", "in", SLOT_DATA, Kind.DATA, 1)])
    assert ("join", "sync") not in fired(world)
    world.run([Injection("in_b", "in", SLOT_DATA, Kind.DATA, 2)])
    assert ("join", "sync") in fired(world)
    assert node_state(world, "sink")["last"] == (1, 2)


def test_split_emits_two_independent_events():
    """Split 验证多输出发射：一次执行产出两个独立事件。"""
    g = GraphDefinition("split")
    g.add_node("split", "Split")
    g.add_node("s1", "Sink")
    g.add_node("s2", "Sink")
    g.wire("split", "out1", "s1", "in")
    g.wire("split", "out2", "s2", "in")
    world = make_world(g)
    world.run([Injection("split", "in", SLOT_DATA, Kind.DATA, "x")])
    assert node_state(world, "s1")["last"] == "x"
    assert node_state(world, "s2")["last"] == "x"
    assert len(world.timeline.events) == 3


def test_data_to_signal_modes():
    """DataToSignal：数据 → 信号显式转换（控制流构造），各模式算电平。"""
    g = GraphDefinition("dts")
    g.add_node("dts", "DataToSignal", mode="gt", threshold=5)
    g.add_node("latch", "Latch")
    g.add_node("sink", "Sink")
    g.wire("dts", "level", "latch", "data", slot=SLOT_QUAL)
    g.wire("dts", "level", "latch", "release", slot=SLOT_TRIGGER)
    g.wire("latch", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("dts", "data", SLOT_DATA, Kind.DATA, 3), Injection("latch", "data", SLOT_DATA, Kind.DATA, "D")])
    assert node_state(world, "sink")["last"] is None  # 3 > 5 = LOW → 不执行不产出
    world.run([Injection("dts", "data", SLOT_DATA, Kind.DATA, 7), Injection("latch", "data", SLOT_DATA, Kind.DATA, "D2")])
    assert node_state(world, "sink")["last"] == "D2"  # 7 > 5 = HIGH → 资格 + 激活 → 放行


def test_signal_to_data_static_controlled_default():
    """SignalToData(静态 x = 受控默认参数)：信号 HIGH → 放行静态值；LOW → 不产出。"""
    g = GraphDefinition("stod")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("stod", "SignalToData", x="FACT")
    g.add_node("sink", "Sink")
    g.wire("gate", "level", "stod", "x", slot=SLOT_QUAL)
    g.wire("gate", "level", "stod", "pass", slot=SLOT_TRIGGER)
    g.wire("stod", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 0)])  # LOW
    assert node_state(world, "sink")["last"] is None
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 1)])  # HIGH
    assert node_state(world, "sink")["last"] == "FACT"  # 信号→数据：受控输入放行


def test_signal_to_data_dynamic_controlled_data():
    """SignalToData(动态 x = 受控数据流)：HIGH 期间到达的数据才放行。

    LOW 期间到达的数据照常缓存，但其 pending 即刻消费（LOW 不产生有效组合，
    文档 §4 序列推导）——不与后续 HIGH 配对；HIGH 期间到达的数据正常放行。
    """
    g = GraphDefinition("stod2")
    g.add_node("gate", "DataToSignal", mode="truthy")
    g.add_node("stod", "SignalToData")
    g.add_node("sink", "Sink")
    g.wire("gate", "level", "stod", "x", slot=SLOT_QUAL)
    g.wire("gate", "level", "stod", "pass", slot=SLOT_TRIGGER)
    g.wire("stod", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 0)])  # 资格 LOW
    world.run([Injection("stod", "x", SLOT_DATA, Kind.DATA, "dropped")])  # LOW 期间到达：缓存但不成对
    assert node_state(world, "sink")["last"] is None
    world.run([Injection("gate", "data", SLOT_DATA, Kind.DATA, 1)])  # HIGH：无 pending 数据 → 不产出
    assert node_state(world, "sink")["last"] is None  # LOW 期间的数据不与后续 HIGH 配对
    world.run([Injection("stod", "x", SLOT_DATA, Kind.DATA, "payload")])  # HIGH 期间到达 → 放行
    assert node_state(world, "sink")["last"] == "payload"


def test_validation_chain_end_to_end():
    """组合链：Source→Buffer→Join→Split→Latch→DataToSignal→SignalToData→Sink 全链路。"""
    g = GraphDefinition("chain")
    g.add_node("src", "Source")
    g.add_node("const", "Constant", value=10)
    g.add_node("buf", "Buffer")
    g.add_node("join", "Join")
    g.add_node("split", "Split")
    g.add_node("latch", "Latch")
    g.add_node("probe", "Probe")
    g.add_node("dts", "DataToSignal", mode="truthy")
    g.add_node("stod", "SignalToData", x="RELEASED")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "buf", "put")
    g.wire("buf", "out", "join", "a")
    g.wire("const", "out", "join", "b")
    g.wire("join", "out", "split", "in")
    g.wire("split", "out1", "latch", "data")
    g.wire("split", "out2", "probe", "in")
    g.wire("latch", "out", "dts", "data")
    g.wire("dts", "level", "stod", "x", slot=SLOT_QUAL)
    g.wire("dts", "level", "stod", "pass", slot=SLOT_TRIGGER)
    g.wire("stod", "out", "sink", "in")
    world = make_world(g)

    world.run()  # epoch 1: src→buf 累积; const→join.b
    world.run()  # epoch 2: 累积继续
    assert deliveries(world, "buf", "out") == []

    world.run([Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)])
    # join 同步 → split 扇出 → latch 缓存 / probe 记录；latch 无 release 不输出
    assert node_state(world, "probe")["log"] == [([0, 1], 10)]
    assert node_state(world, "sink")["last"] is None

    world.run([Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True)])
    # latch 释放 → dts 电平 HIGH → stod 受控放行 → sink 吸收
    assert node_state(world, "sink")["last"] == "RELEASED"
