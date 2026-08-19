"""确定性：同一图、同一输入序列 → 同一结果。

依据：graph-execution-model.md §5：顺序无关性由 Readiness 保证；
单线程队列 + turn 内固定代码序，同输入序列结果唯一。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL, SLOT_TRIGGER

from conftest import make_world, node_state


def build_chain() -> GraphDefinition:
    g = GraphDefinition("det")
    g.add_node("src", "Source")
    g.add_node("const", "Constant", value=10)
    g.add_node("buf", "Buffer")
    g.add_node("join", "Join")
    g.add_node("split", "Split")
    g.add_node("latch", "Latch")
    g.add_node("dts", "DataToSignal", mode="truthy")
    g.add_node("stod", "SignalToData", x="R")
    g.add_node("sink", "Sink")
    g.wire("src", "out", "buf", "put")
    g.wire("buf", "out", "join", "a")
    g.wire("const", "out", "join", "b")
    g.wire("join", "out", "split", "in")
    g.wire("split", "out1", "latch", "data")
    g.wire("split", "out2", "stod", "x")
    g.wire("latch", "out", "dts", "data")
    g.wire("dts", "level", "stod", "x", slot=SLOT_QUAL)
    g.wire("dts", "level", "stod", "pass", slot=SLOT_TRIGGER)
    g.wire("stod", "out", "sink", "in")
    return g


INJECTIONS = [
    [],  # epoch 1
    [],  # epoch 2
    [Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)],
    [Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True)],
]


def run_sequence():
    world = make_world(build_chain)
    for injs in INJECTIONS:
        world.run(injs)
    return world


def snapshot(world) -> tuple:
    """可比较的完整结果：时间线条目 + 事件档案 + 节点状态。"""
    entries = tuple(
        (e.run, e.seq, e.kind, e.event_id, e.payload, e.src_node, e.src_port, e.dst_node, e.dst_port, e.dst_slot, e.group, e.consumed, e.produced, e.message)
        for e in world.timeline.entries
    )
    events = tuple(
        (e.id, e.run, e.kind.value, e.payload, e.producer, e.port, tuple((d.node, d.port, d.slot, d.seq) for d in e.deliveries), tuple(e.consumed_by))
        for e in world.timeline.events.values()
    )
    state = {nid: (v["state"], {k: p for k, p in v["data_in"].items()}) for nid, v in world.observable_state().items()}
    return entries, events, state


def test_same_input_same_result():
    """同一图、同一输入序列 → 时间线/事件档案/状态逐项相等。"""
    a = snapshot(run_sequence())
    b = snapshot(run_sequence())
    assert a == b


def test_same_input_same_sequence_across_instances():
    """两个独立实例运行同一输入序列：结果一致（确定性可复现）。"""
    w1 = run_sequence()
    w2 = run_sequence()
    assert snapshot(w1) == snapshot(w2)
    assert node_state(w1, "sink") == node_state(w2, "sink")
    assert node_state(w1, "src") == node_state(w2, "src")


def test_repeated_epochs_advance_deterministically():
    """重复运行追加 epoch：前序结果不受影响（因果序传播）。"""
    w1 = run_sequence()
    before = snapshot(w1)
    w1.run()  # 追加一个 epoch
    after = snapshot(w1)
    # 追加 epoch 只增加新条目，不改变已有条目
    assert after[0][: len(before[0])] == before[0]
    assert len(after[0]) > len(before[0])
