"""Readiness 语义：D1/S1 事件配对核心案例与触发策略。

依据：graph-port-capability-composition.md §4（逐行推导锁死）
执行条件 = Data.pending AND Qual.pending AND level==HIGH；执行后消费双 pending。
数据/信号到达顺序不进入可观察语义（序列 A 与序列 B 结果一致：D1, D3）。
"""

from eidolon_graph_ref.engine.event import Injection, Kind
from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_DATA, SLOT_QUAL

from conftest import build_pairing_graph, data_port, deliveries, fired, make_world, node_state, pairing_types


def build_pairing():
    return build_pairing_graph()


PAIRING_TYPES = pairing_types()


def d(world, value):
    """数据到达（经数据源入口注入）。"""
    world.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, value)])


def s(world, level):
    """信号到达（经信号源入口注入电平数据）。"""
    world.run([Injection("in_s", "data", SLOT_DATA, Kind.DATA, level)])


def test_pairing_sequence_a():
    """序列 A：D1 S1 D2 S0 D3 S1 D4 S0 → 输出 D1, D3（文档 §4 逐行推导）。"""
    world = make_world(build_pairing, PAIRING_TYPES)
    d(world, "D1")
    assert deliveries(world, "p", "out") == []  # Data 无 pending 资格 → 不执行
    s(world, True)
    assert deliveries(world, "p", "out") == ["D1"]  # 条件全满足 → 输出 D1，消费双 pending
    d(world, "D2")
    assert deliveries(world, "p", "out") == ["D1"]  # Signal 无 pending → 不执行
    s(world, False)
    assert deliveries(world, "p", "out") == ["D1"]  # LOW 不产生有效组合
    assert data_port(world, "p", "data")["qual"]["pending"] is False  # S0 自身消费为控制状态更新
    assert data_port(world, "p", "data")["pending"] is True  # D2 照常缓存，等待
    d(world, "D3")
    s(world, True)
    assert deliveries(world, "p", "out") == ["D1", "D3"]
    d(world, "D4")
    s(world, False)
    assert deliveries(world, "p", "out") == ["D1", "D3"]


def test_pairing_sequence_b():
    """序列 B：S1 D1 S0 D2 S1 D3 S0 D4 → 输出同样为 D1, D3（顺序无关）。"""
    world = make_world(build_pairing, PAIRING_TYPES)
    s(world, True)
    d(world, "D1")
    s(world, False)
    d(world, "D2")
    s(world, True)
    d(world, "D3")
    s(world, False)
    d(world, "D4")
    assert deliveries(world, "p", "out") == ["D1", "D3"]


def test_same_level_repetition_is_two_qualifications():
    """S1 → S1 同电平重复是两次独立资格（level 与 occurrence 独立）。

    S1 D S1 D：第二次 S1 重新授权，D 再次输出——不是电平变化才有效。
    """
    world = make_world(build_pairing, PAIRING_TYPES)
    s(world, True)
    d(world, "D")
    s(world, True)
    d(world, "D")
    assert deliveries(world, "p", "out") == ["D", "D"]


def test_data_accumulation_then_signal():
    """D1 D2 D3 堆积后 S1：执行一次，输出最新缓存值 D3。"""
    world = make_world(build_pairing, PAIRING_TYPES)
    d(world, "D1")
    d(world, "D2")
    d(world, "D3")
    s(world, True)
    assert deliveries(world, "p", "out") == ["D3"]
    assert data_port(world, "p", "data")["pending"] is False  # 执行后消费 pending
    assert data_port(world, "p", "data")["value"] == "D3"  # 缓存值保持


def test_signal_low_does_not_erase_data():
    """不变量：Data Event 不因为 Signal LOW 而消失（§6.2）。

    D 到达时资格已 LOW：数据照常接收缓存（值保留）；pending 即刻消费——
    LOW 不产生有效组合，不与后续 HIGH 配对（§4 序列推导）。
    """
    world = make_world(build_pairing, PAIRING_TYPES)
    s(world, False)  # LOW 先到（自我消费）
    d(world, "D")
    assert data_port(world, "p", "data")["value"] == "D"  # 缓存值不消失
    assert data_port(world, "p", "data")["pending"] is False  # LOW 不产生有效组合
    assert deliveries(world, "p", "out") == []
    s(world, True)  # HIGH：无 pending 数据可配对 → 不执行
    assert deliveries(world, "p", "out") == []


# ==================================================================== 组策略
def test_policy_on_all_data_ready_join():
    """ON_ALL_DATA_READY：Join 全部动态输入 pending 才执行（graph-ports-bindings.md §2.4）。

    动态语义需要真实连线：上游入口节点经数据线驱动 join.a / join.b。
    """
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
    assert ("join", "sync") not in fired(world)  # 部分数据到达 → join 不执行
    world.run([Injection("in_b", "in", SLOT_DATA, Kind.DATA, 2)])
    assert ("join", "sync") in fired(world)
    assert node_state(world, "sink")["last"] == (1, 2)


def test_policy_on_any_data_sink():
    """ON_ANY_DATA：任一动态输入 pending 即执行（Sink 每收到数据执行一次）。"""
    from eidolon_graph_ref.model.graph import GraphDefinition

    g = GraphDefinition("sink")
    g.add_node("sink", "Sink")
    world = make_world(g)
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "x")])
    world.run([Injection("sink", "in", SLOT_DATA, Kind.DATA, "y")])
    assert node_state(world, "sink")["last"] == "y"
    assert len([f for f in fired(world) if f[0] == "sink"]) == 2


def test_policy_on_trigger_buffer_flush():
    """ON_TRIGGER：数据到达只累积（put），flush 触发才执行输出（Buffer）。"""
    from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_TRIGGER

    g = GraphDefinition("buffer")
    g.add_node("buf", "Buffer")
    g.add_node("sink", "Sink")
    g.wire("buf", "out", "sink", "in")
    world = make_world(g)
    world.run([Injection("buf", "put", SLOT_DATA, Kind.DATA, 1)])
    assert ("buf", "put") in fired(world)
    assert ("buf", "flush") not in fired(world)  # 数据到达不触发 flush
    assert deliveries(world, "buf", "out") == []
    world.run([Injection("buf", "flush", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert ("buf", "flush") in fired(world)
    assert deliveries(world, "buf", "out") == [[1]]
    assert node_state(world, "buf")["items"] == []  # 取出清空


def test_policy_on_data_and_trigger_latch():
    """ON_DATA_AND_TRIGGER：数据齐 pending + Trigger pending 才执行（Latch 受控释放）。

    数据与 release 的到达顺序不进入可观察语义（两种顺序都产出一次）。
    数据端口动态语义需要真实连线（上游入口节点驱动）。
    """
    from eidolon_graph_ref.model.graph import GraphDefinition, SLOT_TRIGGER

    def fresh():
        g = GraphDefinition("latch")
        g.add_node("in_d", "Split")
        g.add_node("latch", "Latch")
        g.add_node("sink", "Sink")
        g.wire("in_d", "out1", "latch", "data")
        g.wire("latch", "out", "sink", "in")
        return make_world(g)

    # 只有数据：缓存，不输出
    world = fresh()
    world.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, 42)])
    assert deliveries(world, "latch", "out") == []

    # 只有 release：无数据配对，不输出（"数据齐 pending" 不满足）
    world2 = fresh()
    world2.run([Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world2, "latch", "out") == []

    # 数据 → release：执行，产出缓存值
    world.run([Injection("latch", "release", SLOT_TRIGGER, Kind.SIGNAL, True)])
    assert deliveries(world, "latch", "out") == [42]
    assert node_state(world, "sink")["last"] == 42

    # release → 数据（pending 跨 epoch 保持）：同样执行——等待是条件未满足，不是时间
    world2.run([Injection("in_d", "in", SLOT_DATA, Kind.DATA, 43)])
    assert deliveries(world2, "latch", "out") == [43]
    assert node_state(world2, "sink")["last"] == 43
